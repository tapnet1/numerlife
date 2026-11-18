import datetime as dt

from test_app import numer, token_from
from test_calendars import CalendarTestCase


def month_after(day: dt.date, months: int) -> str:
    y, m = day.year, day.month + months
    while m > 12:
        y, m = y + 1, m - 12
    return f"{y:04d}-{m:02d}"


class CalendarAccessTest(CalendarTestCase):
    def setUp(self):
        super().setUp()
        self.lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        self.start(self.lead["id"], kind="numerology", lang="en")
        self.cal = self.calendar()
        self.today = dt.date.fromisoformat(self.cal["created_at"][:10])
        self.visitor = numer.app.test_client()

    def page(self, key):
        return self.visitor.get(f"/c/{self.cal['token']}/{key}").get_data(as_text=True)

    def test_free_30_days_then_locked(self):
        self.assertNotIn("part of your full year", self.page(self.today.strftime("%Y-%m")))
        self.assertIn("part of your full year", self.page(month_after(self.today, 2)))
        self.assertIn("CAD $250", self.page(month_after(self.today, 2)))
        self.assertEqual(self.visitor.get(f"/c/{self.cal['token']}/{month_after(self.today, 2)}.pdf").status_code, 404)

    def test_practitioner_still_previews_and_can_give_a_year(self):
        later = month_after(self.today, 3)
        staff_view = self.client.get(f"/c/{self.cal['token']}/{later}").get_data(as_text=True)
        self.assertIn("access ends", staff_view)
        page = self.client.get(f"/leads/{self.lead['id']}").get_data(as_text=True)
        self.assertIn("Give one more year", page)
        self.client.post(f"/leads/{self.lead['id']}/calendar/access",
                         data={"csrf_token": token_from(page), "year": "yes"})
        self.assertNotIn("part of your full year", self.page(later))

    def test_emails_stop_after_access_ends(self):
        with numer.open_db() as connection:
            connection.execute("UPDATE calendars SET email_freq = 'daily', access_until = ? WHERE id = ?",
                               ((self.today - dt.timedelta(days=1)).isoformat(), self.cal["id"]))
        with numer.app.app_context():
            cal = numer.db().execute("SELECT * FROM calendars WHERE id = ?", (self.cal["id"],)).fetchone()
            self.assertFalse(numer.calendar_access.day_open(cal, self.today))
            self.assertTrue(numer.calendar_access.day_open(cal, self.today - dt.timedelta(days=1)))
