import os
import re
from unittest import mock

from test_app import PASSWORD, numer, token_from
from test_emails import SMTP_ENV, EmailTestCase

import password_reset

NEW_PASSWORD = "a brand new passphrase"


class PasswordResetTest(EmailTestCase):
    def request_reset(self, email="liliana@example.com", ip="10.5.0.1", client=None, **fields):
        client = client or self.client
        page = client.get("/login/forgot", environ_base={"REMOTE_ADDR": ip})
        data = {"email": email, "website": "", "csrf_token": token_from(page.get_data(as_text=True))}
        data.update(fields)
        return client.post("/login/forgot", data=data, environ_base={"REMOTE_ADDR": ip})

    def reset_emails(self):
        return [row for row in self.outbox() if row["kind"] == "password_reset"]

    def link_from(self, row):
        return re.search(r"/login/reset/[A-Za-z0-9_.\-]+", row["body_text"]).group(0)

    def set_new(self, link, password=NEW_PASSWORD, confirm=None, ip="10.5.0.1"):
        page = self.client.get(link, environ_base={"REMOTE_ADDR": ip})
        token = token_from(page.get_data(as_text=True)) if page.status_code == 200 else \
            token_from(self.client.get("/login/forgot").get_data(as_text=True))
        return self.client.post(link, data={"new_password": password, "confirm_password": confirm or password,
                                            "csrf_token": token}, environ_base={"REMOTE_ADDR": ip})

    def test_login_page_links_to_forgot_password(self):
        self.assertIn(b'href="/login/forgot"', self.client.get("/login").data)

    def test_full_reset_flow(self):
        response = self.request_reset()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"If that email belongs to an account", response.data)
        rows = self.reset_emails()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["to_addr"], "liliana@example.com")
        self.assertEqual(rows[0]["user_id"], self.practitioner_id)
        link = self.link_from(rows[0])
        page = self.client.get(link)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(page.headers["Cache-Control"], "no-store")
        self.assertIn("noindex", page.headers["X-Robots-Tag"])
        response = self.set_new(link)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])
        self.assertEqual(self.login("liliana@example.com").status_code, 401)  # old password gone
        self.assertEqual(self.login("liliana@example.com", NEW_PASSWORD).status_code, 302)

    def test_link_works_only_once(self):
        self.request_reset()
        link = self.link_from(self.reset_emails()[0])
        self.assertEqual(self.set_new(link).status_code, 302)
        again = self.set_new(link, password="yet another passphrase")
        self.assertEqual(again.status_code, 404)
        self.assertEqual(self.client.get(link).status_code, 404)
        self.assertEqual(self.login("liliana@example.com", NEW_PASSWORD).status_code, 302)

    def test_older_link_stops_working_after_password_changes(self):
        self.request_reset()
        link = self.link_from(self.reset_emails()[0])
        self.login("liliana@example.com")
        page = self.client.get("/settings/password")
        self.client.post("/settings/password", data={
            "current_password": PASSWORD, "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD,
            "csrf_token": token_from(page.get_data(as_text=True))})
        self.assertEqual(numer.app.test_client().get(link).status_code, 404)

    def test_expired_and_tampered_links_fail(self):
        with numer.app.app_context():
            with numer.open_db() as connection:
                user = connection.execute("SELECT * FROM users WHERE id = ?", (self.practitioner_id,)).fetchone()
            token = password_reset.make_token(user)
        self.assertEqual(self.client.get(f"/login/reset/{token}x").status_code, 404)
        with mock.patch("itsdangerous.timed.time.time", return_value=__import__("time").time() + 3700):
            self.assertEqual(self.client.get(f"/login/reset/{token}").status_code, 404)
        self.assertEqual(self.client.get(f"/login/reset/{token}").status_code, 200)

    def test_unknown_or_inactive_email_gets_same_answer_and_no_email(self):
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET is_active = 0 WHERE id = ?", (self.practitioner_id,))
        known = self.request_reset("liliana@example.com")
        unknown = self.request_reset("nobody@example.com")
        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known.data, unknown.data)
        self.assertEqual(self.reset_emails(), [])

    def test_validation_errors_keep_password(self):
        self.request_reset()
        link = self.link_from(self.reset_emails()[0])
        self.assertEqual(self.set_new(link, password="short").status_code, 400)
        self.assertEqual(self.set_new(link, confirm="something different!").status_code, 400)
        self.assertEqual(self.login("liliana@example.com").status_code, 302)

    def test_rate_limits(self):
        for _ in range(password_reset.EMAILS_PER_ACCOUNT_PER_HOUR + 1):
            self.request_reset(ip=f"10.6.0.{_}")
        self.assertEqual(len(self.reset_emails()), password_reset.EMAILS_PER_ACCOUNT_PER_HOUR)
        for _ in range(password_reset.REQUESTS_PER_IP_PER_HOUR):
            self.request_reset("nobody@example.com", ip="10.7.0.1")
        self.assertEqual(self.request_reset("nobody@example.com", ip="10.7.0.1").status_code, 429)

    def test_honeypot_sends_nothing(self):
        self.assertEqual(self.request_reset(website="spam").status_code, 200)
        self.assertEqual(self.reset_emails(), [])

    def test_reset_clears_login_lockout(self):
        for _ in range(5):
            self.login("liliana@example.com", "wrong password!!", ip="10.8.0.1")
        self.assertEqual(self.login("liliana@example.com", ip="10.8.0.2").status_code, 429)
        self.request_reset()
        self.assertEqual(self.set_new(self.link_from(self.reset_emails()[0])).status_code, 302)
        self.assertEqual(self.login("liliana@example.com", NEW_PASSWORD, ip="10.8.0.2").status_code, 302)

    def test_no_email_when_smtp_not_configured(self):
        with mock.patch.dict(os.environ, {"NUMER_SMTP_HOST": ""}):
            self.assertEqual(self.request_reset().status_code, 200)
        self.assertEqual(self.reset_emails(), [])

    def test_team_member_can_reset(self):
        numer.create_user("frank@example.com", PASSWORD, "practitioner", "Frank")
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET team_of = ? WHERE email = 'frank@example.com'",
                               (self.practitioner_id,))
        self.request_reset("frank@example.com")
        self.assertEqual(len(self.reset_emails()), 1)

    def test_signed_in_user_is_sent_to_change_password(self):
        self.login("liliana@example.com")
        response = self.client.get("/login/forgot")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/settings/password", response.headers["Location"])

    def test_admin_test_send_includes_reset_email(self):
        self.login("owner@example.com")
        page = self.client.get("/admin")
        with mock.patch("mailer.connect", return_value=self.smtp):
            self.client.post("/admin/email/test", data={"kinds": "password_reset", "to": "owner@example.com",
                                                         "csrf_token": token_from(page.get_data(as_text=True))})
        rows = [row for row in self.outbox() if row["kind"] == "test:password_reset"]
        self.assertEqual(len(rows), 1)
        self.assertNotIn("/login/reset/", rows[0]["body_text"])


assert SMTP_ENV  # imported for the configured-mail environment of EmailTestCase
