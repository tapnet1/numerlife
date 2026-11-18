from test_app import numer, token_from
from test_calendars import CalendarTestCase


class ClientPageTest(CalendarTestCase):
    def page_row(self, lead_id):
        with numer.open_db() as connection:
            return connection.execute("SELECT * FROM client_pages WHERE lead_id = ?", (lead_id,)).fetchone()

    def outbox(self, kind):
        with numer.open_db() as connection:
            return connection.execute("SELECT * FROM email_outbox WHERE kind = ?", (kind,)).fetchall()

    def test_report_email_carries_the_client_page(self):
        lead = self.lead_via_liliana()
        page = self.page_row(lead["id"])
        self.assertIsNotNone(page)
        report = self.outbox("report")[-1]
        self.assertIn(f"/me/{page['token']}", report["body_text"])
        self.assertIn(f"/me/{page['token']}", report["body_html"])

    def test_client_page_starts_the_free_calendar(self):
        lead = self.lead_via_liliana()
        token = self.page_row(lead["id"])["token"]
        visitor = numer.app.test_client()
        response = visitor.get(f"/me/{token}")
        html = response.get_data(as_text=True)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn(f"/r/{lead['token']}", html)
        self.assertIsNone(self.calendar())
        start = visitor.post(f"/me/{token}/calendar", data={"csrf_token": token_from(html)})
        cal = self.calendar()
        self.assertEqual((cal["kind"], cal["email_freq"]), ("numerology", "none"))
        self.assertIn(f"/c/{cal['token']}", start.headers["Location"])
        html = visitor.get(f"/me/{token}").get_data(as_text=True)
        self.assertIn(f"/c/{cal['token']}/", html)
        self.assertEqual(visitor.get("/me/nope").status_code, 404)

    def test_practitioner_emails_and_replaces_the_link(self):
        lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        page = self.client.get(f"/leads/{lead['id']}").get_data(as_text=True)
        self.assertIn("Email the client page", page)
        self.client.post(f"/leads/{lead['id']}/client-page", data={"csrf_token": token_from(page), "send": "yes"})
        sent = self.outbox("client_welcome")
        row = self.page_row(lead["id"])
        self.assertEqual(len(sent), 1)
        self.assertIn(f"/me/{row['token']}", sent[0]["body_text"])
        self.assertIsNotNone(row["emailed_at"])
        self.assertIn("Email the client page again", self.client.get(f"/leads/{lead['id']}").get_data(as_text=True))
        self.client.post(f"/leads/{lead['id']}/client-page/new-link", data={"csrf_token": token_from(page)})
        self.assertNotEqual(self.page_row(lead["id"])["token"], row["token"])
        self.assertEqual(numer.app.test_client().get(f"/me/{row['token']}").status_code, 404)

    def test_other_practitioners_cannot_send(self):
        lead = self.lead_via_liliana()
        numer.create_user("other@example.com", "correct horse battery", "practitioner", "Other")
        self.login("other@example.com", "correct horse battery")
        page = self.client.get("/dashboard").get_data(as_text=True)
        response = self.client.post(f"/leads/{lead['id']}/client-page", data={"csrf_token": token_from(page),
                                                                            "send": "yes"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.outbox("client_welcome"), [])

    def test_announcement_only_to_opted_in_once(self):
        first = self.lead_via_liliana()
        second = self.lead_via_liliana()
        with numer.open_db() as connection:
            connection.execute("UPDATE leads SET marketing_opt_in = CASE WHEN id = ? THEN 1 ELSE 0 END",
                               (first["id"],))
        self.login("owner@example.com")
        page = self.client.get("/admin").get_data(as_text=True)
        self.assertIn("Send to 1 client who opted in", page)
        self.client.post("/admin/email/announce-client-pages", data={"csrf_token": token_from(page)})
        self.assertEqual(self.outbox("client_announce"), [])            # needs the confirm tick
        self.client.post("/admin/email/announce-client-pages", data={"csrf_token": token_from(page),
                                                                     "confirm": "yes"})
        sent = self.outbox("client_announce")
        self.assertEqual([r["lead_id"] for r in sent], [first["id"]])
        self.assertIn("/unsubscribe/", sent[0]["body_text"])
        self.client.post("/admin/email/announce-client-pages", data={"csrf_token": token_from(page),
                                                                     "confirm": "yes"})
        self.assertEqual(len(self.outbox("client_announce")), 1)       # once each
        self.assertNotIn(second["id"], [r["lead_id"] for r in sent])


class ClientPageShareTest(ClientPageTest):
    def test_share_options_on_the_lead_page(self):
        lead = self.lead_via_liliana()
        with numer.open_db() as connection:
            connection.execute("UPDATE leads SET phone = '(780) 555-0123' WHERE id = ?", (lead["id"],))
        self.login("liliana@example.com")
        html = self.client.get(f"/leads/{lead['id']}").get_data(as_text=True)
        token = self.page_row(lead["id"])["token"]
        self.assertIn("data-share-url=\"http", html)
        self.assertIn(f"/me/{token}", html)
        self.assertIn("https://wa.me/17805550123?text=", html)
        self.assertIn("sms:(780) 555-0123?&amp;body=", html)
        self.assertIn("Copy message", html)
        self.assertIn("js/share.js", html)

    def test_whatsapp_number(self):
        self.assertEqual(numer.client_page.whatsapp_number("+52 1 55 1234 5678"), "5215512345678")
        self.assertEqual(numer.client_page.whatsapp_number(""), "")


class ClientHomeLinkTest(ClientPageTest):
    def test_client_pages_link_back_to_my_page(self):
        lead = self.lead_via_liliana()
        token = self.page_row(lead["id"])["token"]
        visitor = numer.app.test_client()
        report = visitor.get(f"/r/{lead['token']}").get_data(as_text=True)
        self.assertIn(f'href="/me/{token}"', report)
        html = visitor.get(f"/me/{token}").get_data(as_text=True)
        visitor.post(f"/me/{token}/calendar", data={"csrf_token": token_from(html)})
        cal = self.calendar()
        for path in (f"/c/{cal['token']}", f"/c/{cal['token']}/journal"):
            self.assertIn(f'href="/me/{token}"', visitor.get(path).get_data(as_text=True), path)
