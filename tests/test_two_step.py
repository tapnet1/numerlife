import os
import time
from unittest import mock

from test_app import PASSWORD, AppTestCase, numer, token_from

import two_step

RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890", RFC 6238 test key


class CodeTest(AppTestCase):
    def test_rfc_6238_vectors(self):
        self.assertEqual(two_step.code_at(RFC_SECRET, 59 // 30), "287082")
        self.assertEqual(two_step.code_at(RFC_SECRET, 1111111109 // 30), "081804")
        self.assertEqual(two_step.code_at(RFC_SECRET, 1234567890 // 30), "005924")

    def test_window_and_replay(self):
        now = 1_700_000_000
        step = now // 30
        code = two_step.code_at(RFC_SECRET, step - 1)
        self.assertEqual(two_step.matching_step(RFC_SECRET, code, now=now), step - 1)
        self.assertIsNone(two_step.matching_step(RFC_SECRET, code, after=step - 1, now=now))
        self.assertIsNone(two_step.matching_step(RFC_SECRET, two_step.code_at(RFC_SECRET, step - 3), now=now))
        self.assertIsNone(two_step.matching_step(RFC_SECRET, "12ab", now=now))


class TwoStepFlowTest(AppTestCase):
    def setUp(self):
        super().setUp()
        key = mock.patch.dict(os.environ, {"NUMER_CREDENTIAL_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="})
        key.start()
        self.addCleanup(key.stop)

    def setup_two_step(self):
        self.login("liliana@example.com")
        page = self.client.get("/settings/two-step")
        self.assertEqual(page.headers["Cache-Control"], "no-store")
        html = page.get_data(as_text=True)
        self.assertIn("data:image/svg+xml", html)
        with self.client.session_transaction() as session:
            secret = session["two_step_setup"]
        bad = self.client.post("/settings/two-step/on", data={"csrf_token": token_from(html), "code": "000000"})
        self.assertFalse(self.is_on())
        code = two_step.code_at(secret, int(time.time() // 30))
        self.client.post("/settings/two-step/on", data={"csrf_token": token_from(html), "code": code})
        self.assertTrue(self.is_on())
        self.client.post("/logout", data={"csrf_token": token_from(html)})
        return secret, bad

    def is_on(self):
        with numer.open_db() as connection:
            row = connection.execute("SELECT totp_enabled_at, totp_secret FROM users WHERE id = ?",
                                     (self.practitioner_id,)).fetchone()
        if row["totp_secret"]:
            self.assertFalse(row["totp_secret"][:4].isdigit())
        return bool(row["totp_enabled_at"])

    def code_page(self):
        return self.client.get("/login/code").get_data(as_text=True)

    def test_sign_in_needs_the_code_once(self):
        secret, _ = self.setup_two_step()
        response = self.login("liliana@example.com")
        self.assertEqual(response.headers["Location"], "/login/code")
        self.assertEqual(self.client.get("/dashboard").status_code, 302)  # not signed in yet
        html = self.code_page()
        wrong = self.client.post("/login/code", data={"csrf_token": token_from(html), "code": "000001"})
        self.assertEqual(wrong.status_code, 401)
        # a code already used to turn two-step on does not work again; wait for a fresh one
        later = time.time() + 30
        code = two_step.code_at(secret, int(later // 30))
        with mock.patch("two_step.time.time", return_value=later):
            ok = self.client.post("/login/code", data={"csrf_token": token_from(html), "code": code})
            self.assertEqual(ok.status_code, 302)
            self.assertEqual(self.client.get("/dashboard").status_code, 200)
        # replaying the same code in a new sign-in fails
        self.client.post("/logout", data={"csrf_token": token_from(html)})
        self.login("liliana@example.com")
        html = self.code_page()
        with mock.patch("two_step.time.time", return_value=later):
            again = self.client.post("/login/code", data={"csrf_token": token_from(html), "code": code})
        self.assertEqual(again.status_code, 401)

    def test_pending_sign_in_expires(self):
        self.setup_two_step()
        self.login("liliana@example.com")
        with mock.patch("two_step.time.time", return_value=time.time() + 400):
            response = self.client.get("/login/code")
        self.assertEqual(response.headers["Location"], "/login")

    def test_admin_turns_it_off_for_a_lost_phone(self):
        self.setup_two_step()
        self.login("owner@example.com")
        page = self.client.get("/admin").get_data(as_text=True)
        self.assertIn("Lost phone", page)
        self.client.post("/admin/accounts/two-step-off",
                         data={"csrf_token": token_from(page), "user_id": self.practitioner_id})
        self.assertFalse(self.is_on())

    def test_practitioner_cannot_use_admin_reset_and_cli_works(self):
        self.setup_two_step()
        self.login("liliana@example.com")
        html = self.code_page()
        self.assertIn("Enter your code", html)
        result = numer.app.test_cli_runner().invoke(args=["reset-two-step", "liliana@example.com"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertFalse(self.is_on())
        self.assertEqual(self.login("liliana@example.com").headers["Location"], "/dashboard")
        page = self.client.get("/dashboard").get_data(as_text=True)
        response = self.client.post("/admin/accounts/two-step-off",
                                    data={"csrf_token": token_from(page), "user_id": self.admin_id})
        self.assertEqual(response.status_code, 403)

    def test_settings_page_links_to_setup(self):
        self.login("liliana@example.com")
        self.assertIn("Set up two-step sign-in", self.client.get("/settings/password").get_data(as_text=True))
