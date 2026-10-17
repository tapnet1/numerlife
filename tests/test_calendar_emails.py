import datetime as dt
import re
from zoneinfo import ZoneInfo

from test_app import numer, token_from
from test_calendars import CalendarTestCase

import calendar_engine as engine
import calendar_mail
import emails


def local_morning(days_ahead=1, hour=8):
    day = engine.local_today() + dt.timedelta(days=days_ahead)
    return dt.datetime(day.year, day.month, day.day, hour, tzinfo=ZoneInfo(engine.DEFAULT_TZ)).astimezone(dt.timezone.utc)


class CalendarEmailTest(CalendarTestCase):
    def outbox(self, kind=None):
        with numer.open_db() as connection:
            query = "SELECT * FROM email_outbox WHERE kind LIKE 'calendar%'" + (" AND kind = ?" if kind else "")
            return connection.execute(query + " ORDER BY id", (kind,) if kind else ()).fetchall()

    def signup(self, lead, freq="daily", lang="es"):
        page = self.client.get(f"/r/{lead['token']}").get_data(as_text=True)
        return self.client.post(f"/r/{lead['token']}/calendar",
                                data={"csrf_token": token_from(page), "freq": freq, "lang": lang})

    def test_report_signup_daily(self):
        lead = self.lead_via_liliana()
        page = self.client.get(f"/r/{lead['token']}", headers={"Accept-Language": "es-MX,es;q=0.9"})
        self.assertIn("Get your personal numerology calendar", page.get_data(as_text=True))
        self.assertIn('<option value="es" selected>', page.get_data(as_text=True))
        response = self.signup(lead)
        cal = self.calendar()
        self.assertEqual(response.headers["Location"], f"/c/{cal['token']}")
        self.assertEqual((cal["kind"], cal["lang"], cal["email_freq"]), ("numerology", "es", "daily"))
        welcome = self.outbox()
        self.assertEqual(len(welcome), 1)
        self.assertTrue(welcome[0]["subject"].startswith("Tu calendario de"))
        self.assertIn("Tu calendario personal está listo.", welcome[0]["body_text"])
        self.assertIn('lang="es"', welcome[0]["body_html"])
        self.assertIn("on its way by email every morning",
                      self.client.get(f"/r/{lead['token']}").get_data(as_text=True))
        # cron: nothing before 7 am, then one daily email per day
        self.assertEqual(calendar_mail.run(local_morning(1, hour=5))["daily"], 0)
        self.assertEqual(calendar_mail.run(local_morning(1))["daily"], 1)
        self.assertEqual(calendar_mail.run(local_morning(1, hour=9))["daily"], 0)
        self.assertEqual(calendar_mail.run(local_morning(2))["daily"], 1)
        daily = self.outbox("calendar_daily")
        self.assertTrue(daily[0]["subject"].startswith("Día personal "))
        self.assertIn("Enfoque:", daily[0]["body_text"])

    def test_unsubscribe_link_stops_calendar_email(self):
        lead = self.lead_via_liliana()
        self.signup(lead, freq="daily", lang="en")
        calendar_mail.run(local_morning(1))
        pending = self.outbox("calendar_daily")[0]
        token = re.search(r"/unsubscribe/(\S+)", pending["body_text"]).group(1)
        page = self.client.get(f"/unsubscribe/{token}").get_data(as_text=True)
        self.client.post(f"/unsubscribe/{token}", data={"csrf_token": token_from(page)})
        self.assertEqual(self.calendar()["email_freq"], "none")
        self.assertIn("cancelled", self.outbox("calendar_daily")[0]["last_error"])
        self.assertEqual(calendar_mail.run(local_morning(2))["daily"], 0)

    def test_client_changes_frequency_on_calendar_page(self):
        lead = self.lead_via_liliana()
        self.signup(lead, freq="daily", lang="en")
        cal = self.calendar()
        page = self.client.get(f"/c/{cal['token']}").get_data(as_text=True)
        self.assertIn("Calendar by email", page)
        self.client.post(f"/c/{cal['token']}/emails", data={"csrf_token": token_from(page), "freq": "monthly"})
        self.assertEqual(self.calendar()["email_freq"], "monthly")
        self.assertEqual(len(self.outbox()), 1)               # no second welcome when switching
        self.assertEqual(calendar_mail.run(local_morning(1))["monthly"], 0)  # this month already sent
        next_month = dt.date(*engine.shift_month(*engine.local_today().timetuple()[:2], 1), 1)
        later = dt.datetime(next_month.year, next_month.month, 1, 8, tzinfo=ZoneInfo(engine.DEFAULT_TZ))
        self.assertEqual(calendar_mail.run(later.astimezone(dt.timezone.utc))["monthly"], 1)
        self.assertEqual(self.client.post(f"/c/{cal['token']}/emails",
                                          data={"csrf_token": token_from(page), "freq": "hourly"}).status_code, 400)

    def test_magialogia_emails_only_published_months(self):
        lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        self.start(lead["id"])
        cal = self.calendar()
        page = self.client.get(f"/leads/{lead['id']}/calendar").get_data(as_text=True)
        self.assertIn("Email John the link when I publish", page)
        self.client.post(f"/leads/{lead['id']}/calendar/settings",
                         data={"csrf_token": token_from(page), "lang": "es", "email_freq": "monthly"})
        self.assertEqual(self.calendar()["email_freq"], "monthly")
        self.assertEqual(self.outbox(), [])                  # nothing published yet, so no welcome
        self.assertEqual(calendar_mail.run(local_morning(0, hour=23))["monthly"], 0)
        key = engine.local_today().strftime("%Y-%m")
        self.client.post(f"/leads/{lead['id']}/calendar/{key}", data={
            "csrf_token": token_from(page), "intro": "Tu mes", "action": "publish", "notify": "yes"})
        sent = self.outbox("calendar_published")
        self.assertEqual(len(sent), 1)
        self.assertIn("Liliana", sent[0]["subject"])
        self.assertIn("Tu mes", sent[0]["body_text"])
        # the monthly run doesn't repeat it
        with numer.open_db() as connection:
            connection.execute("UPDATE calendars SET last_emailed = NULL WHERE id = ?", (cal["id"],))
        self.assertEqual(calendar_mail.run(local_morning(0, hour=23))["monthly"], 0)
        self.assertEqual(len(self.outbox()), 1)

    def test_paused_calendar_drops_queued_email(self):
        lead = self.lead_via_liliana()
        self.signup(lead, freq="daily", lang="en")
        with numer.open_db() as connection:
            connection.execute("UPDATE calendars SET active = 0")
            row = connection.execute("SELECT * FROM email_outbox WHERE kind = 'calendar_monthly'").fetchone()
            self.assertFalse(emails._still_wanted(connection, row))

    def test_signup_requires_unlocked_report(self):
        response = self.submit("/liliana")
        token = response.headers["Location"].rsplit("/", 1)[-1]
        page = self.client.get(f"/r/{token}").get_data(as_text=True)
        self.assertNotIn("Get your personal numerology calendar", page)
        self.assertEqual(self.client.post(f"/r/{token}/calendar", data={
            "csrf_token": token_from(page), "freq": "daily", "lang": "en"}).status_code, 400)

    def test_email_labels_cover_every_language(self):
        import calendar_content
        for lang in calendar_content.LANGUAGES:
            self.assertEqual(set(calendar_mail.EMAIL_LABELS[lang]), set(calendar_mail.EMAIL_LABELS["en"]), lang)

    def magialogia_for(self, lead, notify=None, publish=True, email_freq=None):
        self.login("liliana@example.com")
        self.start(lead["id"])
        page = self.client.get(f"/leads/{lead['id']}/calendar").get_data(as_text=True)
        if email_freq:
            self.client.post(f"/leads/{lead['id']}/calendar/settings",
                             data={"csrf_token": token_from(page), "lang": "es", "email_freq": email_freq})
        if publish:
            data = {"csrf_token": token_from(page), "action": "publish"}
            if notify:
                data["notify"] = "yes"
            self.client.post(f"/leads/{lead['id']}/calendar/{engine.local_today():%Y-%m}", data=data)
        return page

    def test_opt_out_stops_publish_notices(self):
        lead = self.lead_via_liliana()
        page = self.magialogia_for(lead, publish=False)
        cal = self.calendar()
        self.client.post(f"/c/{cal['token']}/emails", data={"csrf_token": token_from(page), "freq": "none"})
        self.assertEqual(self.calendar()["email_opt_out"], 1)
        editor = self.client.get(f"/leads/{lead['id']}/calendar").get_data(as_text=True)
        self.assertIn("has turned off calendar emails", editor)
        self.client.post(f"/leads/{lead['id']}/calendar/{engine.local_today():%Y-%m}",
                         data={"csrf_token": token_from(editor), "action": "publish", "notify": "yes"})
        self.assertEqual(self.outbox("calendar_published"), [])
        # subscribing again lifts the opt-out
        self.client.post(f"/c/{cal['token']}/emails", data={"csrf_token": token_from(page), "freq": "monthly"})
        self.assertEqual(self.calendar()["email_opt_out"], 0)

    def test_monthly_waits_for_publishing_without_notice(self):
        lead = self.lead_via_liliana()
        page = self.magialogia_for(lead, publish=False, email_freq="monthly")
        self.assertIsNone(self.calendar()["last_emailed"])          # nothing sent, so nothing marked
        self.client.post(f"/leads/{lead['id']}/calendar/{engine.local_today():%Y-%m}",
                         data={"csrf_token": token_from(page), "action": "publish"})
        self.assertEqual(calendar_mail.run(local_morning(0, hour=23))["monthly"], 1)

    def test_resaving_settings_keeps_todays_daily_email(self):
        lead = self.lead_via_liliana()
        self.signup(lead, freq="daily", lang="en")
        with numer.open_db() as connection:
            connection.execute("UPDATE calendars SET last_emailed = '2000-01-01'")
        calendar_mail.set_frequency(self.calendar()["id"], "daily")
        self.assertEqual(self.calendar()["last_emailed"], "2000-01-01")

    def test_signup_on_existing_calendar_keeps_its_language(self):
        lead = self.lead_via_liliana()
        page = self.magialogia_for(lead)                    # Spanish calendar, emails off
        self.client.post("/logout", data={"csrf_token": token_from(page)})
        report = self.client.get(f"/r/{lead['token']}").get_data(as_text=True)
        self.assertIn('<input type="hidden" name="lang" value="es">', report)
        self.assertNotIn('id="cal-lang"', report)

    def test_switching_monthly_to_daily_sends_tomorrow(self):
        lead = self.lead_via_liliana()
        self.signup(lead, freq="monthly", lang="en")
        cal = self.calendar()
        page = self.client.get(f"/c/{cal['token']}").get_data(as_text=True)
        self.client.post(f"/c/{cal['token']}/emails", data={"csrf_token": token_from(page), "freq": "daily"})
        self.assertEqual(calendar_mail.run(local_morning(0, hour=23))["daily"], 1)  # today's hasn't gone yet
        self.assertEqual(calendar_mail.run(local_morning(1))["daily"], 1)
