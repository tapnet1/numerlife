import datetime as dt
import os
import tempfile
import time

from test_app import numer
from test_calendars import CalendarTestCase

import retention


class RetentionTest(CalendarTestCase):
    def later(self, days):
        return numer.now_utc() + dt.timedelta(days=days)

    def purge(self, days):
        with numer.open_db() as connection:
            return retention.purge_leads(connection, self.later(days))

    def count(self, table, where="1", args=()):
        with numer.open_db() as connection:
            return connection.execute(f"SELECT count(*) FROM {table} WHERE {where}", args).fetchone()[0]

    def client_with_history(self):
        lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        self.start(lead["id"], kind="numerology", lang="en")
        cal = self.calendar()
        now = numer.iso(numer.now_utc())
        with numer.open_db() as connection:
            entry = connection.execute(
                "INSERT INTO feedback_entries (lead_id, session_kind, entry_date, body, created_at, source)"
                " VALUES (?, 'calendar', ?, 'Felt calm', ?, 'calendar')",
                (lead["id"], numer.now_utc().date().isoformat(), now)).lastrowid
            connection.execute("INSERT INTO journal_replies (entry_id, lead_id, author, body, created_at)"
                               " VALUES (?, ?, 'client', 'More', ?)", (entry, lead["id"], now))
            connection.execute("INSERT INTO calendar_notes (calendar_id, day, text) VALUES (?, '2026-01-01', 'x')",
                               (cal["id"],))
        return lead

    def test_inactive_client_is_deleted_with_everything(self):
        lead = self.client_with_history()
        self.assertGreater(self.count("email_outbox", "lead_id = ?", (lead["id"],)), 0)
        self.assertEqual(self.purge(200), 0)
        self.assertEqual(self.purge(400), 1)
        for table in ("feedback_entries", "journal_replies", "calendars", "client_pages", "email_outbox"):
            self.assertEqual(self.count(table, "lead_id = ?", (lead["id"],)), 0, table)
        self.assertEqual(self.count("calendar_notes"), 0)
        self.assertEqual(self.count("leads", "id = ?", (lead["id"],)), 0)
        self.assertEqual(self.count("users", "email = 'liliana@example.com'"), 1)

    def test_recent_note_or_paid_time_keeps_the_client(self):
        lead = self.client_with_history()
        recent = numer.iso(self.later(380))
        with numer.open_db() as connection:
            connection.execute("UPDATE feedback_entries SET created_at = ? WHERE lead_id = ?", (recent, lead["id"]))
        self.assertEqual(self.purge(400), 0)
        with numer.open_db() as connection:
            connection.execute("DELETE FROM journal_replies")
            connection.execute("DELETE FROM feedback_entries")
            connection.execute("UPDATE calendars SET access_until = ?", (self.later(500).date().isoformat(),))
        self.assertEqual(self.purge(400), 0)
        with numer.open_db() as connection:
            connection.execute("UPDATE calendars SET access_until = ?", (self.later(10).date().isoformat(),))
        self.assertEqual(self.purge(400), 1)

    def test_client_visit_counts_as_activity(self):
        lead = self.lead_via_liliana()
        with numer.open_db() as connection:
            token = connection.execute("SELECT token FROM client_pages WHERE lead_id = ?", (lead["id"],)).fetchone()[0]
            old = numer.iso(numer.now_utc() - dt.timedelta(days=500))
            connection.execute("UPDATE leads SET created_at = ?, captured_at = ?, updated_at = ?, client_seen_at = NULL",
                               (old, old, old))
            connection.execute("UPDATE client_pages SET created_at = ?", (old,))
        with numer.open_db() as connection:
            self.assertEqual(retention.expired_leads(connection), [lead["id"]])
        numer.app.test_client().get(f"/me/{token}")
        with numer.open_db() as connection:
            self.assertEqual(retention.expired_leads(connection), [])

    def test_unfinished_report_is_deleted_after_30_days(self):
        self.submit("/liliana")
        self.assertEqual(self.count("leads", "status = 'started'"), 1)
        self.assertEqual(self.purge(10), 0)
        self.assertEqual(self.purge(31), 1)
        self.assertEqual(self.count("leads"), 0)

    def test_old_backups_are_deleted_but_newest_kept(self):
        folder = tempfile.mkdtemp()
        live = os.path.join(folder, "x.db")
        old = time.time() - 200 * 86400
        for i in range(8):
            path = f"{live}.2026010{i}T000000Z.bak"
            open(path, "w").close()
            os.utime(path, (old + i, old + i))
        open(os.path.join(folder, "other.bak"), "w").close()
        self.assertEqual(retention.purge_backups(live), 3)
        left = sorted(os.listdir(folder))
        self.assertEqual(len(left), 6)
        self.assertIn("other.bak", left)
        self.assertNotIn("x.db.20260100T000000Z.bak", left)
