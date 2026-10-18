import csv
import io
import json
import os
import sqlite3
import unittest

from test_app import PASSWORD, numer, token_from
from test_intake import IntakeTestCase


class WorkspaceTestCase(IntakeTestCase):
    def setUp(self):
        super().setUp()
        self.other_id = numer.create_user("maria@example.com", PASSWORD, "practitioner", "Maria", slug="maria")
        self.mine = self.add_lead(self.practitioner_id, "Ana Lopez", email="ana@example.com", phone="+1 780 555 0101")
        self.mine_started = self.add_lead(self.practitioner_id, "Ben Cole", email=None)
        self.theirs = self.add_lead(self.other_id, "Carla Ruiz", email="carla@example.com")
        self.orphan = self.add_lead(None, "Dan Wu", email="dan@example.com")

    def add_lead(self, practitioner_id, name, email=None, phone=""):
        numbers = json.dumps({"life_path": 5, "expression": 8, "soul_urge": 6, "personality": 11, "birthday": 6})
        with numer.open_db() as connection:
            cursor = connection.execute(
                "INSERT INTO leads (practitioner_id, token, full_name, birth_date, birth_city, birth_region,"
                " birth_country, numbers, email, phone, consent_report, status, created_at, captured_at)"
                " VALUES (?, ?, ?, '1990-07-15', 'Edmonton', 'Alberta', 'Canada', ?, ?, ?, ?, ?, ?, ?)",
                (practitioner_id, os.urandom(12).hex(), name, numbers, email, phone, 1 if email else 0,
                 "captured" if email else "started", numer.iso(numer.now_utc()),
                 numer.iso(numer.now_utc()) if email else None),
            )
            return cursor.lastrowid

    def csrf(self, path):
        return token_from(self.client.get(path).get_data(as_text=True))


class ScopingTest(WorkspaceTestCase):
    def test_practitioner_sees_only_their_leads(self):
        self.login("liliana@example.com")
        html = self.client.get("/leads").get_data(as_text=True)
        self.assertIn("Ana Lopez", html)
        self.assertIn("Ben Cole", html)
        self.assertNotIn("Carla Ruiz", html)
        self.assertNotIn("Dan Wu", html)

    def test_other_practitioners_lead_is_404(self):
        self.login("liliana@example.com")
        self.assertEqual(self.client.get(f"/leads/{self.theirs}").status_code, 404)
        self.assertEqual(self.client.get(f"/leads/{self.orphan}").status_code, 404)

    def test_cannot_update_another_practitioners_lead(self):
        self.login("liliana@example.com")
        token = self.csrf("/leads")
        response = self.client.post(f"/leads/{self.theirs}", data={"csrf_token": token, "stage": "client", "notes": "x"})
        self.assertEqual(response.status_code, 404)
        with numer.open_db() as connection:
            row = connection.execute("SELECT stage, notes FROM leads WHERE id = ?", (self.theirs,)).fetchone()
        self.assertEqual((row["stage"], row["notes"]), ("new", ""))

    def test_admin_sees_everything_with_practitioner_names(self):
        self.login("owner@example.com")
        html = self.client.get("/leads").get_data(as_text=True)
        for name in ("Ana Lopez", "Carla Ruiz", "Dan Wu"):
            self.assertIn(name, html)
        self.assertIn("No practitioner", html)
        self.assertEqual(self.client.get(f"/leads/{self.theirs}").status_code, 200)

    def test_export_is_scoped(self):
        self.login("liliana@example.com")
        body = self.client.get("/leads/export.csv").get_data(as_text=True)
        names = {row[1] for row in csv.reader(io.StringIO(body))}
        self.assertEqual(names, {"Name", "Ana Lopez", "Ben Cole"})

    def test_workspace_requires_sign_in(self):
        for path in ("/dashboard", "/leads", f"/leads/{self.mine}", "/leads/export.csv"):
            self.assertEqual(self.client.get(path).status_code, 302, path)


