import datetime as dt
import json
import os
import re
import sqlite3
import unittest

from test_app import PASSWORD, AppTestCase, numer, token_from

import intake


def form_data(**overrides):
    data = {
        "full_name": "John Smith",
        "birth_date": "1990-07-15",
        "birth_city": "Edmonton",
        "birth_region": "Alberta",
        "birth_country": "Canada",
        "website": "",
    }
    data.update(overrides)
    return data


class IntakeTestCase(AppTestCase):
    def setUp(self):
        super().setUp()
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET slug = 'liliana' WHERE id = ?", (self.practitioner_id,))

    def submit(self, path="/", ip="10.1.0.1", **overrides):
        page = self.client.get(path, environ_base={"REMOTE_ADDR": ip}, follow_redirects=True)
        data = form_data(**overrides)
        data["csrf_token"] = token_from(page.get_data(as_text=True))
        return self.client.post("/report", data=data, environ_base={"REMOTE_ADDR": ip})

    def leads(self):
        with numer.open_db() as connection:
            return connection.execute("SELECT * FROM leads ORDER BY id").fetchall()

    def unlock(self, token, ip="10.1.0.1", **fields):
        page = self.client.get(f"/r/{token}")
        data = {"email": "john@example.com", "phone": "", "consent_report": "yes", "website": ""}
        data.update(fields)
        data["csrf_token"] = token_from(page.get_data(as_text=True))
        return self.client.post(f"/r/{token}/unlock", data=data, environ_base={"REMOTE_ADDR": ip})


