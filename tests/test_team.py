import json

from test_app import PASSWORD, numer, token_from
from test_intake import IntakeTestCase


class TeamTestCase(IntakeTestCase):
    def setUp(self):
        super().setUp()
        self.frank_id = numer.create_user("frank@example.com", PASSWORD, "practitioner", "Frank",
                                          invited_by=self.admin_id, slug="broccoli")

    def admin_post(self, path, **data):
        page = self.client.get("/admin")
        data["csrf_token"] = token_from(page.get_data(as_text=True))
        return self.client.post(path, data=data, follow_redirects=True)

    def lead_owner(self, lead_id):
        with numer.open_db() as connection:
            return connection.execute("SELECT practitioner_id FROM leads WHERE id = ?", (lead_id,)).fetchone()[0]


class AdminAccountsTest(TeamTestCase):
    def test_admin_adds_practitioner_with_chosen_password(self):
        with numer.open_db() as connection:
            connection.execute("INSERT INTO invites (token, inviter_id, name, email, created_at, expires_at)"
                               " VALUES ('tok', ?, 'Lili', 'lili@example.com', '2026-01-01', '2999-01-01')",
                               (self.admin_id,))
        self.login("owner@example.com")
        html = self.admin_post("/admin/accounts/create", display_name="Liliana Ferrer", email="lili@example.com",
                               slug="liliana-ferrer", password=PASSWORD).get_data(as_text=True)
        self.assertIn("Added Liliana Ferrer", html)
        self.client.post("/logout", data={"csrf_token": token_from(html)})
        self.assertEqual(self.login("lili@example.com").status_code, 302)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)
        with numer.open_db() as connection:
            invite = connection.execute("SELECT used_by FROM invites WHERE token = 'tok'").fetchone()
        self.assertIsNotNone(invite["used_by"])  # her open invite is closed

    def test_validation_and_admin_only(self):
        self.login("owner@example.com")
        html = self.admin_post("/admin/accounts/create", display_name="X", email="bad", slug="admin",
                               password="short").get_data(as_text=True)
        for text in ("valid email", "reserved", "12 characters"):
            self.assertIn(text, html)
        self.client.post("/logout", data={"csrf_token": token_from(html)})
        self.login("liliana@example.com")
        page = self.client.get("/dashboard").get_data(as_text=True)
        for path in ("/admin/accounts/create", "/admin/accounts/password", "/admin/accounts/team"):
            self.assertEqual(self.client.post(path, data={"csrf_token": token_from(page)}).status_code, 403, path)

    def test_admin_sets_password(self):
        self.login("owner@example.com")
        self.admin_post("/admin/accounts/password", user_id=self.frank_id, password="a brand new password")
        self.client.post("/logout", data={"csrf_token": token_from(self.client.get("/admin").get_data(as_text=True))})
        self.assertEqual(self.login("frank@example.com", password="a brand new password").status_code, 302)


class TeamMemberTest(TeamTestCase):
    def make_team(self):
        self.login("owner@example.com")
        html = self.admin_post("/admin/accounts/team", user_id=self.frank_id,
                               owner_id=self.practitioner_id).get_data(as_text=True)
        self.client.post("/logout", data={"csrf_token": token_from(html)})
        return html

    def test_team_conversion_moves_leads_and_removes_link_and_round_robin(self):
        response = self.submit("/")          # round robin: Frank or Liliana
        token = response.headers["Location"].rsplit("/", 1)[-1]
        self.unlock(token)
        with numer.open_db() as connection:
            connection.execute("UPDATE leads SET practitioner_id = ?", (self.frank_id,))
        html = self.make_team()
        self.assertIn("Frank is now on Liliana&#39;s team; 1 lead moved to Liliana", html)
        self.assertEqual(self.lead_owner(self.leads()[0]["id"]), self.practitioner_id)
        with numer.open_db() as connection:
            frank = connection.execute("SELECT slug, round_robin, team_of FROM users WHERE id = ?",
                                       (self.frank_id,)).fetchone()
        self.assertEqual((frank["slug"], frank["round_robin"], frank["team_of"]), (None, 0, self.practitioner_id))
        # new link-less visitors never go to a team member
        for ip in ("10.2.0.1", "10.2.0.2", "10.2.0.3"):
            self.submit("/", ip=ip)
        self.assertTrue(all(lead["practitioner_id"] == self.practitioner_id for lead in self.leads()))

    def test_team_member_works_on_the_practitioners_leads(self):
        response = self.submit("/liliana")
        self.unlock(response.headers["Location"].rsplit("/", 1)[-1])
        lead_id = self.leads()[0]["id"]
        self.make_team()
        self.login("frank@example.com")
        dashboard = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("Liliana's team", dashboard)
        self.assertIn("Liliana's link", dashboard)
        self.assertNotIn("add your booking link", dashboard)
        self.assertIn("localhost/liliana", dashboard)         # shares her link
        self.assertNotIn("Earnings", dashboard)
        self.assertNotIn('href="/invite"', dashboard)
        self.assertEqual(self.client.get(f"/leads/{lead_id}").status_code, 200)
        page = self.client.get(f"/leads/{lead_id}").get_data(as_text=True)
        self.client.post(f"/leads/{lead_id}", data={"csrf_token": token_from(page), "stage": "contacted", "notes": "Called"})
        with numer.open_db() as connection:
            self.assertEqual(connection.execute("SELECT stage FROM leads WHERE id = ?", (lead_id,)).fetchone()[0],
                             "contacted")
        self.assertEqual(self.client.get("/invite").status_code, 403)
        self.assertEqual(self.client.get("/settings/profile").status_code, 302)  # to password page
        self.assertEqual(self.client.get("/broccoli").status_code, 302)          # old link no longer works

    def test_team_member_gets_lead_alerts(self):
        import emails, notify
        self.make_team()
        response = self.submit("/liliana")
        self.unlock(response.headers["Location"].rsplit("/", 1)[-1])
        with numer.open_db() as connection:
            pushes = [r[0] for r in connection.execute("SELECT user_id FROM push_outbox")]
        self.assertEqual(sorted(pushes), sorted([self.practitioner_id, self.frank_id]))

    def test_remove_from_team(self):
        self.make_team()
        self.login("owner@example.com")
        self.admin_post("/admin/accounts/team", user_id=self.frank_id, owner_id="")
        with numer.open_db() as connection:
            self.assertIsNone(connection.execute("SELECT team_of FROM users WHERE id = ?",
                                                 (self.frank_id,)).fetchone()[0])


