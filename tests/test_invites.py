import datetime as dt
import re

from test_app import PASSWORD, numer, token_from
from test_intake import IntakeTestCase

import invites


class InviteTestCase(IntakeTestCase):
    def create_invite(self, **fields):
        token = token_from(self.client.get("/invite").get_data(as_text=True))
        data = {"csrf_token": token, "name": "", "email": ""}
        data.update(fields)
        html = self.client.post("/invite", data=data).get_data(as_text=True)
        return re.search(r"/join/([A-Za-z0-9_-]+)", html).group(1)

    def join(self, client, token, **fields):
        page = client.get(f"/join/{token}")
        if page.status_code != 200:  # invalid invite: take a CSRF token from any form page
            page = client.get("/")
        data = {"csrf_token": token_from(page.get_data(as_text=True)), "display_name": "Maria Santos",
                "email": "maria@example.com", "slug": "maria", "password": PASSWORD,
                "confirm_password": PASSWORD, "accept_terms": "yes"}
        data.update(fields)
        return client.post(f"/join/{token}", data=data, environ_base={"REMOTE_ADDR": "10.3.3.3"})


class InviteTest(InviteTestCase):
    def test_invite_page_requires_sign_in(self):
        self.assertEqual(self.client.get("/invite").status_code, 302)

    def test_join_creates_practitioner_with_referral_and_signs_in(self):
        self.login("liliana@example.com")
        token = self.create_invite(name="Maria Santos", email="maria@example.com")
        guest = numer.app.test_client()
        page = guest.get(f"/join/{token}").get_data(as_text=True)
        self.assertIn("Liliana invited you", page)
        self.assertIn('value="maria@example.com"', page)
        response = self.join(guest, token)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/dashboard"))
        with numer.open_db() as connection:
            row = connection.execute("SELECT role, slug, invited_by FROM users WHERE email = 'maria@example.com'").fetchone()
        self.assertEqual((row["role"], row["slug"], row["invited_by"]), ("practitioner", "maria", self.practitioner_id))
        self.assertIn(b"Welcome back, Maria Santos", guest.get("/dashboard").data)
        self.assertIn(b"offered by <strong>Maria Santos</strong>", numer.app.test_client().get("/maria").data)
        referrals = self.client.get("/invite").get_data(as_text=True)
        self.assertIn("Maria Santos", referrals)
        self.assertIn("1</strong> practitioner referred", self.client.get("/dashboard").get_data(as_text=True))

    def test_invite_is_single_use(self):
        self.login("liliana@example.com")
        token = self.create_invite()
        self.assertEqual(self.join(numer.app.test_client(), token).status_code, 302)
        second = self.join(numer.app.test_client(), token, email="other@example.com", slug="other")
        self.assertEqual(second.status_code, 404)
        self.assertIn(b"isn't valid", second.data)

    def test_expired_and_revoked_invites_fail(self):
        self.login("liliana@example.com")
        expired = self.create_invite()
        past = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()
        with numer.open_db() as connection:
            connection.execute("UPDATE invites SET expires_at = ? WHERE token = ?", (past, expired))
        self.assertEqual(numer.app.test_client().get(f"/join/{expired}").status_code, 404)
        revoked = self.create_invite()
        with numer.open_db() as connection:
            invite_id = connection.execute("SELECT id FROM invites WHERE token = ?", (revoked,)).fetchone()[0]
        csrf = token_from(self.client.get("/invite").get_data(as_text=True))
        self.assertEqual(self.client.post(f"/invite/{invite_id}/revoke", data={"csrf_token": csrf}).status_code, 302)
        self.assertEqual(numer.app.test_client().get(f"/join/{revoked}").status_code, 404)

    def test_cannot_revoke_someone_elses_invite(self):
        self.login("liliana@example.com")
        token = self.create_invite()
        with numer.open_db() as connection:
            invite_id = connection.execute("SELECT id FROM invites WHERE token = ?", (token,)).fetchone()[0]
        owner = numer.app.test_client()
        page = owner.get("/login")
        owner.post("/login", data={"email": "owner@example.com", "password": PASSWORD,
                                   "csrf_token": token_from(page.get_data(as_text=True))})
        csrf = token_from(owner.get("/invite").get_data(as_text=True))
        self.assertEqual(owner.post(f"/invite/{invite_id}/revoke", data={"csrf_token": csrf}).status_code, 404)

    def test_join_validation(self):
        self.login("liliana@example.com")
        token = self.create_invite()
        guest = numer.app.test_client()
        response = self.join(guest, token, display_name="", email="bad", slug="admin",
                             password="short", accept_terms="")
        self.assertEqual(response.status_code, 400)
        html = response.get_data(as_text=True)
        for text in ("name your clients", "valid email", "reserved", "at least 12", "accept the terms"):
            self.assertIn(text, html)
        taken = self.join(guest, token, slug="liliana")
        self.assertIn("already in use", taken.get_data(as_text=True))
        with numer.open_db() as connection:
            self.assertIsNone(connection.execute("SELECT used_by FROM invites WHERE token = ?", (token,)).fetchone()[0])

    def test_open_invite_limit(self):
        self.login("liliana@example.com")
        with numer.open_db() as connection:
            for n in range(invites.MAX_OPEN_INVITES):
                connection.execute("INSERT INTO invites (token, inviter_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                                   (f"t{n}", self.practitioner_id, numer.iso(numer.now_utc()), "2999-01-01T00:00:00+00:00"))
        token = token_from(self.client.get("/invite").get_data(as_text=True))
        response = self.client.post("/invite", data={"csrf_token": token})
        self.assertEqual(response.status_code, 400)

    def test_inactive_inviter_link_stops_working(self):
        self.login("liliana@example.com")
        token = self.create_invite()
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET is_active = 0 WHERE id = ?", (self.practitioner_id,))
        self.assertEqual(numer.app.test_client().get(f"/join/{token}").status_code, 404)