class FormTest(IntakeTestCase):
    def test_home_shows_form(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="full_name"', response.data)
        self.assertIn(b'name="birth_country"', response.data)

    def test_submission_creates_lead_and_shows_teaser(self):
        response = self.submit()
        self.assertEqual(response.status_code, 302)
        lead = self.leads()[0]
        self.assertEqual(lead["status"], "started")
        self.assertEqual(lead["source"], "round_robin")  # no link: shared fairly
        self.assertEqual(json.loads(lead["numbers"])["life_path"], 5)
        page = self.client.get(response.headers["Location"])
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("The Adventurer", html)       # Life Path 5 shown free
        self.assertNotIn("The Achiever", html)       # Expression 8 still locked
        self.assertIn("Unlock my full report", html)
        self.assertEqual(page.headers["Cache-Control"], "no-store")

    def test_validation_errors(self):
        response = self.submit(full_name="", birth_date="2999-01-01", birth_city="", birth_country="")
        self.assertEqual(response.status_code, 400)
        html = response.get_data(as_text=True)
        for message in ("full birth name", "valid date of birth", "city you were born", "country you were born"):
            self.assertIn(message, html)
        self.assertEqual(self.leads(), [])

    def test_year_in_the_name_is_rejected(self):
        response = self.submit(full_name="Violetta Maria Stokowska 1965")
        self.assertEqual(response.status_code, 400)
        self.assertIn("without numbers", response.get_data(as_text=True))
        self.assertEqual(self.leads(), [])

    def test_recent_birth_date_needs_confirmation(self):
        yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        response = self.submit(birth_date=yesterday)
        self.assertEqual(response.status_code, 400)
        html = response.get_data(as_text=True)
        self.assertIn("less than a year ago", html)
        self.assertIn('name="recent_ok"', html)
        self.assertEqual(self.leads(), [])
        self.assertEqual(self.submit(birth_date=yesterday, recent_ok="yes").status_code, 302)  # a baby's report
        self.assertEqual(self.leads()[0]["birth_date"], yesterday)

    def test_one_year_ago_exactly_needs_no_confirmation(self):
        data, errors = intake.parse_birth_form(form_data(birth_date="2025-10-18"), today=dt.date(2026, 10, 18))
        self.assertEqual(errors, [])
        data, errors = intake.parse_birth_form(form_data(birth_date="2025-10-19"), today=dt.date(2026, 10, 18))
        self.assertTrue(data["confirm_recent"])
        self.assertEqual(intake.one_year_before(dt.date(2028, 2, 29)), dt.date(2027, 2, 28))

    def test_recent_date_confirmation_stays_ticked_while_other_fields_are_fixed(self):
        yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        html = self.submit(birth_date=yesterday, recent_ok="yes", birth_city="").get_data(as_text=True)
        self.assertIn('name="recent_ok" value="yes" checked', html)
        self.assertNotIn("less than a year ago", html)

    def test_country_typed_under_state_province_is_caught(self):
        response = self.submit(birth_city="Warszawa", birth_region="Poland", birth_country="Canada")
        self.assertEqual(response.status_code, 400)
        self.assertIn("looks like a country", response.get_data(as_text=True))
        self.assertEqual(self.leads(), [])
        self.assertEqual(self.submit(birth_city="Warszawa", birth_region="", birth_country="Poland").status_code, 302)

    def test_regions_that_share_a_country_name_are_accepted(self):
        self.assertEqual(self.submit(birth_city="Atlanta", birth_region="Georgia",
                                     birth_country="United States").status_code, 302)
        self.assertEqual(self.submit(birth_city="Cardiff", birth_region="Wales",
                                     birth_country="United Kingdom").status_code, 302)

    def test_form_asks_for_country_of_birth_without_autofill(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("Country of birth", html)
        self.assertIn("not where you live now", html)
        self.assertNotIn('autocomplete="country-name"', html)
        self.assertIn(f'max="{dt.date.today().isoformat()}"', html)

    def test_rejected_submission_is_not_cached(self):
        response = self.submit(birth_country="")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_non_latin_name_asks_for_latin_spelling(self):
        response = self.submit(full_name="Иван Петров")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Latin letters", response.get_data(as_text=True))

    def test_parallel_submissions_cannot_exceed_the_limit(self):
        import threading
        from werkzeug.test import Client
        results = []
        def worker():
            client = Client(numer.app)
            page = client.get("/", environ_base={"REMOTE_ADDR": "10.7.7.7"})
            token = token_from(page.get_data(as_text=True))
            data = form_data(csrf_token=token)
            response = client.post("/report", data=data, environ_base={"REMOTE_ADDR": "10.7.7.7"})
            results.append(response.status_code)
        threads = [threading.Thread(target=worker) for _ in range(intake.SUBMIT_LIMIT_PER_HOUR + 6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results.count(302), intake.SUBMIT_LIMIT_PER_HOUR)
        self.assertEqual(results.count(429), 6)

    def test_region_is_optional(self):
        self.assertEqual(self.submit(birth_region="").status_code, 302)

    def test_honeypot_discards_bots(self):
        response = self.submit(website="http://spam.example")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.leads(), [])

    def test_rate_limit_per_connection(self):
        for _ in range(intake.SUBMIT_LIMIT_PER_HOUR):
            self.assertEqual(self.submit(ip="10.9.9.9").status_code, 302)
        self.assertEqual(self.submit(ip="10.9.9.9").status_code, 429)
        self.assertEqual(self.submit(ip="10.9.9.10").status_code, 302)

    def test_ip_addresses_are_not_stored(self):
        self.submit(ip="203.0.113.7")
        with numer.open_db() as connection:
            stored = [row[0] for row in connection.execute("SELECT ip_hash FROM form_attempts")]
        self.assertTrue(stored)
        self.assertNotIn("203.0.113.7", "".join(stored))


class AttributionTest(IntakeTestCase):
    def test_practitioner_link_attributes_the_lead(self):
        page = self.client.get("/liliana")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"offered by <strong>Liliana</strong>", page.data)
        self.submit(path="/liliana")
        lead = self.leads()[0]
        self.assertEqual(lead["practitioner_id"], self.practitioner_id)
        self.assertEqual(lead["ref_slug"], "liliana")

    def test_attribution_survives_navigation_to_home(self):
        self.client.get("/liliana")
        self.submit(path="/")
        self.assertEqual(self.leads()[0]["practitioner_id"], self.practitioner_id)

    def test_unknown_slug_redirects_home_without_attribution(self):
        response = self.client.get("/nobody-here")
        self.assertEqual(response.status_code, 302)
        self.submit()
        self.assertEqual(self.leads()[0]["source"], "round_robin")

    def test_unknown_link_clears_an_earlier_attribution(self):
        self.client.get("/liliana")
        self.client.get("/someone-else")
        self.submit()
        lead = self.leads()[0]
        self.assertEqual((lead["source"], lead["ref_slug"]), ("round_robin", None))

    def test_inactive_practitioner_link_is_not_attributed(self):
        self.client.get("/liliana")
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET is_active = 0 WHERE id = ?", (self.practitioner_id,))
        self.submit()
        self.assertIsNone(self.leads()[0]["practitioner_id"])

    def test_admin_slug_is_never_a_practitioner_link(self):
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET slug = 'owner' WHERE id = ?", (self.admin_id,))
        self.assertEqual(self.client.get("/owner").status_code, 302)

    def test_reserved_and_malformed_slugs(self):
        for slug in ("api", "terms", "lil iana", "-x", "x-", "x", "a" * 41, "lil_iana"):
            with self.assertRaises(ValueError, msg=slug):
                intake.validate_slug(slug)
        self.assertEqual(intake.validate_slug(" Liliana "), "liliana")
        self.assertEqual(self.client.get("/api").status_code, 404)

    def test_existing_routes_are_not_shadowed(self):
        for path in ("/login", "/health", "/privacy", "/robots.txt"):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        self.assertEqual(self.client.get("/admin").status_code, 302)


class UnlockTest(IntakeTestCase):
    def token(self, path="/"):
        return self.submit(path=path).headers["Location"].rsplit("/", 1)[-1]

    def test_unlock_captures_lead_and_shows_full_report(self):
        token = self.token()
        response = self.unlock(token, phone="+1 (780) 555-0101", marketing_opt_in="yes")
        self.assertEqual(response.status_code, 302)
        lead = self.leads()[0]
        self.assertEqual((lead["status"], lead["email"], lead["phone"]),
                         ("captured", "john@example.com", "+1 (780) 555-0101"))
        self.assertEqual((lead["consent_report"], lead["marketing_opt_in"]), (1, 1))
        html = self.client.get(f"/r/{token}").get_data(as_text=True)
        self.assertIn("The Achiever", html)          # Expression 8 now visible
        self.assertNotIn("Unlock my full report", html)

    def test_marketing_is_opt_in(self):
        token = self.token()
        self.unlock(token)
        self.assertEqual(self.leads()[0]["marketing_opt_in"], 0)

    def test_unlock_requires_email_and_consent(self):
        token = self.token()
        response = self.unlock(token, email="not-an-email", consent_report="", phone="abc")
        self.assertEqual(response.status_code, 400)
        html = response.get_data(as_text=True)
        self.assertIn("valid email", html)
        self.assertIn("tick the box", html)
        self.assertIn("valid phone", html)
        self.assertEqual(self.leads()[0]["status"], "started")

    def test_consent_names_the_practitioner(self):
        token = self.token(path="/liliana")
        html = self.client.get(f"/r/{token}").get_data(as_text=True)
        self.assertIn("share my details and report with Liliana", html)
        self.assertIn("liliana@example.com", html)

    def test_unknown_and_expired_tokens_are_404(self):
        self.assertEqual(self.client.get("/r/not-a-real-token").status_code, 404)
        token = self.token()
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=intake.REPORT_LINK_DAYS + 1)).isoformat()
        with numer.open_db() as connection:
            connection.execute("UPDATE leads SET created_at = ?", (old,))
        self.assertEqual(self.client.get(f"/r/{token}").status_code, 404)

    def test_report_pages_are_never_indexed_even_after_launch(self):
        from unittest import mock
        token = self.token()
        with mock.patch.object(numer, "NOINDEX", False):
            self.assertNotIn("X-Robots-Tag", self.client.get("/").headers)
            self.assertEqual(self.client.get(f"/r/{token}").headers["X-Robots-Tag"], "noindex, nofollow")

    def test_not_active_number_is_explained(self):
        token = self.submit(full_name="Aia Eo").headers["Location"].rsplit("/", 1)[-1]
        self.unlock(token)
        html = self.client.get(f"/r/{token}").get_data(as_text=True)
        self.assertIn("Not active", html)

    def test_tokens_are_unguessable(self):
        token = self.token()
        self.assertGreaterEqual(len(token), 30)
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9_-]+", token))