class ReassignTest(TeamTestCase):
    def test_admin_reassigns_a_lead(self):
        response = self.submit("/broccoli")
        self.unlock(response.headers["Location"].rsplit("/", 1)[-1])
        lead_id = self.leads()[0]["id"]
        self.login("owner@example.com")
        page = self.client.get(f"/leads/{lead_id}").get_data(as_text=True)
        self.assertIn("Reassign", page)
        self.client.post(f"/leads/{lead_id}/assign", data={"csrf_token": token_from(page),
                                                           "practitioner_id": self.practitioner_id})
        self.assertEqual(self.lead_owner(lead_id), self.practitioner_id)
        self.assertEqual(self.leads()[0]["source"], "assigned")

    def test_practitioners_cannot_reassign(self):
        response = self.submit("/liliana")
        self.unlock(response.headers["Location"].rsplit("/", 1)[-1])
        lead_id = self.leads()[0]["id"]
        self.login("liliana@example.com")
        page = self.client.get(f"/leads/{lead_id}").get_data(as_text=True)
        self.assertNotIn("Reassign", page)
        self.assertEqual(self.client.post(f"/leads/{lead_id}/assign", data={
            "csrf_token": token_from(page), "practitioner_id": self.frank_id}).status_code, 403)


class TeamReviewTest(TeamTestCase):
    def convert(self):
        self.login("owner@example.com")
        html = self.admin_post("/admin/accounts/team", user_id=self.frank_id,
                               owner_id=self.practitioner_id).get_data(as_text=True)
        self.client.post("/logout", data={"csrf_token": token_from(html)})

    def test_cached_referral_to_converted_member_credits_the_practitioner(self):
        self.client.get("/broccoli")          # visitor opens Frank's link first
        visitor = self.client
        self.client = numer.app.test_client()
        self.convert()
        self.client = visitor
        response = self.submit("/")           # same browser session, link now gone
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.leads()[-1]["practitioner_id"], self.practitioner_id)

    def test_member_can_turn_off_email_alerts(self):
        self.convert()
        self.login("frank@example.com")
        page = self.client.get("/settings/password").get_data(as_text=True)
        self.assertIn("Email alerts", page)
        self.client.post("/settings/alerts", data={"csrf_token": token_from(page)})
        with numer.open_db() as connection:
            self.assertEqual(connection.execute("SELECT email_alerts FROM users WHERE id = ?",
                                                (self.frank_id,)).fetchone()[0], 0)

    def test_conversion_closes_the_members_invites(self):
        self.login("frank@example.com")
        page = self.client.get("/invite").get_data(as_text=True)
        self.client.post("/invite", data={"csrf_token": token_from(page), "name": "Pal"})
        with numer.open_db() as connection:
            invite = connection.execute("SELECT id, token FROM invites").fetchone()
        self.client.post("/logout", data={"csrf_token": token_from(page)})
        self.convert()
        self.assertEqual(self.client.get(f"/join/{invite['token']}").status_code, 404)
        self.login("frank@example.com")
        page = self.client.get("/dashboard").get_data(as_text=True)
        self.assertEqual(self.client.post(f"/invite/{invite['id']}/email", data={
            "csrf_token": token_from(page), "email": "pal@example.com"}).status_code, 403)


class TeamSecondReviewTest(TeamTestCase):
    def test_platform_account_cannot_join_a_team(self):
        with numer.open_db() as connection:
            connection.execute("INSERT INTO settings (key, value) VALUES ('platform_user_id', ?)", (str(self.frank_id),))
        self.login("owner@example.com")
        page = self.client.get("/admin").get_data(as_text=True)
        response = self.client.post("/admin/accounts/team", data={"csrf_token": token_from(page),
                                                                  "user_id": self.frank_id, "owner_id": self.practitioner_id})
        self.assertEqual(response.status_code, 400)

    def test_assigned_leads_are_labelled(self):
        response = self.submit("/broccoli")
        self.unlock(response.headers["Location"].rsplit("/", 1)[-1])
        lead_id = self.leads()[0]["id"]
        self.login("owner@example.com")
        page = self.client.get(f"/leads/{lead_id}").get_data(as_text=True)
        html = self.client.post(f"/leads/{lead_id}/assign", follow_redirects=True, data={
            "csrf_token": token_from(page), "practitioner_id": self.practitioner_id}).get_data(as_text=True)
        self.assertIn("assigned by numer.life", html)
        self.assertNotIn("no practitioner", html)
