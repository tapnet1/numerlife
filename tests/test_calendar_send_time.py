import datetime as dt
from zoneinfo import ZoneInfo

import calendar_engine as engine
from test_app import numer
from test_calendars import CalendarTestCase

UTC = dt.timezone.utc


class SendTimeTest(CalendarTestCase):
    def setUp(self):
        super().setUp()
        self.lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        self.start(self.lead["id"], kind="numerology", lang="en")
        with numer.open_db() as connection:
            connection.execute("UPDATE calendars SET email_freq = 'daily'")
        self.cal = self.calendar()

    def add_daily(self, days_ago: int, open_local_hour: int | None, open_after_seconds: int | None = None):
        zone = ZoneInfo(engine.DEFAULT_TZ)
        sent_local = dt.datetime.combine(dt.date.today() - dt.timedelta(days=days_ago), dt.time(7, 17), zone)
        sent = sent_local.astimezone(UTC)
        track = f"t{days_ago}"
        with numer.open_db() as connection:
            connection.execute("INSERT INTO email_outbox (kind, to_addr, subject, body_text, body_html, lead_id,"
                               " created_at, sent_at, track) VALUES ('calendar_daily', 'a@b.c', 's', 't', 'h', ?, ?, ?, ?)",
                               (self.lead["id"], numer.iso(sent), numer.iso(sent), track))
            if open_local_hour is not None:
                opened = (sent + dt.timedelta(seconds=open_after_seconds) if open_after_seconds is not None else
                          dt.datetime.combine(sent_local.date(), dt.time(open_local_hour, 30), zone).astimezone(UTC))
                for _ in range(2):  # a second open of the same email doesn't count twice
                    connection.execute("INSERT INTO email_events (track, event, created_at) VALUES (?, 'open', ?)",
                                       (track, numer.iso(opened)))

    def learned(self):
        with numer.app.app_context():
            numer.calendar_send_time.refresh()
        return self.calendar()["send_hour"]

    def test_needs_enough_opens(self):
        for d in range(1, 5):
            self.add_daily(d, 9)
        self.assertIsNone(self.learned())

    def test_most_common_open_hour_minus_one(self):
        for d, hour in zip(range(1, 9), [9, 9, 9, 12, 20, 9, 12, 12]):
            self.add_daily(d, hour)
        self.assertEqual(self.learned(), 8)
        cal = self.calendar()
        self.assertIsNotNone(cal["send_hour_set_at"])

    def test_ties_take_the_earliest_and_prefetch_opens_are_ignored(self):
        for d, hour in zip(range(1, 7), [9, 9, 18, 18, None, None]):
            self.add_daily(d, hour)
        for d in range(7, 15):
            self.add_daily(d, 0, open_after_seconds=30)  # preloaded at delivery: ignored
        self.assertIsNone(self.learned())               # only 4 real opens
        self.add_daily(15, 18)
        with numer.open_db() as connection:
            connection.execute("UPDATE calendars SET send_hour_checked_at = NULL")
        self.assertEqual(self.learned(), 17)            # 18 is now the most common

    def test_run_waits_for_the_clients_hour(self):
        with numer.open_db() as connection:
            connection.execute("UPDATE calendars SET send_hour = 10, send_hour_checked_at = ?", (numer.iso(numer.now_utc()),))
        zone = ZoneInfo(engine.DEFAULT_TZ)
        today = engine.local_today()
        early = dt.datetime.combine(today, dt.time(8, 17), zone).astimezone(UTC)
        later = dt.datetime.combine(today, dt.time(10, 17), zone).astimezone(UTC)
        self.assertEqual(numer.calendar_mail.run(now=early)["daily"], 0)
        self.assertEqual(numer.calendar_mail.run(now=later)["daily"], 1)
        with numer.open_db() as connection:
            row = connection.execute("SELECT * FROM email_outbox WHERE kind = 'calendar_daily'").fetchone()
        self.assertTrue(row["track"])
        self.assertIn(f"/e/o/{row['track']}.gif", row["body_html"])

    def test_admin_report(self):
        for d, hour in zip(range(1, 9), [9] * 8):
            self.add_daily(d, hour)
        self.learned()
        self.client = numer.app.test_client()
        self.login("owner@example.com")
        html = self.client.get("/admin").get_data(as_text=True)
        self.assertIn("Daily calendar emails: send time", html)
        self.assertIn("08:00", html)
