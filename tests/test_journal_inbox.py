from test_app import PASSWORD, numer, token_from
from test_calendar_journal import JournalTest


class JournalInboxTest(JournalTest):
    def test_inbox_lists_clients_and_shows_notes_by_date(self):
        visitor, _ = self.write(body="Primera nota")
        self.write(body="Segunda nota", day=(self.today.replace(day=1)).isoformat())
        page = self.client.get("/journal").get_data(as_text=True)
        self.assertIn(self.lead["full_name"], page)
        self.assertIn("2 new", page)
        self.assertIn('Journal <span class="badge">2</span>', page)        # menu badge
        thread = self.client.get(f"/journal?client={self.lead['id']}").get_data(as_text=True)
        self.assertIn("Primera nota", thread)
        self.assertIn("Segunda nota", thread)
        self.assertLess(thread.index("Segunda nota") if self.today.day != 1 else 0,
                        thread.index("Primera nota") + 1)                    # oldest first
        one_day = self.client.get(f"/journal?client={self.lead['id']}&date={self.today.isoformat()}").get_data(as_text=True)
        self.assertIn("Primera nota", one_day)
        if self.today.day != 1:
            self.assertNotIn("Segunda nota", one_day)
        self.assertNotIn("2 new", self.client.get("/journal").get_data(as_text=True))   # marked read

    def test_search_and_reply_from_the_inbox(self):
        self.write(body="Hola")
        self.assertNotIn(self.lead["full_name"], self.client.get("/journal?q=zzzz").get_data(as_text=True))
        first = self.lead["full_name"].split()[0]
        self.assertIn(self.lead["full_name"], self.client.get(f"/journal?q={first}").get_data(as_text=True))
        note = self.entries()[0]
        page = self.client.get(f"/journal?client={self.lead['id']}").get_data(as_text=True)
        response = self.client.post(f"/leads/{self.lead['id']}/journal/{note['id']}/reply", data={
            "csrf_token": token_from(page), "body": "Gracias por escribir",
            "back": f"/journal?client={self.lead['id']}#note-{note['id']}"})
        self.assertIn(f"/journal?client={self.lead['id']}", response.headers["Location"])
        self.assertIn("Gracias por escribir", self.client.get(f"/journal?client={self.lead['id']}").get_data(as_text=True))

    def test_other_practitioners_see_nothing(self):
        self.write(body="Secreto")
        numer.create_user("other@example.com", PASSWORD, "practitioner", "Other")
        other = numer.app.test_client()
        page = other.get("/login").get_data(as_text=True)
        other.post("/login", data={"csrf_token": token_from(page), "email": "other@example.com", "password": PASSWORD})
        self.assertNotIn("Secreto", other.get("/journal").get_data(as_text=True))
        self.assertEqual(other.get(f"/journal?client={self.lead['id']}").status_code, 404)

    def test_push_alert_opens_the_inbox(self):
        self.write(body="Hola")
        with numer.open_db() as connection:
            push = connection.execute("SELECT payload FROM push_outbox ORDER BY id DESC").fetchone()
        self.assertIn(f"/journal?client={self.lead['id']}", push["payload"])


class JournalSharingTest(JournalTest):
    def test_client_can_keep_the_journal_private(self):
        visitor, _ = self.write(body="Visible")
        page = visitor.get(f"/c/{self.cal['token']}/{self.key}").get_data(as_text=True)
        self.assertIn("Your journal is shared with", page.replace("Tu diario se comparte con", "Your journal is shared with"))
        visitor.post(f"/c/{self.cal['token']}/journal-sharing", data={"csrf_token": token_from(page), "share": "no"})
        self.assertEqual(self.calendar()["journal_shared"], 0)
        with numer.open_db() as connection:
            alerts_before = connection.execute("SELECT COUNT(*) FROM email_outbox WHERE kind = 'feedback_alert'").fetchone()[0]
        self.write(body="Privado")
        with numer.open_db() as connection:
            alerts_after = connection.execute("SELECT COUNT(*) FROM email_outbox WHERE kind = 'feedback_alert'").fetchone()[0]
        self.assertEqual(alerts_before, alerts_after)                       # no alert while private
        lead_page = self.client.get(f"/leads/{self.lead['id']}").get_data(as_text=True)
        self.assertNotIn("Privado", lead_page)
        self.assertNotIn("Visible", lead_page)
        self.assertNotIn("Privado", self.client.get(f"/journal?client={self.lead['id']}").get_data(as_text=True))
        page = visitor.get(f"/c/{self.cal['token']}/{self.key}").get_data(as_text=True)
        self.assertIn("Privado", page)                                       # the client still sees it
        self.assertNotIn("/reply", page)
        visitor.post(f"/c/{self.cal['token']}/journal-sharing", data={"csrf_token": token_from(page), "share": "yes"})
        self.assertIn("Privado", self.client.get(f"/journal?client={self.lead['id']}").get_data(as_text=True))
