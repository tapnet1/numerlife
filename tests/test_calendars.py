import datetime as dt
import json

from test_app import PASSWORD, numer, token_from
from test_intake import IntakeTestCase

import calendar_content
import calendar_engine as engine


class EngineTest(IntakeTestCase):
    def test_personal_numbers(self):
        birth = dt.date(1990, 7, 15)
        self.assertEqual(engine.personal_year(birth, 2026), 5)      # 7 + 6 + 1 = 14 -> 5
        self.assertEqual(engine.personal_month(birth, 2026, 10), 6)  # 5 + 1
        self.assertEqual(engine.personal_day(birth, dt.date(2026, 10, 16)), 4)  # 6 + 7 = 13 -> 4
        self.assertEqual(engine.personal_year(dt.date(1980, 2, 8), 2026), 11)  # 2 + 8 + 1: master number kept
        self.assertEqual(engine.universal_day(dt.date(2025, 12, 25)), 1)  # 2+0+2+5+1+2+2+5 = 19 -> 1

    def test_moon_phases_match_published_dates(self):
        phases = engine.moon_phases(dt.date(2025, 12, 1), dt.date(2025, 12, 31), "UTC")
        self.assertEqual(phases, {dt.date(2025, 12, 4): "full", dt.date(2025, 12, 11): "last_quarter",
                                  dt.date(2025, 12, 20): "new", dt.date(2025, 12, 27): "first_quarter"})
        april = engine.moon_phases(dt.date(2024, 4, 1), dt.date(2024, 4, 30), "UTC")
        self.assertEqual(april[dt.date(2024, 4, 8)], "new")      # total solar eclipse day
        # local day: the Dec 4 2025 full moon (23:14 UTC) is still Dec 4 in Edmonton
        self.assertEqual(engine.moon_phases(dt.date(2025, 12, 1), dt.date(2025, 12, 7))[dt.date(2025, 12, 4)], "full")

    def test_month_grid_is_monday_first(self):
        month = engine.build_month(dt.date(1990, 7, 15), 2025, 12, lang="es")
        weeks = month.weeks()
        self.assertEqual(weeks[0][0].date, dt.date(2025, 12, 1))   # Dec 1 2025 was a Monday
        self.assertTrue(all(len(week) == 7 for week in weeks))
        self.assertEqual(month.days[24].holiday, "Navidad")

    def test_guidance_prefers_practitioner_text(self):
        self.assertEqual(calendar_content.guidance(5, "es")["title"], "Cambio")
        self.assertEqual(calendar_content.guidance(5, "es", {5: {"title": "Libertad", "focus": ""}})["title"], "Libertad")
        self.assertEqual(calendar_content.guidance(5, "es", {5: {"focus": ""}})["focus"],
                         calendar_content.DEFAULTS["es"][5]["focus"])


class CalendarTestCase(IntakeTestCase):
    def lead_via_liliana(self):
        response = self.submit("/liliana")
        self.unlock(response.headers["Location"].rsplit("/", 1)[-1])
        return self.leads()[-1]

    def start(self, lead_id, kind="magialogia", lang="es"):
        page = self.client.get(f"/leads/{lead_id}").get_data(as_text=True)
        return self.client.post(f"/leads/{lead_id}/calendar", data={
            "csrf_token": token_from(page), "kind": kind, "lang": lang})

    def calendar(self):
        with numer.open_db() as connection:
            return connection.execute("SELECT * FROM calendars").fetchone()


