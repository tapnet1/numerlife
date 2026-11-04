from test_app import PASSWORD, numer, token_from
from test_calendars import CalendarTestCase
from test_orders import OrdersTestCase


class FeedbackTestCase(CalendarTestCase):
    def journal(self):
        with numer.open_db() as connection:
            return connection.execute("SELECT * FROM feedback_journals ORDER BY id DESC").fetchone()

    def entries(self):
        with numer.open_db() as connection:
            return connection.execute("SELECT * FROM feedback_entries ORDER BY id").fetchall()

    def outbox(self, kind):
        with numer.open_db() as connection:
            return connection.execute("SELECT * FROM email_outbox WHERE kind = ?", (kind,)).fetchall()

    def invite(self, lead_id, send="yes"):
        page = self.client.get(f"/leads/{lead_id}").get_data(as_text=True)
        return self.client.post(f"/leads/{lead_id}/feedback", data={"csrf_token": token_from(page), "send": send})

    def client_writes(self, token, ip="10.9.0.1", **fields):
        visitor = numer.app.test_client()
        page = visitor.get(f"/f/{token}", environ_base={"REMOTE_ADDR": ip}).get_data(as_text=True)
        data = {"csrf_token": token_from(page), "day": "2025-11-23", "feeling": "4", "body": "Más tranquila.",
                "website": ""}
        data.update(fields)
        return visitor.post(f"/f/{token}", data=data, environ_base={"REMOTE_ADDR": ip})


class InviteTest(FeedbackTestCase):
    def test_invite_needs_a_completed_session(self):
        lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        page = self.client.get(f"/leads/{lead['id']}").get_data(as_text=True)
        self.assertIn("After a completed reading", page)
        self.invite(lead["id"])
        self.assertIsNone(self.journal())
        self.log_reading(lead["id"], done=False)                      # scheduled isn't enough
        self.invite(lead["id"])
        self.assertIsNone(self.journal())
        self.log_reading(lead["id"], done=True)
        self.assertIn("Email a feedback link", self.client.get(f"/leads/{lead['id']}").get_data(as_text=True))
        self.invite(lead["id"])
        journal = self.journal()
        self.assertIsNotNone(journal["invited_at"])
        sent = self.outbox("feedback_invite")
        self.assertEqual((len(sent), sent[0]["to_addr"]), (1, "john@example.com"))
        self.assertIn(f"/f/{journal['token']}", sent[0]["body_text"])
        self.assertIn(f"/f/{journal['token']}", self.client.get(f"/leads/{lead['id']}").get_data(as_text=True))

    def test_link_without_email_and_replacing_it(self):
        lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        self.log_reading(lead["id"])
        self.invite(lead["id"], send="no")
        old = self.journal()
        self.assertIsNone(old["invited_at"])
        self.assertEqual(self.outbox("feedback_invite"), [])
        page = self.client.get(f"/leads/{lead['id']}").get_data(as_text=True)
        self.client.post(f"/leads/{lead['id']}/feedback/new-link", data={"csrf_token": token_from(page)})
        self.assertNotEqual(self.journal()["token"], old["token"])
        self.assertEqual(numer.app.test_client().get(f"/f/{old['token']}").status_code, 404)

    def test_other_practitioners_cannot_invite(self):
        lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        self.log_reading(lead["id"])
        numer.create_user("other@example.com", PASSWORD, "practitioner", "Other")
        self.client = numer.app.test_client()
        self.login("other@example.com")
        page = self.client.get("/dashboard").get_data(as_text=True)
        response = self.client.post(f"/leads/{lead['id']}/feedback", data={"csrf_token": token_from(page), "send": "yes"})
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(self.journal())


