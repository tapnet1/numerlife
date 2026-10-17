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

    def log_reading(self, lead_id, kind="magialogia", done=True):
        page = self.client.get(f"/leads/{lead_id}").get_data(as_text=True)
        data = {"csrf_token": token_from(page), "kind": kind, "date": "2025-11-20", "time": "18:30",
                "duration": "90", "mode": "online"}
        if done:
            data["already_done"] = "yes"
        return self.client.post(f"/leads/{lead_id}/readings", data=data)

    def start(self, lead_id, kind="magialogia", lang="es", with_reading=True):
        if kind == "magialogia" and with_reading:
            self.log_reading(lead_id)
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


    def test_today_is_local_not_utc(self):
        late_evening = dt.datetime(2026, 11, 1, 3, 0, tzinfo=dt.timezone.utc)  # Oct 31, 8 pm in Edmonton
        self.assertEqual(engine.local_today("America/Edmonton", late_evening), dt.date(2026, 10, 31))


class LanguageTest(CalendarTestCase):
    def test_every_language_is_complete(self):
        reference = calendar_content.LABELS["en"]
        for lang in calendar_content.LANGUAGES:
            labels = calendar_content.LABELS[lang]
            self.assertEqual(set(labels), set(reference), lang)
            self.assertEqual(len(labels["weekdays"]), 7, lang)
            self.assertEqual(set(labels["moon"]), set(reference["moon"]), lang)
            self.assertEqual(len(calendar_content.MONTH_NAMES[lang]), 12, lang)
            self.assertEqual(set(calendar_content.DEFAULTS[lang]), set(calendar_content.NUMBERS), lang)
            for number, guide in calendar_content.DEFAULTS[lang].items():
                self.assertTrue(all(guide[field] for field in calendar_content.FIELDS), (lang, number))
            month = engine.build_month(dt.date(1990, 7, 15), 2025, 12, lang=lang)
            self.assertNotEqual(month.days[24].holiday, None)

    def test_french_calendar_page_and_pdf(self):
        lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        self.assertIn("Français", self.client.get(f"/leads/{lead['id']}").get_data(as_text=True))
        self.start(lead["id"], kind="numerology", lang="fr")
        cal = self.calendar()
        html = self.client.get(f"/c/{cal['token']}/2025-12").get_data(as_text=True)
        self.assertIn("Décembre 2025", html)
        self.assertIn("Noël", html)
        self.assertTrue(self.client.get(f"/c/{cal['token']}/2025-12.pdf").data.startswith(b"%PDF"))


    def test_language_picker_defaults(self):
        lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        self.assertIn('<option value="es" selected>', self.client.get(f"/leads/{lead['id']}").get_data(as_text=True))
        self.start(lead["id"], kind="numerology", lang="fr")
        second = self.lead_via_liliana()
        self.assertIn('<option value="fr" selected>', self.client.get(f"/leads/{second['id']}").get_data(as_text=True))


class GuidanceEditorTest(CalendarTestCase):
    def save(self, lang, **fields):
        page = self.client.get(f"/settings/calendar?lang={lang}").get_data(as_text=True)
        return self.client.post("/settings/calendar", data={"csrf_token": token_from(page), "lang": lang, **fields})

    def test_practitioner_wording_replaces_defaults_in_her_calendars(self):
        lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        page = self.client.get("/settings/calendar?lang=es").get_data(as_text=True)
        self.assertIn("Nuevos comienzos", page)                     # starter text shown as placeholder
        self.assertIn("0 of 12 numbers", page)
        self.save("es", **{"5-title": "Libertad", "5-focus": "  Viaja   ligero  ",
                           "1-title": calendar_content.DEFAULTS["es"][1]["title"]})  # unchanged text isn't stored
        with numer.open_db() as connection:
            rows = connection.execute("SELECT number, title, focus FROM calendar_content").fetchall()
        self.assertEqual([(r["number"], r["title"], r["focus"]) for r in rows], [(5, "Libertad", "Viaja ligero")])
        self.assertIn("1 of 12 numbers", self.client.get("/settings/calendar?lang=es").get_data(as_text=True))
        self.start(lead["id"], kind="numerology", lang="es")
        cal = self.calendar()
        # find a month whose personal month is 5
        birth = dt.date.fromisoformat(lead["birth_date"])
        month = next(m for m in range(1, 13) if engine.personal_month(birth, 2026, m) == 5)
        html = self.client.get(f"/c/{cal['token']}/2026-{month:02d}").get_data(as_text=True)
        self.assertIn("Libertad", html)
        self.assertIn("Viaja ligero", html)
        # clearing the field goes back to the default; reset removes everything
        self.save("es", **{"5-title": "", "5-focus": ""})
        with numer.open_db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM calendar_content").fetchone()[0], 0)
        self.save("es", **{"7-color": "Azul"})
        self.save("es", action="reset")
        with numer.open_db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM calendar_content").fetchone()[0], 0)

    def test_languages_are_separate_and_unknown_rejected(self):
        self.login("liliana@example.com")
        self.save("en", **{"3-title": "Joy"})
        page = self.client.get("/settings/calendar?lang=es").get_data(as_text=True)
        self.assertNotIn('value="Joy"', page)
        self.assertEqual(self.save("xx", **{"3-title": "Nope"}).status_code, 400)
        self.assertEqual(self.client.get("/settings/calendar").status_code, 200)

    def test_requires_login(self):
        self.assertEqual(self.client.get("/settings/calendar").status_code, 302)

    def test_admin_edits_the_calendar_practitioners_guidance(self):
        lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        self.start(lead["id"], kind="numerology", lang="es")
        page = self.client.get("/settings/calendar").get_data(as_text=True)
        self.client.post("/logout", data={"csrf_token": token_from(page)})
        self.login("owner@example.com")
        editor = self.client.get(f"/leads/{lead['id']}/calendar").get_data(as_text=True)
        self.assertIn(f"/settings/calendar?lang=es&amp;for={lead['practitioner_id']}", editor)
        page = self.client.get(f"/settings/calendar?lang=es&for={lead['practitioner_id']}").get_data(as_text=True)
        self.assertIn("Liliana", page)
        self.client.post("/settings/calendar", data={"csrf_token": token_from(page), "lang": "es",
                                                     "for": str(lead["practitioner_id"]), "2-title": "Unión"})
        with numer.open_db() as connection:
            row = connection.execute("SELECT practitioner_id, title FROM calendar_content").fetchone()
        self.assertEqual((row["practitioner_id"], row["title"]), (lead["practitioner_id"], "Unión"))

    def test_practitioner_cannot_edit_someone_elses_guidance(self):
        self.login("liliana@example.com")
        page = self.client.get(f"/settings/calendar?for={self.admin_id}").get_data(as_text=True)
        self.assertNotIn("Owner's guidance", page)            # ignored: she edits her own
        self.client.post("/settings/calendar", data={"csrf_token": token_from(page), "lang": "es",
                                                     "for": str(self.admin_id), "2-title": "X"})
        with numer.open_db() as connection:
            self.assertEqual(connection.execute("SELECT practitioner_id FROM calendar_content").fetchone()[0],
                             self.practitioner_id)

    def test_admin_without_practitioner_chooses_one(self):
        self.login("owner@example.com")
        page = self.client.get("/settings/calendar").get_data(as_text=True)
        self.assertIn("Choose a practitioner", page)
        self.assertIn(f"/settings/calendar?for={self.practitioner_id}", page)
        self.assertEqual(self.client.post("/settings/calendar", data={
            "csrf_token": token_from(page), "lang": "es", "2-title": "X"}).status_code, 400)