class LeadsTest(WorkspaceTestCase):
    def test_detail_shows_contact_birth_and_numbers_and_marks_viewed(self):
        self.login("liliana@example.com")
        html = self.client.get(f"/leads/{self.mine}").get_data(as_text=True)
        for text in ("ana@example.com", "+1 780 555 0101", "1990-07-15", "Edmonton, Alberta, Canada",
                     "Life Path", "The Adventurer", "Soul Urge"):
            self.assertIn(text, html)
        with numer.open_db() as connection:
            viewed = connection.execute("SELECT viewed_at FROM leads WHERE id = ?", (self.mine,)).fetchone()[0]
        self.assertIsNotNone(viewed)

    def test_started_lead_explains_missing_contact(self):
        self.login("liliana@example.com")
        html = self.client.get(f"/leads/{self.mine_started}").get_data(as_text=True)
        self.assertIn("didn't unlock the full report", html)

    def test_stage_and_notes_are_saved(self):
        self.login("liliana@example.com")
        token = self.csrf(f"/leads/{self.mine}")
        response = self.client.post(f"/leads/{self.mine}", data={
            "csrf_token": token, "stage": "contacted", "notes": "Called Tuesday.\r\nBook a reading."})
        self.assertEqual(response.status_code, 302)
        with numer.open_db() as connection:
            row = connection.execute("SELECT stage, notes FROM leads WHERE id = ?", (self.mine,)).fetchone()
        self.assertEqual((row["stage"], row["notes"]), ("contacted", "Called Tuesday.\nBook a reading."))

    def test_unknown_stage_is_rejected(self):
        self.login("liliana@example.com")
        token = self.csrf(f"/leads/{self.mine}")
        response = self.client.post(f"/leads/{self.mine}", data={"csrf_token": token, "stage": "vip"})
        self.assertEqual(response.status_code, 400)

    def test_search_and_filters(self):
        self.login("liliana@example.com")
        html = self.client.get("/leads?q=ana@").get_data(as_text=True)
        self.assertIn("Ana Lopez", html)
        self.assertNotIn("Ben Cole", html)
        html = self.client.get("/leads?unlocked=no").get_data(as_text=True)
        self.assertIn("Ben Cole", html)
        self.assertNotIn("Ana Lopez", html)

    def test_archived_hidden_by_default(self):
        with numer.open_db() as connection:
            connection.execute("UPDATE leads SET stage = 'archived' WHERE id = ?", (self.mine_started,))
        self.login("liliana@example.com")
        self.assertNotIn("Ben Cole", self.client.get("/leads").get_data(as_text=True))
        self.assertIn("Ben Cole", self.client.get("/leads?stage=archived").get_data(as_text=True))
        self.assertIn("Ben Cole", self.client.get("/leads?stage=all").get_data(as_text=True))

    def test_csv_neutralises_formulas(self):
        self.add_lead(self.practitioner_id, "=HYPERLINK(\"http://evil\")", email="x@example.com")
        self.login("liliana@example.com")
        body = self.client.get("/leads/export.csv").get_data(as_text=True)
        self.assertIn("'=HYPERLINK", body)
        self.assertNotIn(",=HYPERLINK", body)


class DashboardTest(WorkspaceTestCase):
    def test_stats_and_share_link(self):
        self.login("liliana@example.com")
        html = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn('<span class="stat-n">2</span><span class="stat-l">Total leads</span>', html)
        self.assertIn('<span class="stat-n">50%</span>', html)       # 1 of 2 unlocked
        self.assertIn("1 new lead", html)
        self.assertIn('data-share-url="http://localhost/liliana"', html)
        self.assertIn("Ana Lopez", html)
        self.assertNotIn("Carla Ruiz", html)

    def test_empty_dashboard_guides_the_practitioner(self):
        self.login("maria@example.com")
        with numer.open_db() as connection:
            connection.execute("DELETE FROM leads WHERE practitioner_id = ?", (self.other_id,))
        html = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("No leads yet.", html)


class LeadColumnsMigrationTest(unittest.TestCase):
    def test_existing_leads_table_gains_pipeline_columns(self):
        path = os.path.join(os.path.dirname(numer.DB), "old-leads.db")
        if os.path.exists(path):
            os.remove(path)
        numer.initialize(path)
        connection = sqlite3.connect(path)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(leads)")}
        finally:
            connection.close()
            os.remove(path)
        self.assertTrue({"stage", "notes", "viewed_at", "updated_at"} <= columns)