class MigrationTest(unittest.TestCase):
    def test_existing_database_gains_new_columns_and_tables(self):
        path = os.path.join(os.path.dirname(numer.DB), "old.db")
        if os.path.exists(path):
            os.remove(path)
        connection = sqlite3.connect(path)
        connection.executescript("""
            CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
              password_hash TEXT NOT NULL, role TEXT NOT NULL, display_name TEXT NOT NULL DEFAULT '',
              invited_by INTEGER, is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
              last_login_at TEXT);
            INSERT INTO users (email, password_hash, role, created_at) VALUES ('a@b.c', 'x', 'admin', 't');
        """)
        connection.close()
        numer.initialize(path)
        numer.initialize(path)  # twice: must stay idempotent
        connection = sqlite3.connect(path)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            count = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        finally:
            connection.close()
            os.remove(path)
        self.assertIn("slug", columns)
        self.assertTrue(numer.REQUIRED_TABLES <= tables)
        self.assertEqual(count, 1)


class CommandsTest(IntakeTestCase):
    def test_add_practitioner_and_set_slug(self):
        runner = numer.app.test_cli_runner()
        result = runner.invoke(args=["add-practitioner", "--email", "maria@example.com", "--name", "Maria",
                                     "--slug", "Maria-N", "--password", PASSWORD])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(b"offered by <strong>Maria</strong>", self.client.get("/maria-n").data)
        duplicate = runner.invoke(args=["set-slug", "--email", "maria@example.com", "--slug", "liliana"])
        self.assertNotEqual(duplicate.exit_code, 0)
        self.assertIn("already in use", duplicate.output)
        reserved = runner.invoke(args=["set-slug", "--email", "maria@example.com", "--slug", "admin"])
        self.assertIn("reserved", reserved.output)
        ok = runner.invoke(args=["set-slug", "--email", "maria@example.com", "--slug", "maria"])
        self.assertEqual(ok.exit_code, 0, ok.output)

    def test_failed_add_practitioner_leaves_no_account(self):
        runner = numer.app.test_cli_runner()
        taken = runner.invoke(args=["add-practitioner", "--email", "new@example.com", "--name", "New",
                                    "--slug", "liliana", "--password", PASSWORD])
        self.assertIn("already in use", taken.output)
        with numer.open_db() as connection:
            self.assertIsNone(connection.execute(
                "SELECT id FROM users WHERE email = 'new@example.com'").fetchone())
        retry = runner.invoke(args=["add-practitioner", "--email", "new@example.com", "--name", "New",
                                    "--slug", "new-one", "--password", PASSWORD])
        self.assertEqual(retry.exit_code, 0, retry.output)

    def test_dashboard_shows_share_link(self):
        self.login("liliana@example.com")
        self.assertIn(b"localhost/liliana</a>", self.client.get("/dashboard").data)


if __name__ == "__main__":
    unittest.main()
