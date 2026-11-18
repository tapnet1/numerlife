import datetime as dt

from test_app import numer, token_from
from test_calendars import CalendarTestCase


class JournalTest(CalendarTestCase):
    def setUp(self):
        super().setUp()
        lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        self.start(lead["id"], kind="numerology", lang="es")
        self.lead = lead
        self.cal = self.calendar()
        self.today = numer.calendar_journal.engine.local_today()
        self.key = self.today.strftime("%Y-%m")

    def entries(self):
        with numer.open_db() as connection:
            return connection.execute("SELECT * FROM feedback_entries ORDER BY id").fetchall()

    def write(self, ip="10.9.0.1", **fields):
        visitor = numer.app.test_client()
        page = visitor.get(f"/c/{self.cal['token']}/{self.key}", environ_base={"REMOTE_ADDR": ip})
        data = {"csrf_token": token_from(page.get_data(as_text=True)), "day": self.today.isoformat(),
                "month": self.key, "body": "Me sentí con mucha energía.", "feeling": "", "tz": "America/Toronto",
                "website": ""}
        data.update(fields)
        return visitor, visitor.post(f"/c/{self.cal['token']}/notes", data=data, environ_base={"REMOTE_ADDR": ip})

    def test_every_day_has_a_note_form(self):
        page = numer.app.test_client().get(f"/c/{self.cal['token']}/{self.key}").get_data(as_text=True)
        self.assertIn("Añadir una nota", page)
        self.assertIn('name="day" value="' + self.today.isoformat(), page)
        self.assertIn("js/journal.js", page)

    def test_several_notes_a_day_with_time_and_alert(self):
        visitor, response = self.write()
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"#d{self.today.day}", response.headers["Location"])
        self.write(body="Segunda nota.", feeling="4")
        notes = self.entries()
        self.assertEqual(len(notes), 2)
        self.assertEqual((notes[0]["source"], notes[0]["tz"], notes[0]["entry_date"]),
                         ("calendar", "America/Toronto", self.today.isoformat()))
        page = visitor.get(f"/c/{self.cal['token']}/{self.key}").get_data(as_text=True)
        self.assertIn("Segunda nota.", page)
        self.assertIn("✎ 2", page)                        # marker on the month grid
        self.assertIn("Bien", page)
        self.assertIn(numer.calendar_journal.time_label(notes[0]), page)
        with numer.open_db() as connection:
            alerts = connection.execute("SELECT * FROM email_outbox WHERE kind = 'feedback_alert'").fetchall()
        self.assertEqual(len(alerts), 2)
        self.assertIn("Journal note from", alerts[0]["subject"])
        self.assertIn("calendar journal", alerts[0]["body_text"])
        lead_page = self.client.get(f"/leads/{self.lead['id']}").get_data(as_text=True)
        self.assertIn("Calendar journal", lead_page)
        self.assertIn("Segunda nota.", lead_page)

    def test_any_day_but_not_far_away_and_not_empty(self):
        earlier = (self.today - dt.timedelta(days=20)).isoformat()
        self.write(day=earlier)
        self.assertEqual(self.entries()[-1]["entry_date"], earlier)
        self.write(day=(self.today + dt.timedelta(days=900)).isoformat())
        self.write(body="", feeling="")
        self.write(day="not-a-date")
        self.assertEqual(len(self.entries()), 1)

    def test_bad_time_zone_falls_back(self):
        self.write(tz="Mars/Olympus")
        self.assertIsNone(self.entries()[0]["tz"])

    def test_honeypot_and_delete(self):
        self.write(website="spam")
        self.assertEqual(self.entries(), [])
        visitor, _ = self.write()
        note = self.entries()[0]
        page = visitor.get(f"/c/{self.cal['token']}/{self.key}").get_data(as_text=True)
        visitor.post(f"/c/{self.cal['token']}/notes/{note['id']}/delete", data={"csrf_token": token_from(page)})
        self.assertEqual(self.entries(), [])

    def test_cannot_delete_another_clients_note(self):
        self.write()
        note = self.entries()[0]
        other = self.lead_via_liliana()
        self.start(other["id"], kind="numerology", lang="es")
        with numer.open_db() as connection:
            other_cal = connection.execute("SELECT * FROM calendars WHERE lead_id = ?", (other["id"],)).fetchone()
        visitor = numer.app.test_client()
        page = visitor.get(f"/c/{other_cal['token']}").get_data(as_text=True)
        response = visitor.post(f"/c/{other_cal['token']}/notes/{note['id']}/delete",
                                data={"csrf_token": token_from(page)})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(self.entries()), 1)

    def test_daily_email_links_to_the_journal(self):
        with numer.app.test_request_context("/", base_url=numer.BASE_URL), numer.open_db() as connection:
            lead = connection.execute("SELECT * FROM leads WHERE id = ?", (self.lead["id"],)).fetchone()
            subject, text, html, _ = numer.calendar_mail._build(self.cal, lead, self.today.year, self.today.month,
                                                                "daily", self.today)
        self.assertIn("Escribir en tu diario", html)
        self.assertIn(f"#d{self.today.day}", text)