class ClientJournalTest(FeedbackTestCase):
    def ready(self):
        lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        self.log_reading(lead["id"])                                  # completed on 2025-11-20
        self.invite(lead["id"])
        return lead, self.journal()

    def test_client_writes_on_any_day_and_practitioner_sees_it(self):
        lead, journal = self.ready()
        page = numer.app.test_client().get(f"/f/{journal['token']}")
        self.assertEqual(page.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertEqual(page.headers["Cache-Control"], "no-store")
        self.assertEqual(page.headers["Referrer-Policy"], "no-referrer")
        self.assertIn("How are you feeling", page.get_data(as_text=True))
        response = self.client_writes(journal["token"])
        self.assertEqual(response.status_code, 302)
        self.client_writes(journal["token"], day="2025-11-27", feeling="", body="Soñé con mi abuela.")
        first, second = self.entries()
        self.assertEqual((first["session_date"], first["entry_date"], first["feeling"]), ("2025-11-20", "2025-11-23", 4))
        self.assertIsNone(second["feeling"])
        self.assertEqual(len(self.outbox("feedback_alert")), 2)
        with numer.open_db() as connection:
            push = connection.execute("SELECT payload FROM push_outbox WHERE user_id = ?",
                                      (self.practitioner_id,)).fetchall()
        self.assertTrue(any("Day 3 after" in row["payload"] for row in push))
        dashboard = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("New client feedback", dashboard)
        lead_page = self.client.get(f"/leads/{lead['id']}").get_data(as_text=True)
        self.assertIn("Day 3 after", lead_page)
        self.assertIn("Day 7 after", lead_page)
        self.assertIn("Más tranquila.", lead_page)
        self.assertIn("badge-unread", lead_page)                       # new on this view...
        self.assertTrue(all(e["seen_at"] for e in self.entries()))     # ...and seen afterwards
        self.assertNotIn("New client feedback", self.client.get("/dashboard").get_data(as_text=True))
        history = numer.app.test_client().get(f"/f/{journal['token']}").get_data(as_text=True)
        self.assertIn("Soñé con mi abuela.", history)

    def test_validation_and_honeypot(self):
        _, journal = self.ready()
        self.assertEqual(self.client_writes(journal["token"], feeling="", body="").status_code, 400)
        self.assertEqual(self.client_writes(journal["token"], day="2025-11-19").status_code, 400)  # before the session
        self.assertEqual(self.client_writes(journal["token"], day="2099-01-01").status_code, 400)
        self.assertEqual(self.client_writes(journal["token"], day="nope").status_code, 400)
        self.client_writes(journal["token"], website="spam")
        self.assertEqual(self.entries(), [])
        self.assertEqual(numer.app.test_client().get("/f/not-a-token").status_code, 404)

    def test_alerts_respect_the_email_switch_and_reach_the_team(self):
        _, journal = self.ready()
        frank = numer.create_user("frank@example.com", PASSWORD, "practitioner", "Frank")
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET team_of = ? WHERE id = ?", (self.practitioner_id, frank))
            connection.execute("UPDATE users SET email_alerts = 0 WHERE id = ?", (self.practitioner_id,))
        self.client_writes(journal["token"])
        self.assertEqual([r["to_addr"] for r in self.outbox("feedback_alert")], ["frank@example.com"])
        with numer.open_db() as connection:
            users = {r["user_id"] for r in connection.execute("SELECT user_id FROM push_outbox")}
        self.assertTrue({self.practitioner_id, frank} <= users)


class DeliveredReadingFeedbackTest(OrdersTestCase):
    def test_delivery_email_and_page_carry_the_feedback_link(self):
        self.pay(self.captured_lead())
        with numer.open_db() as connection:
            order = connection.execute("SELECT * FROM orders ORDER BY id DESC").fetchone()
        self.login("liliana@example.com")
        page = self.client.get(f"/settings/orders/{order['id']}").get_data(as_text=True)
        self.client.post(f"/settings/orders/{order['id']}", data={"csrf_token": token_from(page), "action": "send"})
        with numer.open_db() as connection:
            journal = connection.execute("SELECT * FROM feedback_journals").fetchone()
        self.assertIn(f"/f/{journal['token']}", self.outbox("order_ready")[0]["body_text"])
        public = numer.app.test_client().get(f"/p/{order['token']}").get_data(as_text=True)
        self.assertIn(f"/f/{journal['token']}", public)
        journal_page = numer.app.test_client().get(f"/f/{journal['token']}").get_data(as_text=True)
        self.assertIn('name="session" value="order-', journal_page)

    def test_feedback_paths_cannot_be_practitioner_links(self):
        import intake
        for slug in ("f", "feedback"):
            with self.assertRaises(ValueError):
                intake.validate_slug(slug)
