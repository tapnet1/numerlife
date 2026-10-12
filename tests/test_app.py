import os
import re
import sqlite3
import tempfile
import unittest

_TMP = tempfile.TemporaryDirectory()
os.environ["NUMER_DB"] = os.path.join(_TMP.name, "test.db")
os.environ["NUMER_SECRET_KEY"] = "test-secret"
os.environ["NUMER_BASE_URL"] = "http://localhost"
os.environ["NUMER_NOINDEX"] = "1"

import app as numer  # noqa: E402

PASSWORD = "correct horse battery"


def tearDownModule():
    _TMP.cleanup()


def token_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "CSRF token not found in page"
    return match.group(1)


class AppTestCase(unittest.TestCase):
    def setUp(self):
        if os.path.exists(numer.DB):
            os.remove(numer.DB)
        numer.initialize()
        numer.app.config["TESTING"] = True
        self.client = numer.app.test_client()
        self.admin_id = numer.create_user("owner@example.com", PASSWORD, "admin", "Owner")
        self.practitioner_id = numer.create_user("liliana@example.com", PASSWORD, "practitioner",
                                                 "Liliana", invited_by=self.admin_id)

    def login(self, email, password=PASSWORD, ip="10.0.0.1"):
        page = self.client.get("/login")
        return self.client.post("/login", data={
            "email": email, "password": password, "csrf_token": token_from(page.get_data(as_text=True)),
        }, environ_base={"REMOTE_ADDR": ip})


class PublicPagesTest(AppTestCase):
    def test_health_ok(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_health_reports_uninitialized_database(self):
        with numer.open_db() as connection:
            connection.execute("DROP TABLE login_attempts")
        self.assertEqual(self.client.get("/health").status_code, 503)

    def test_home_is_public(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'property="og:title"', response.data)
        self.assertIn(b'name="description"', response.data)

    def test_login_page_and_css(self):
        self.assertEqual(self.client.get("/login").status_code, 200)
        self.assertEqual(self.client.get("/static/css/app.css").status_code, 200)
        self.assertEqual(self.client.get("/static/favicon.svg").status_code, 200)

    def test_noindex_everywhere(self):
        response = self.client.get("/")
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn(b'name="robots" content="noindex, nofollow"', response.data)
        self.assertIn(b"Disallow: /", self.client.get("/robots.txt").data)

    def test_security_headers(self):
        headers = self.client.get("/").headers
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_www_redirects_to_canonical_host(self):
        response = self.client.get("/login?next=/admin", base_url="http://www.localhost")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], "http://localhost/login?next=/admin")
        self.assertEqual(self.client.get("/login").status_code, 200)

    def test_unknown_page_is_404(self):
        self.assertEqual(self.client.get("/no-such-page/deeper").status_code, 404)


class AuthTest(AppTestCase):
    def test_protected_pages_redirect_when_logged_out(self):
        for path in ("/admin", "/dashboard"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302, path)
            self.assertIn("/login", response.headers["Location"])

    def test_login_success_and_logout(self):
        response = self.login("owner@example.com")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/dashboard"))
        self.assertEqual(self.client.get("/dashboard").status_code, 200)
        page = self.client.get("/dashboard").get_data(as_text=True)
        self.client.post("/logout", data={"csrf_token": token_from(page)})
        self.assertEqual(self.client.get("/dashboard").status_code, 302)

    def test_email_is_case_insensitive(self):
        self.assertEqual(self.login("OWNER@example.com").status_code, 302)

    def test_wrong_password_and_unknown_email_look_the_same(self):
        wrong = self.login("owner@example.com", "wrong password!")
        unknown = self.login("nobody@example.com", "wrong password!")
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(unknown.status_code, 401)
        self.assertIn(b"Email or password is incorrect", wrong.data)
        self.assertIn(b"Email or password is incorrect", unknown.data)

    def test_post_without_csrf_token_is_rejected(self):
        self.client.get("/login")
        response = self.client.post("/login", data={"email": "owner@example.com", "password": PASSWORD})
        self.assertEqual(response.status_code, 400)

    def test_session_cookie_flags(self):
        self.login("owner@example.com")
        cookie = self.client.get_cookie("numer_session")
        self.assertIsNotNone(cookie)
        self.assertTrue(cookie.http_only)
        self.assertEqual(cookie.same_site, "Lax")

    def test_session_id_changes_on_login(self):
        self.client.get("/login")
        before = self.client.get_cookie("numer_session").value
        self.login("owner@example.com")
        self.assertNotEqual(before, self.client.get_cookie("numer_session").value)

    def test_open_redirect_is_blocked(self):
        page = self.client.get("/login")
        response = self.client.post("/login", data={
            "email": "owner@example.com", "password": PASSWORD,
            "csrf_token": token_from(page.get_data(as_text=True)), "next": "//evil.example.com/x",
        })
        self.assertTrue(response.headers["Location"].endswith("/dashboard"))

    def test_next_path_is_honoured(self):
        self.client.get("/admin")
        page = self.client.get("/login?next=/admin")
        response = self.client.post("/login", data={
            "email": "owner@example.com", "password": PASSWORD,
            "csrf_token": token_from(page.get_data(as_text=True)), "next": "/admin",
        })
        self.assertTrue(response.headers["Location"].endswith("/admin"))

    def test_inactive_user_cannot_log_in(self):
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET is_active = 0 WHERE id = ?", (self.practitioner_id,))
        self.assertEqual(self.login("liliana@example.com").status_code, 401)


