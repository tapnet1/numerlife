from test_app import PASSWORD, AppTestCase, numer, token_from


class AppNudgeTest(AppTestCase):
    def dashboard(self):
        return self.client.get("/dashboard").get_data(as_text=True)

    def test_practitioner_without_device_sees_banner_and_can_snooze(self):
        self.login("liliana@example.com")
        page = self.dashboard()
        self.assertIn("Get client alerts on your phone", page)
        self.assertIn('id="alerts"', page)
        self.client.post("/settings/app-nudge/later", data={"csrf_token": token_from(page)})
        self.assertNotIn("Get client alerts on your phone", self.dashboard())

    def test_hidden_once_a_device_is_on_and_for_admins(self):
        with numer.open_db() as connection:
            connection.execute("INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, created_at)"
                               " VALUES (?, 'https://push.example/x', 'k', 'a', ?)",
                               (self.practitioner_id, numer.iso(numer.now_utc())))
        self.login("liliana@example.com")
        self.assertNotIn("Get client alerts on your phone", self.dashboard())
        self.client.post("/logout", data={"csrf_token": token_from(self.dashboard())})
        self.login("owner@example.com")
        self.assertNotIn("Get client alerts on your phone", self.dashboard())