class BirthCorrectionTest(WorkspaceTestCase):
    def correct(self, lead_id, **fields):
        data = {"full_name": "Violetta Maria Stokowska", "birth_date": "1965-03-14", "birth_city": "Warszawa",
                "birth_region": "", "birth_country": "Poland"}
        data.update(fields)
        data["csrf_token"] = self.csrf(f"/leads/{lead_id}") if lead_id != self.theirs else self.csrf("/leads")
        return self.client.post(f"/leads/{lead_id}/birth", data=data)

    def test_practitioner_corrects_birth_details_and_numbers_are_recalculated(self):
        self.login("liliana@example.com")
        response = self.correct(self.mine)
        self.assertEqual(response.status_code, 302)
        with numer.open_db() as connection:
            lead = connection.execute("SELECT * FROM leads WHERE id = ?", (self.mine,)).fetchone()
        self.assertEqual((lead["full_name"], lead["birth_date"], lead["birth_region"], lead["birth_country"]),
                         ("Violetta Maria Stokowska", "1965-03-14", "", "Poland"))
        expected = numer_numbers("Violetta Maria Stokowska", "1965-03-14")
        self.assertEqual(json.loads(lead["numbers"]), expected)
        self.assertIn("Correct birth details", self.client.get(f"/leads/{self.mine}").get_data(as_text=True))

    def test_pending_emails_are_rebuilt_with_the_corrected_details(self):
        import emails
        emails.queue_unlock_emails(self.mine)
        with numer.open_db() as connection:
            connection.execute("INSERT INTO email_outbox (kind, to_addr, subject, body_text, body_html, lead_id,"
                               " created_at) VALUES ('calendar_daily', 'ana@example.com', 'old', 'old', 'old', ?, ?)",
                               (self.mine, numer.iso(numer.now_utc())))
        self.login("liliana@example.com")
        self.correct(self.mine)
        with numer.open_db() as connection:
            rows = connection.execute("SELECT kind, subject, body_text, sent_at, last_error FROM email_outbox"
                                      " WHERE lead_id = ?", (self.mine,)).fetchall()
        report = next(r for r in rows if r["kind"] == "report")
        self.assertIsNone(report["sent_at"])
        self.assertIn("Violetta", report["subject"])
        self.assertNotIn("Ana", report["body_text"])
        calendar = next(r for r in rows if r["kind"] == "calendar_daily")
        self.assertEqual(calendar["last_error"], "cancelled: birth details corrected")

    def test_cancelled_calendar_email_can_be_sent_again_today(self):
        now = numer.iso(numer.now_utc())
        with numer.open_db() as connection:
            cal = connection.execute(
                "INSERT INTO calendars (lead_id, practitioner_id, kind, token, created_at, email_freq, last_emailed)"
                " VALUES (?, ?, 'numerology', 'tok-cal', ?, 'daily', '2026-10-18')",
                (self.mine, self.practitioner_id, now)).lastrowid
            connection.execute("INSERT INTO email_outbox (kind, to_addr, subject, body_text, body_html, lead_id,"
                               " created_at) VALUES ('calendar_daily', 'ana@example.com', 'old', 'old', 'old', ?, ?)",
                               (self.mine, now))
        self.login("liliana@example.com")
        self.correct(self.mine)
        with numer.open_db() as connection:
            marker = connection.execute("SELECT last_emailed FROM calendars WHERE id = ?", (cal,)).fetchone()[0]
        self.assertIsNone(marker)

    def test_cancelled_welcome_for_a_daily_subscriber_clears_the_daily_marker(self):
        now = numer.iso(numer.now_utc())
        with numer.open_db() as connection:
            cal = connection.execute(
                "INSERT INTO calendars (lead_id, practitioner_id, kind, token, created_at, email_freq, last_emailed)"
                " VALUES (?, ?, 'numerology', 'tok-w', ?, 'daily', '2026-10-18')",
                (self.mine, self.practitioner_id, now)).lastrowid
            connection.execute("INSERT INTO email_outbox (kind, to_addr, subject, body_text, body_html, lead_id,"
                               " created_at) VALUES ('calendar_monthly', 'ana@example.com', 'w', 'w', 'w', ?, ?)",
                               (self.mine, now))
        self.login("liliana@example.com")
        self.correct(self.mine)
        with numer.open_db() as connection:
            marker = connection.execute("SELECT last_emailed FROM calendars WHERE id = ?", (cal,)).fetchone()[0]
        self.assertIsNone(marker)

    def test_queued_practitioner_digest_shows_the_corrected_name(self):
        import datetime as dt
        import emails
        earlier = numer.iso(numer.now_utc() - dt.timedelta(days=3))
        with numer.open_db() as connection:
            connection.execute("UPDATE leads SET captured_at = ? WHERE id = ?", (earlier, self.mine))
        emails.run_nudges(force=True)
        with numer.open_db() as connection:
            before = connection.execute("SELECT body_text FROM email_outbox WHERE kind = 'practitioner_nudge'"
                                        " AND user_id = ?", (self.practitioner_id,)).fetchone()
        self.assertIsNotNone(before)
        self.assertIn("Ana", before["body_text"])
        self.login("liliana@example.com")
        self.correct(self.mine)
        with numer.open_db() as connection:
            after = connection.execute("SELECT body_text, sent_at FROM email_outbox WHERE kind = 'practitioner_nudge'"
                                       " AND user_id = ?", (self.practitioner_id,)).fetchone()
        self.assertIsNone(after["sent_at"])
        self.assertIn("Violetta", after["body_text"])
        self.assertNotIn("Ana", after["body_text"])

    def test_queued_publish_notice_is_rerendered_not_lost(self):
        import calendar_mail
        now = numer.iso(numer.now_utc())
        with numer.open_db() as connection:
            cal = connection.execute(
                "INSERT INTO calendars (lead_id, practitioner_id, kind, token, created_at)"
                " VALUES (?, ?, 'magialogia', 'tok-mag', ?)", (self.mine, self.practitioner_id, now)).lastrowid
            connection.execute("INSERT INTO calendar_months (calendar_id, month, published_at) VALUES (?, '2026-10', ?)",
                               (cal, now))
        self.assertTrue(calendar_mail.queue_published(cal, "2026-10"))
        self.login("liliana@example.com")
        self.correct(self.mine)
        with numer.open_db() as connection:
            row = connection.execute("SELECT * FROM email_outbox WHERE kind = 'calendar_published'").fetchone()
        self.assertIsNone(row["sent_at"])
        self.assertIn("Violetta", row["body_text"])
        self.assertIn("October 2026", row["body_text"])

    def test_publish_notice_keeps_its_month_after_a_language_change(self):
        import calendar_mail
        now = numer.iso(numer.now_utc())
        with numer.open_db() as connection:
            cal = connection.execute(
                "INSERT INTO calendars (lead_id, practitioner_id, kind, token, created_at, lang)"
                " VALUES (?, ?, 'magialogia', 'tok-lang', ?, 'es')", (self.mine, self.practitioner_id, now)).lastrowid
            for month in ("2026-09", "2026-10"):
                connection.execute("INSERT INTO calendar_months (calendar_id, month, published_at) VALUES (?, ?, ?)",
                                   (cal, month, now))
        self.assertTrue(calendar_mail.queue_published(cal, "2026-09"))
        with numer.open_db() as connection:
            connection.execute("UPDATE calendars SET lang = 'en' WHERE id = ?", (cal,))
        self.login("liliana@example.com")
        self.correct(self.mine)
        with numer.open_db() as connection:
            row = connection.execute("SELECT body_text, sent_at FROM email_outbox WHERE kind = 'calendar_published'").fetchone()
        self.assertIsNone(row["sent_at"])
        self.assertIn("September 2026", row["body_text"])
        self.assertNotIn("October 2026", row["body_text"])

    def test_series_email_being_sent_is_not_rewound(self):
        now = numer.iso(numer.now_utc())
        with numer.open_db() as connection:
            connection.execute("UPDATE leads SET series_step = 2 WHERE id = ?", (self.mine,))
            connection.execute("INSERT INTO email_outbox (kind, to_addr, subject, body_text, body_html, lead_id,"
                               " created_at, claimed_at) VALUES ('lead_series', 'ana@example.com', 's', 's', 's', ?, ?, ?)",
                               (self.mine, now, now))
        self.login("liliana@example.com")
        self.correct(self.mine)
        with numer.open_db() as connection:
            step = connection.execute("SELECT series_step FROM leads WHERE id = ?", (self.mine,)).fetchone()[0]
            row = connection.execute("SELECT sent_at FROM email_outbox WHERE kind = 'lead_series'").fetchone()
        self.assertEqual(step, 2)
        self.assertIsNone(row["sent_at"])

    def test_series_email_in_retry_backoff_is_rebuilt(self):
        import datetime as dt
        soon = numer.iso(numer.now_utc() + dt.timedelta(minutes=10))
        with numer.open_db() as connection:
            connection.execute("UPDATE leads SET series_step = 2 WHERE id = ?", (self.mine,))
            connection.execute("INSERT INTO email_outbox (kind, to_addr, subject, body_text, body_html, lead_id,"
                               " created_at, claimed_at, last_error) VALUES ('lead_series', 'ana@example.com',"
                               " 's', 's', 's', ?, ?, ?, 'SMTP timeout')", (self.mine, numer.iso(numer.now_utc()), soon))
        self.login("liliana@example.com")
        self.correct(self.mine)
        with numer.open_db() as connection:
            step = connection.execute("SELECT series_step FROM leads WHERE id = ?", (self.mine,)).fetchone()[0]
            row = connection.execute("SELECT sent_at, last_error FROM email_outbox WHERE kind = 'lead_series'").fetchone()
        self.assertEqual(step, 1)
        self.assertEqual(row["last_error"], "cancelled: birth details corrected")

    def test_pending_push_alert_shows_the_corrected_name(self):
        import notify
        notify.queue_new_lead(self.mine)
        self.login("liliana@example.com")
        self.correct(self.mine)
        with numer.open_db() as connection:
            payload = json.loads(connection.execute("SELECT payload FROM push_outbox WHERE user_id = ?",
                                                    (self.practitioner_id,)).fetchone()[0])
        self.assertEqual(payload["title"], "New lead: Violetta")
        self.assertEqual(payload["tag"], f"lead-{self.mine}")

    def test_sender_uses_the_rebuilt_email_not_its_earlier_snapshot(self):
        from unittest import mock
        import emails
        emails.queue_unlock_emails(self.mine)
        with numer.open_db() as connection:
            report_id = connection.execute("SELECT id FROM email_outbox WHERE kind = 'report'").fetchone()[0]
        sent = []

        class FakeSMTP:
            def send_message(self, message):
                sent.append(message["Subject"])

            def quit(self):
                pass

        real_connect = emails.connect

        class RacingConnection:
            """Lets a correction rewrite the row after the sender's SELECT, just before its claim."""
            def __init__(self):
                self.inner = real_connect()
                self.raced = False

            def execute(self, sql, *args):
                if not self.raced and sql.startswith("UPDATE email_outbox SET claimed_at"):
                    self.raced = True
                    with numer.open_db() as other:
                        other.execute("UPDATE email_outbox SET subject = 'Corrected subject' WHERE id = ?",
                                      (report_id,))
                return self.inner.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self.inner, name)

        with mock.patch.object(emails, "connect", RacingConnection), \
                mock.patch.object(emails.mailer, "configured", return_value=True):
            emails.send_pending(smtp_factory=FakeSMTP, ids=[report_id])
        self.assertEqual(sent, ["Corrected subject"])

    def test_practitioner_can_save_a_us_state_that_is_also_a_country(self):
        self.login("liliana@example.com")
        self.correct(self.mine, birth_city="Atlanta", birth_region="Georgia", birth_country="United States")
        with numer.open_db() as connection:
            region = connection.execute("SELECT birth_region FROM leads WHERE id = ?", (self.mine,)).fetchone()[0]
        self.assertEqual(region, "Georgia")

    def test_invalid_correction_is_not_saved(self):
        self.login("liliana@example.com")
        self.correct(self.mine, full_name="Ana Lopez 1990")
        with numer.open_db() as connection:
            lead = connection.execute("SELECT full_name, birth_date FROM leads WHERE id = ?", (self.mine,)).fetchone()
        self.assertEqual((lead["full_name"], lead["birth_date"]), ("Ana Lopez", "1990-07-15"))

    def test_cannot_correct_another_practitioners_lead(self):
        self.login("liliana@example.com")
        self.assertEqual(self.correct(self.theirs).status_code, 404)
        with numer.open_db() as connection:
            name = connection.execute("SELECT full_name FROM leads WHERE id = ?", (self.theirs,)).fetchone()[0]
        self.assertEqual(name, "Carla Ruiz")


def numer_numbers(name, born):
    import datetime as dt
    import numerology
    return numerology.core_numbers(name, dt.date.fromisoformat(born)).as_dict()


if __name__ == "__main__":
    unittest.main()
