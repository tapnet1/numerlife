import json
import os
from unittest import mock

from test_app import PASSWORD, numer, token_from
from test_intake import IntakeTestCase

import orders
import paypal


def completed(reference, value="29.00", currency="CAD", status="COMPLETED"):
    return {"id": "PP123", "status": status, "payer": {"email_address": "buyer@example.com"},
            "purchase_units": [{"reference_id": reference, "payments": {"captures": [
                {"id": "CAP1", "status": "COMPLETED", "amount": {"currency_code": currency, "value": value}}]}}]}


class OrdersTestCase(IntakeTestCase):
    def setUp(self):
        super().setUp()
        self.env = mock.patch.dict(os.environ, {"NUMER_PAYPAL_CLIENT_ID": "id", "NUMER_PAYPAL_SECRET": "secret"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.tapnet_id = numer.create_user("tapnet@example.com", PASSWORD, "practitioner", "TAPNET", slug="tapnet")
        with numer.open_db() as connection:
            connection.execute("INSERT INTO settings (key, value) VALUES ('platform_user_id', ?)", (str(self.tapnet_id),))
            connection.execute("INSERT INTO settings (key, value) VALUES ('paid_reports', 'on')")
            connection.execute("UPDATE users SET report_method = 'kdp' WHERE id = ?", (self.practitioner_id,))

    def captured_lead(self):
        token = self.submit(path="/liliana", full_name="Marco James Bonanni",
                            birth_date="1974-06-19").headers["Location"].rsplit("/", 1)[-1]
        self.unlock(token)
        return token

    def buy(self, token):
        page = self.client.get(f"/r/{token}").get_data(as_text=True)
        with mock.patch.object(paypal, "create_order", return_value=("PP123", "https://www.sandbox.paypal.com/x")) as create:
            response = self.client.post(f"/r/{token}/buy", data={"csrf_token": token_from(page)})
        return response, create

    def pay(self, token, result=None):
        self.buy(token)
        with mock.patch.object(paypal, "capture_order", return_value=result or completed("order-1")):
            return self.client.get("/paypal/return?token=PP123")

    def order(self):
        with numer.open_db() as connection:
            return connection.execute("SELECT * FROM orders ORDER BY id DESC").fetchone()

    def ledger(self):
        with numer.open_db() as connection:
            return {(r["user_id"], r["kind"]): r["amount_cents"] for r in connection.execute(
                "SELECT user_id, kind, SUM(amount_cents) AS amount_cents FROM ledger GROUP BY user_id, kind")}

    def outbox(self, kind):
        with numer.open_db() as connection:
            return connection.execute("SELECT * FROM email_outbox WHERE kind = ?", (kind,)).fetchall()


class CheckoutTest(OrdersTestCase):
    def test_order_and_paypal_paths_cannot_be_practitioner_links(self):
        import intake
        for slug in ("orders", "paypal", "checkout"):
            with self.assertRaises(ValueError):
                intake.validate_slug(slug)

    def test_offer_needs_the_switch_and_paypal_keys(self):
        token = self.captured_lead()
        self.assertIn("Get my full reading · CAD $29.00", self.client.get(f"/r/{token}").get_data(as_text=True))
        with mock.patch.dict(os.environ, {"NUMER_PAYPAL_SECRET": ""}):
            self.assertNotIn("Get my full reading", self.client.get(f"/r/{token}").get_data(as_text=True))
        with numer.open_db() as connection:
            connection.execute("UPDATE settings SET value = 'off' WHERE key = 'paid_reports'")
        self.assertNotIn("Get my full reading", self.client.get(f"/r/{token}").get_data(as_text=True))

    def test_buy_sends_the_visitor_to_paypal_for_29_cad(self):
        token = self.captured_lead()
        response, create = self.buy(token)
        self.assertEqual(response.headers["Location"], "https://www.sandbox.paypal.com/x")
        kwargs = create.call_args.kwargs
        self.assertEqual((kwargs["cents"], kwargs["currency"], kwargs["reference"]), (2900, "CAD", "order-1"))
        order = self.order()
        self.assertEqual((order["status"], order["paypal_order_id"], order["practitioner_id"]),
                         ("pending", "PP123", self.practitioner_id))

    def test_a_new_checkout_cancels_the_unpaid_one(self):
        token = self.captured_lead()
        self.buy(token)
        with mock.patch.object(paypal, "create_order", return_value=("PP999", "https://www.sandbox.paypal.com/y")):
            page = self.client.get(f"/r/{token}").get_data(as_text=True)
            self.client.post(f"/r/{token}/buy", data={"csrf_token": token_from(page)})
        with numer.open_db() as connection:
            statuses = [r[0] for r in connection.execute("SELECT status FROM orders ORDER BY id")]
        self.assertEqual(statuses, ["cancelled", "pending"])

    def test_only_unlocked_reports_can_buy(self):
        token = self.submit(path="/liliana").headers["Location"].rsplit("/", 1)[-1]
        page = self.client.get(f"/r/{token}").get_data(as_text=True)
        self.client.post(f"/r/{token}/buy", data={"csrf_token": token_from(page)})
        self.assertIsNone(self.order())

    def test_payment_confirmed_by_capture_marks_paid_credits_and_alerts(self):
        token = self.captured_lead()
        response = self.pay(token)
        order = self.order()
        self.assertEqual(response.headers["Location"], f"/p/{order['token']}")
        self.assertEqual((order["status"], order["paypal_capture_id"], order["payer_email"]),
                         ("paid", "CAP1", "buyer@example.com"))
        ledger = self.ledger()
        self.assertEqual(ledger[(self.practitioner_id, "sale_share")], 2175)
        self.assertEqual(ledger[(self.tapnet_id, "platform_share")], 725)
        self.assertEqual(len(self.outbox("order_paid")), 1)
        alert = self.outbox("order_alert")[0]
        self.assertEqual(alert["to_addr"], "liliana@example.com")
        self.assertIn("/settings/orders/1", alert["body_text"])
        with numer.open_db() as connection:
            push = [json.loads(r[0]) for r in connection.execute("SELECT payload FROM push_outbox")]
        self.assertTrue(any(p["url"] == "/settings/orders/1" for p in push))
        page = self.client.get(f"/p/{order['token']}").get_data(as_text=True)
        self.assertIn("Your reading is being prepared", page)
        self.assertIn("Your full written reading", self.client.get(f"/r/{token}").get_data(as_text=True))

    def test_returning_twice_records_the_payment_once(self):
        token = self.captured_lead()
        self.pay(token)
        with mock.patch.object(paypal, "capture_order", return_value=completed("order-1")) as capture:
            self.client.get("/paypal/return?token=PP123")
        capture.assert_not_called()
        self.assertEqual(len(self.outbox("order_paid")), 1)
        self.assertEqual(self.ledger()[(self.practitioner_id, "sale_share")], 2175)

    def test_wrong_amount_or_incomplete_capture_is_never_paid(self):
        for result in (completed("order-1", value="1.00"), completed("order-1", currency="USD"),
                       completed("order-2"), completed("order-1", status="PENDING")):
            self.assertIsNone(paypal.completed_capture(result, reference="order-1", cents=2900, currency="CAD"))
        token = self.captured_lead()
        self.pay(token, result=completed("order-1", value="1.00"))
        self.assertEqual(self.order()["status"], "pending")
        self.assertEqual(self.ledger(), {})

    def test_capture_completing_after_a_cancel_is_still_recorded(self):
        token = self.captured_lead()
        self.buy(token)

        def capture_while_cancelled(_order_id):
            with numer.open_db() as connection:  # e.g. the buyer's cancel link in another tab
                connection.execute("UPDATE orders SET status = 'cancelled'")
            return completed("order-1")

        with mock.patch.object(paypal, "capture_order", side_effect=capture_while_cancelled):
            with numer.app.app_context():
                self.assertEqual(orders.finalize(1), "paid")
        self.assertEqual(self.order()["status"], "paid")
        self.assertEqual(self.ledger()[(self.practitioner_id, "sale_share")], 2175)
        self.assertEqual(len(self.outbox("order_paid")), 1)

    def test_cancel_link_after_payment_shows_the_paid_order(self):
        token = self.captured_lead()
        self.pay(token)
        response = self.client.get("/paypal/cancel?token=PP123")
        self.assertEqual(response.headers["Location"], f"/p/{self.order()['token']}")
        self.assertEqual(self.order()["status"], "paid")

    def test_declined_payment_returns_to_the_report(self):
        token = self.captured_lead()
        self.buy(token)
        with mock.patch.object(paypal, "capture_order",
                               side_effect=paypal.PayPalError("declined", 422, "INSTRUMENT_DECLINED")):
            response = self.client.get("/paypal/return?token=PP123")
        self.assertEqual(response.headers["Location"], f"/r/{token}")
        self.assertEqual(self.order()["status"], "pending")

    def test_cancel_at_paypal(self):
        token = self.captured_lead()
        self.buy(token)
        response = self.client.get("/paypal/cancel?token=PP123")
        self.assertEqual(response.headers["Location"], f"/r/{token}")
        self.assertEqual(self.order()["status"], "cancelled")

    def test_job_captures_approved_orders_whose_buyer_never_returned(self):
        token = self.captured_lead()
        self.buy(token)
        with numer.open_db() as connection:
            connection.execute("UPDATE orders SET created_at = '2020-01-01T00:00:00+00:00'")
        result = orders.capture_open_orders()
        self.assertEqual(result["paid"], 0)  # older than 3 days: given up, never captured
        self.assertEqual(self.order()["status"], "cancelled")

        with numer.open_db() as connection:
            connection.execute("UPDATE orders SET status = 'pending', created_at = ?",
                               (numer.iso(numer.now_utc() - __import__("datetime").timedelta(minutes=10)),))
        with mock.patch.object(paypal, "get_order", return_value={"status": "APPROVED"}), \
                mock.patch.object(paypal, "capture_order", return_value=completed("order-1")):
            result = orders.capture_open_orders()
        self.assertEqual(result, {"checked": 1, "paid": 1})
        self.assertEqual(self.order()["status"], "paid")


class ReviewAndDeliveryTest(OrdersTestCase):
    def paid_order(self):
        self.pay(self.captured_lead())
        return self.order()

    def test_practitioner_edits_and_sends_the_reading(self):
        order = self.paid_order()
        self.login("liliana@example.com")
        page = self.client.get(f"/settings/orders/{order['id']}").get_data(as_text=True)
        self.assertIn("Oversensitivity", page)            # draft from the Karma 11 starter text
        self.assertNotIn('name="0-strengths"', page)     # Karma: no strengths
        self.assertEqual(self.outbox("order_ready"), [])
        self.client.post(f"/settings/orders/{order['id']}", data={
            "csrf_token": token_from(page), "action": "save", "note": "Querido Marco,\nun abrazo.",
            "0-lesson": "Learning to trust his intuition, develop inner confidence."})
        self.assertEqual(self.order()["status"], "paid")  # saving never sends
        public = self.client.get(f"/p/{order['token']}").get_data(as_text=True)
        self.assertNotIn("Learning to trust his intuition", public)
        page = self.client.get(f"/settings/orders/{order['id']}").get_data(as_text=True)
        self.client.post(f"/settings/orders/{order['id']}", data={"csrf_token": token_from(page), "action": "send"})
        self.assertEqual(self.order()["status"], "delivered")
        ready = self.outbox("order_ready")
        self.assertEqual((len(ready), ready[0]["to_addr"]), (1, "john@example.com"))
        public = self.client.get(f"/p/{order['token']}").get_data(as_text=True)
        self.assertIn("Learning to trust his intuition, develop inner confidence.", public)
        self.assertIn("un abrazo", public)
        pdf = self.client.get(f"/p/{order['token']}/reading.pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.data.startswith(b"%PDF"))
        # later edits update the page without a second email
        page = self.client.get(f"/settings/orders/{order['id']}").get_data(as_text=True)
        self.client.post(f"/settings/orders/{order['id']}", data={"csrf_token": token_from(page), "action": "send",
                                                         "combination": "Updated."})
        self.assertEqual(len(self.outbox("order_ready")), 1)
        self.assertIn("Updated.", self.client.get(f"/p/{order['token']}").get_data(as_text=True))

    def test_pdf_waits_for_delivery_and_pages_are_private(self):
        order = self.paid_order()
        self.assertEqual(self.client.get(f"/p/{order['token']}/reading.pdf").status_code, 404)
        response = self.client.get(f"/p/{order['token']}")
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.client.get("/p/not-a-token").status_code, 404)

    def test_other_practitioners_cannot_open_the_order(self):
        order = self.paid_order()
        numer.create_user("other@example.com", PASSWORD, "practitioner", "Other", slug="other")
        self.login("other@example.com")
        self.assertEqual(self.client.get(f"/settings/orders/{order['id']}").status_code, 404)
        self.assertNotIn("Marco", self.client.get("/settings/orders").get_data(as_text=True))

    def test_refund_reverses_credits(self):
        order = self.paid_order()
        self.login("owner@example.com")
        page = self.client.get("/admin").get_data(as_text=True)
        self.assertIn("Mark refunded", page)
        self.client.post(f"/admin/orders/{order['id']}/refunded", data={"csrf_token": token_from(page)})
        self.assertEqual(self.order()["status"], "refunded")
        with numer.open_db() as connection:
            totals = dict(connection.execute("SELECT user_id, SUM(amount_cents) FROM ledger GROUP BY user_id").fetchall())
        self.assertEqual(totals, {self.practitioner_id: 0, self.tapnet_id: 0})
        self.assertIn("refunded", self.client.get(f"/p/{order['token']}").get_data(as_text=True))

    def test_payment_is_recorded_even_without_a_platform_account(self):
        token = self.captured_lead()
        self.buy(token)
        with numer.open_db() as connection:
            connection.execute("DELETE FROM settings WHERE key = 'platform_user_id'")
        with mock.patch.object(paypal, "capture_order", return_value=completed("order-1")):
            self.client.get("/paypal/return?token=PP123")
        self.assertEqual(self.order()["status"], "paid")
        self.assertEqual(self.ledger(), {(self.practitioner_id, "sale_share"): 2175})
        self.assertEqual(len(self.outbox("order_alert")), 1)
        self.assertNotIn("Get my full reading", self.client.get(f"/r/{token}").get_data(as_text=True))

    def test_a_sent_reading_cannot_be_reset(self):
        order = self.paid_order()
        self.login("liliana@example.com")
        page = self.client.get(f"/settings/orders/{order['id']}").get_data(as_text=True)
        self.assertIn('value="reset"', page)
        self.client.post(f"/settings/orders/{order['id']}", data={"csrf_token": token_from(page), "action": "send"})
        page = self.client.get(f"/settings/orders/{order['id']}").get_data(as_text=True)
        self.assertNotIn('value="reset"', page)
        response = self.client.post(f"/settings/orders/{order['id']}", data={"csrf_token": token_from(page), "action": "reset"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(f"/p/{order['token']}/reading.pdf").status_code, 200)

    def test_admin_switch_needs_paypal_keys(self):
        self.login("owner@example.com")
        page = self.client.get("/admin").get_data(as_text=True)
        self.client.post("/admin/paid-reports", data={"csrf_token": token_from(page), "on": "no"})
        import intake
        with numer.app.app_context():
            self.assertFalse(intake.paid_reports_on())
        with mock.patch.dict(os.environ, {"NUMER_PAYPAL_CLIENT_ID": ""}):
            self.client.post("/admin/paid-reports", data={"csrf_token": token_from(page), "on": "yes"})
            with numer.app.app_context():
                self.assertFalse(intake.paid_reports_on())
        self.client.post("/admin/paid-reports", data={"csrf_token": token_from(page), "on": "yes"})
        with numer.app.app_context():
            self.assertTrue(intake.paid_reports_on())
        self.client.post("/admin/paid-reports", data={"csrf_token": token_from(page), "on": "no"})
        with numer.open_db() as connection:
            connection.execute("DELETE FROM settings WHERE key = 'platform_user_id'")
        self.client.post("/admin/paid-reports", data={"csrf_token": token_from(page), "on": "yes"})
        with numer.app.app_context():
            self.assertFalse(intake.paid_reports_on())  # TAPNET's account must exist first