class CalendarFlowTest(CalendarTestCase):
    def test_practitioner_starts_edits_and_publishes(self):
        lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        self.assertIn("Start calendar", self.client.get(f"/leads/{lead['id']}").get_data(as_text=True))
        self.assertEqual(self.start(lead["id"]).status_code, 302)
        cal = self.calendar()
        page = self.client.get(f"/leads/{lead['id']}/calendar?month=2025-12").get_data(as_text=True)
        self.assertIn("Diciembre 2025", page)
        self.assertIn("Draft.", page)
        # reading parameters
        self.client.post(f"/leads/{lead['id']}/calendar/settings", data={
            "csrf_token": token_from(page), "lang": "es", "an": "7", "ac": "3", "strong": "Marzo a mayo",
            "notes": "Private"})
        self.assertEqual(json.loads(self.calendar()["params"])["an"], "7")
        # client can't see the draft month
        public = self.client_view(cal["token"], "2025-12")
        self.client.post("/logout", data={"csrf_token": token_from(page)})
        self.assertIn("aún no está listo", self.client.get(f"/c/{cal['token']}/2025-12").get_data(as_text=True))
        self.assertEqual(self.client.get(f"/c/{cal['token']}/2025-12.pdf").status_code, 404)
        # practitioner adds a note and publishes
        self.login("liliana@example.com")
        page = self.client.get(f"/leads/{lead['id']}/calendar?month=2025-12").get_data(as_text=True)
        self.client.post(f"/leads/{lead['id']}/calendar/2025-12", data={
            "csrf_token": token_from(page), "intro": "Un mes de cierre.", "note-2025-12-24": "Llama a tu madre",
            "action": "publish"})
        self.client.post("/logout", data={"csrf_token": token_from(page)})
        html = self.client.get(f"/c/{cal['token']}/2025-12").get_data(as_text=True)
        self.assertIn("Llama a tu madre", html)
        self.assertIn("Un mes de cierre.", html)
        self.assertIn("Luna llena", html)
        self.assertNotIn("Private", html)                     # private notes never shown
        pdf = self.client.get(f"/c/{cal['token']}/2025-12.pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.data.startswith(b"%PDF"))
        self.assertIn("noindex", self.client.get(f"/c/{cal['token']}/2025-12").headers.get("X-Robots-Tag", ""))

    def client_view(self, token, month):
        return self.client.get(f"/c/{token}/{month}")

    def test_practitioner_preview_of_unpublished_month(self):
        lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        self.start(lead["id"])
        cal = self.calendar()
        html = self.client.get(f"/c/{cal['token']}/2025-12").get_data(as_text=True)
        self.assertIn("Preview", html)
        self.assertEqual(self.client.get(f"/c/{cal['token']}/2025-12.pdf").status_code, 200)

    def test_numerology_calendar_is_always_visible(self):
        lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        page = self.client.get(f"/leads/{lead['id']}").get_data(as_text=True)
        self.start(lead["id"], kind="numerology", lang="en")
        self.client.post("/logout", data={"csrf_token": token_from(page)})
        cal = self.calendar()
        html = self.client.get(f"/c/{cal['token']}/2026-01").get_data(as_text=True)
        self.assertIn("January 2026", html)
        self.assertIn("Personal year", html)

    def test_scoping_and_pause(self):
        lead = self.lead_via_liliana()
        numer.create_user("maria@example.com", PASSWORD, "practitioner", "Maria", slug="maria")
        self.login("maria@example.com")
        page = self.client.get("/dashboard").get_data(as_text=True)
        self.assertEqual(self.client.post(f"/leads/{lead['id']}/calendar", data={
            "csrf_token": token_from(page), "kind": "numerology", "lang": "en"}).status_code, 404)
        self.client.post("/logout", data={"csrf_token": token_from(page)})
        self.login("liliana@example.com")
        self.start(lead["id"], kind="numerology")
        cal = self.calendar()
        page = self.client.get(f"/leads/{lead['id']}/calendar").get_data(as_text=True)
        self.client.post(f"/leads/{lead['id']}/calendar/settings", data={
            "csrf_token": token_from(page), "lang": "es", "paused": "yes"})
        self.assertEqual(self.client.get(f"/c/{cal['token']}").status_code, 404)
        self.assertEqual(self.client.get("/c/not-a-real-token").status_code, 404)

    def test_bad_input(self):
        lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        page = self.client.get(f"/leads/{lead['id']}").get_data(as_text=True)
        self.assertEqual(self.client.post(f"/leads/{lead['id']}/calendar", data={
            "csrf_token": token_from(page), "kind": "tarot", "lang": "en"}).status_code, 400)
        self.start(lead["id"])
        page = self.client.get(f"/leads/{lead['id']}/calendar").get_data(as_text=True)
        self.assertEqual(self.client.post(f"/leads/{lead['id']}/calendar/2025-13", data={
            "csrf_token": token_from(page)}).status_code, 404)


class CalendarReviewTest(CalendarTestCase):
    def test_master_33_has_its_own_guidance(self):
        self.assertEqual(calendar_content.guidance(33, "es")["title"], "Maestro sanador")
        for lang in calendar_content.DEFAULTS:
            self.assertEqual(set(calendar_content.DEFAULTS[lang]), set(calendar_content.NUMBERS))

    def test_calendar_follows_lead_reassignment(self):
        lead = self.lead_via_liliana()
        maria = numer.create_user("maria@example.com", PASSWORD, "practitioner", "Maria Lopez", slug="maria")
        self.login("liliana@example.com")
        self.start(lead["id"])
        cal = self.calendar()
        page = self.client.get("/dashboard").get_data(as_text=True)
        self.client.post("/logout", data={"csrf_token": token_from(page)})
        self.login("owner@example.com")
        page = self.client.get(f"/leads/{lead['id']}").get_data(as_text=True)
        self.client.post(f"/leads/{lead['id']}/assign", data={"csrf_token": token_from(page), "practitioner_id": maria})
        self.assertEqual(self.calendar()["practitioner_id"], maria)
        self.client.post("/logout", data={"csrf_token": token_from(page)})
        self.login("maria@example.com")
        html = self.client.get(f"/c/{cal['token']}/2025-12").get_data(as_text=True)
        self.assertIn("Maria Lopez", html)
        self.assertIn("Preview", html)               # new practitioner can preview
        self.client.post("/logout", data={"csrf_token": token_from(html)})
        self.login("liliana@example.com")
        self.assertNotIn("Preview", self.client.get(f"/c/{cal['token']}/2025-12").get_data(as_text=True))