class RolesTest(AppTestCase):
    def test_admin_can_open_admin(self):
        self.login("owner@example.com")
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"liliana@example.com", response.data)

    def test_practitioner_cannot_open_admin(self):
        self.login("liliana@example.com")
        self.assertEqual(self.client.get("/admin").status_code, 403)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_referral_is_stored(self):
        with numer.open_db() as connection:
            row = connection.execute("SELECT invited_by FROM users WHERE id = ?",
                                     (self.practitioner_id,)).fetchone()
        self.assertEqual(row["invited_by"], self.admin_id)


class RateLimitTest(AppTestCase):
    def test_account_locks_after_repeated_failures(self):
        for _ in range(numer.LOGIN_MAX_FAILURES_PER_ACCOUNT):
            self.assertEqual(self.login("owner@example.com", "bad password!!", ip="10.0.0.2").status_code, 401)
        # Even the right password is refused while locked, from another IP too.
        self.assertEqual(self.login("owner@example.com", ip="10.0.0.3").status_code, 429)
        # Other accounts are unaffected.
        self.assertEqual(self.login("liliana@example.com", ip="10.0.0.4").status_code, 302)

    def test_login_addresses_are_stored_hashed(self):
        self.login("owner@example.com", "bad password!!", ip="198.51.100.23")
        with numer.open_db() as connection:
            stored = [row[0] for row in connection.execute("SELECT ip FROM login_attempts")]
        self.assertTrue(stored)
        self.assertNotIn("198.51.100.23", "".join(stored))

    def test_ip_locks_after_many_failures(self):
        for i in range(numer.LOGIN_MAX_FAILURES_PER_IP):
            self.login(f"user{i}@example.com", "bad password!!", ip="10.0.0.9")
        self.assertEqual(self.login("liliana@example.com", ip="10.0.0.9").status_code, 429)


class DatabaseTest(AppTestCase):
    def test_initialize_is_idempotent(self):
        numer.initialize()
        numer.initialize()
        with numer.open_db() as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            count = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        self.assertTrue(numer.REQUIRED_TABLES <= tables)
        self.assertEqual(count, 2)

    def test_passwords_are_hashed(self):
        with numer.open_db() as connection:
            stored = connection.execute("SELECT password_hash FROM users WHERE id = ?",
                                        (self.admin_id,)).fetchone()[0]
        self.assertNotIn(PASSWORD, stored)

    def test_create_user_validation(self):
        with self.assertRaises(ValueError):
            numer.create_user("short@example.com", "short", "practitioner")
        with self.assertRaises(ValueError):
            numer.create_user("not-an-email", PASSWORD, "practitioner")
        with self.assertRaises(ValueError):
            numer.create_user("x@example.com", PASSWORD, "superuser")
        with self.assertRaises(sqlite3.IntegrityError):
            numer.create_user("Owner@Example.com", PASSWORD, "practitioner")


class CommandsTest(AppTestCase):
    def test_create_owner_command(self):
        runner = numer.app.test_cli_runner()
        result = runner.invoke(args=["create-owner", "--email", "marco@example.com", "--name", "Marco",
                                     "--password", PASSWORD])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.login("marco@example.com").status_code, 302)

    def test_create_owner_rejects_duplicates(self):
        runner = numer.app.test_cli_runner()
        result = runner.invoke(args=["create-owner", "--email", "owner@example.com", "--name", "",
                                     "--password", PASSWORD])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("already exists", result.output)

    def test_restore_db_while_a_connection_is_open(self):
        backup_name = os.path.basename(numer.DB) + ".20260101T000000Z.bak"
        backup_path = os.path.join(os.path.dirname(numer.DB), backup_name)
        live = sqlite3.connect(numer.DB)
        snapshot = sqlite3.connect(backup_path)
        live.backup(snapshot)
        snapshot.close()
        numer.create_user("later@example.com", PASSWORD, "practitioner")
        worker = sqlite3.connect(numer.DB)  # like a running Passenger worker
        try:
            result = numer.app.test_cli_runner().invoke(args=["restore-db", backup_name])
            self.assertEqual(result.exit_code, 0, result.output)
            emails = {row[0] for row in worker.execute("SELECT email FROM users")}
            self.assertNotIn("later@example.com", emails)
            self.assertIn("owner@example.com", emails)
            saved = [n for n in os.listdir(os.path.dirname(numer.DB)) if ".before-restore." in n]
            self.assertEqual(len(saved), 1)
        finally:
            worker.close()
            live.close()
            for name in os.listdir(os.path.dirname(numer.DB)):
                if name.endswith(".bak"):
                    os.remove(os.path.join(os.path.dirname(numer.DB), name))

    def test_restore_db_refuses_other_files(self):
        for name in ("../etc/passwd", "other.db", os.path.basename(numer.DB) + ".missing.bak"):
            result = numer.app.test_cli_runner().invoke(args=["restore-db", name])
            self.assertNotEqual(result.exit_code, 0, name)

    def test_init_db_command(self):
        result = numer.app.test_cli_runner().invoke(args=["init-db"])
        self.assertEqual(result.exit_code, 0, result.output)


if __name__ == "__main__":
    unittest.main()


class ErrorLogTest(AppTestCase):
    def test_unhandled_errors_are_recorded(self):
        log_path = os.path.join(_TMP.name, "errors.log")
        numer.ERROR_LOG = numer.Path(log_path)
        with numer.app.test_request_context("/admin/email/test", method="POST"):
            try:
                raise RuntimeError("kaboom")
            except RuntimeError as exc:
                body, status = numer._server_error(exc)
        self.assertEqual(status, 500)
        with open(log_path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("POST /admin/email/test", text)
        self.assertIn("RuntimeError: kaboom", text)
