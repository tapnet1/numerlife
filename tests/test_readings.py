import json

from test_app import PASSWORD, numer, token_from
from test_calendars import CalendarTestCase


class ReadingsTest(CalendarTestCase):
    def readings(self):
        with numer.open_db() as connection:
            return connection.execute("SELECT * FROM readings ORDER BY id").fetchall()

    def test_schedule_complete_and_record_details(self):
        lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        self.assertIn("Schedule or log a reading", self.client.get(f"/leads/{lead['id']}").get_data(as_text=True))
        response = self.log_reading(lead["id"], done=False)
        reading = self.readings()[0]
        self.assertEqual((reading["status"], reading["scheduled_at"], reading["duration_min"]),
                         ("scheduled", "2025-11-20T18:30", 90))
        self.assertEqual(self.leads()[-1]["stage"], "contacted")      # booking moves the lead along
        page = self.client.get(response.headers["Location"]).get_data(as_text=True)
        self.assertIn("Magialogía Numérica details", page)
        self.client.post(f"/leads/{lead['id']}/readings/{reading['id']}", data={
            "csrf_token": token_from(page), "status": "completed", "kind": "magialogia", "mode": "online",
            "date": "2025-11-20", "time": "18:30", "duration": "90", "notes": "Intensa y clara.",
            "an": "7", "ac": "3", "strong": "Marzo a mayo"})
        reading = self.readings()[0]
        self.assertEqual(reading["status"], "completed")
        self.assertIsNotNone(reading["completed_at"])
        self.assertEqual(json.loads(reading["params"])["an"], "7")
        self.assertEqual(reading["notes"], "Intensa y clara.")

    def test_magialogia_calendar_requires_completed_reading_and_uses_its_details(self):
        lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        response = self.start(lead["id"], with_reading=False)
        self.assertIn("#readings", response.headers["Location"])
        self.assertIsNone(self.calendar())
        self.log_reading(lead["id"], done=False)                      # scheduled isn't enough
        self.start(lead["id"], with_reading=False)
        self.assertIsNone(self.calendar())
        self.log_reading(lead["id"], done=True)
        reading = self.readings()[-1]
        page = self.client.get(f"/leads/{lead['id']}/readings/{reading['id']}").get_data(as_text=True)
        self.client.post(f"/leads/{lead['id']}/readings/{reading['id']}", data={
            "csrf_token": token_from(page), "status": "completed", "kind": "magialogia", "mode": "online",
            "date": "2025-11-20", "time": "18:30", "duration": "90", "an": "9", "weak": "Agosto"})
        self.start(lead["id"], with_reading=False)
        self.assertIsNotNone(self.calendar())
        editor = self.client.get(f"/leads/{lead['id']}/calendar?month=2025-12").get_data(as_text=True)
        self.assertIn("Edit the reading details", editor)
        self.assertIn("Agosto", editor)
        pdf = self.client.get(f"/c/{self.calendar()['token']}/2025-12.pdf")
        self.assertEqual(pdf.status_code, 200)

    def test_numerology_calendar_needs_no_reading(self):
        lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        self.start(lead["id"], kind="numerology", lang="en")
        self.assertIsNotNone(self.calendar())

    def test_upcoming_readings_on_dashboard_and_scoping(self):
        import calendar_engine, datetime as dt
        lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        page = self.client.get(f"/leads/{lead['id']}").get_data(as_text=True)
        soon = calendar_engine.local_today() + dt.timedelta(days=2)
        self.client.post(f"/leads/{lead['id']}/readings", data={
            "csrf_token": token_from(page), "kind": "numerology", "date": soon.isoformat(), "time": "10:00",
            "duration": "60", "mode": "phone"})
        dashboard = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("Upcoming readings", dashboard)
        self.assertIn("John Smith", dashboard)
        reading = self.readings()[0]
        self.client.post("/logout", data={"csrf_token": token_from(dashboard)})
        numer.create_user("maria@example.com", PASSWORD, "practitioner", "Maria", slug="maria")
        self.login("maria@example.com")
        self.assertNotIn("Upcoming readings", self.client.get("/dashboard").get_data(as_text=True))
        self.assertEqual(self.client.get(f"/leads/{lead['id']}/readings/{reading['id']}").status_code, 404)

    def test_validation(self):
        lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        page = self.client.get(f"/leads/{lead['id']}").get_data(as_text=True)
        html = self.client.post(f"/leads/{lead['id']}/readings", follow_redirects=True, data={
            "csrf_token": token_from(page), "kind": "tarot", "date": "nope"}).get_data(as_text=True)
        self.assertIn("Choose a reading type", html)
        self.assertEqual(self.readings(), [])


class ReadingsReviewTest(ReadingsTest):
    def test_latest_reading_is_by_session_date(self):
        lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        page = self.client.get(f"/leads/{lead['id']}").get_data(as_text=True)
        for date in ("2025-11-20", "2025-06-01"):      # the older session logged last
            self.client.post(f"/leads/{lead['id']}/readings", data={
                "csrf_token": token_from(page), "kind": "magialogia", "date": date, "time": "10:00",
                "duration": "60", "mode": "online", "already_done": "yes"})
        import readings
        with numer.app.test_request_context("/"):
            self.assertEqual(readings.completed_magialogia(lead["id"])["scheduled_at"], "2025-11-20T10:00")

    def test_reading_page_offers_switching_a_numerology_calendar(self):
        lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        self.start(lead["id"], kind="numerology", lang="fr")
        self.log_reading(lead["id"], done=True)
        reading = self.readings()[-1]
        page = self.client.get(f"/leads/{lead['id']}/readings/{reading['id']}").get_data(as_text=True)
        self.assertIn("Switch to Magialogía calendar", page)
        self.client.post(f"/leads/{lead['id']}/calendar", data={
            "csrf_token": token_from(page), "kind": "magialogia", "lang": "fr"})
        self.assertEqual((self.calendar()["kind"], self.calendar()["lang"]), ("magialogia", "fr"))
