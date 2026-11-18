import os
from unittest import mock

import paypal
from test_app import PASSWORD, numer, token_from
from test_orders import OrdersTestCase, completed

CLIENT_ID = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
SECRET = "EeFfGgHhIiJjKkLlMmNnOoPpQqRrSsTt9876543210"


class PractitionerPayPalTest(OrdersTestCase):
    def setUp(self):
        super().setUp()
        key = mock.patch.dict(os.environ, {"NUMER_CREDENTIAL_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="})
        key.start()
        self.addCleanup(key.stop)

    def connect(self, env="live", verify=None):
        self.login("liliana@example.com")
        page = self.client.get("/settings/paypal").get_data(as_text=True)
        with mock.patch.object(paypal, "verify", side_effect=verify) as check:
            response = self.client.post("/settings/paypal", data={
                "csrf_token": token_from(page), "env": env, "client_id": CLIENT_ID, "secret": SECRET})
        self.client.post("/logout", data={"csrf_token": token_from(page)})
        return response, check

    def liliana(self):
        with numer.open_db() as connection:
            return connection.execute("SELECT * FROM users WHERE id = ?", (self.practitioner_id,)).fetchone()

    def test_connect_stores_encrypted_keys_after_paypal_accepts_them(self):
        _, check = self.connect()
        account = check.call_args.args[0]
        self.assertEqual((account.env, account.client_id, account.secret), ("live", CLIENT_ID, SECRET))
        user = self.liliana()
        self.assertEqual((user["paypal_client_id"], user["paypal_env"]), (CLIENT_ID, "live"))
        self.assertNotIn(SECRET, user["paypal_secret"])
        self.assertNotIn(SECRET, repr(account))

    def test_rejected_keys_are_not_saved(self):
        self.connect(verify=paypal.PayPalError("bad", 401))
        self.assertIsNone(self.liliana()["paypal_client_id"])

    def test_team_members_cannot_open_paypal_settings(self):
        member = numer.create_user("frank@example.com", PASSWORD, "practitioner", "Frank")
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET team_of = ? WHERE id = ?", (self.practitioner_id, member))
        self.login("frank@example.com")
        self.assertEqual(self.client.get("/settings/paypal").status_code, 403)

    def test_clients_pay_the_practitioner_and_the_fee_is_owed(self):
        self.connect()
        token = self.captured_lead()
        response, create = self.buy(token)
        self.assertEqual(create.call_args.kwargs["account"].key, f"user:{self.practitioner_id}")
        self.assertEqual(self.order()["paypal_account"], f"user:{self.practitioner_id}")
        with mock.patch.object(paypal, "capture_order", return_value=completed("order-1")) as capture:
            self.client.get("/paypal/return?token=PP123")
        self.assertEqual(capture.call_args.kwargs["account"].client_id, CLIENT_ID)
        self.assertEqual(self.order()["status"], "paid")
        with numer.open_db() as connection:
            ledger = connection.execute("SELECT user_id, kind, amount_cents FROM ledger ORDER BY id").fetchall()
            receipt = connection.execute("SELECT body_text FROM email_outbox WHERE kind = 'order_paid'").fetchone()
        self.assertEqual([tuple(r) for r in ledger], [(self.practitioner_id, "platform_fee", -725)])
        self.assertIn("Paid with PayPal to Liliana", receipt["body_text"])

    def test_without_their_paypal_clients_pay_tapnet(self):
        token = self.captured_lead()
        _, create = self.buy(token)
        self.assertEqual(create.call_args.kwargs["account"].key, "tapnet")
        self.assertEqual(self.order()["paypal_account"], "tapnet")

    def test_practitioner_paypal_alone_opens_sales(self):
        self.connect()
        with mock.patch.dict(os.environ, {"NUMER_PAYPAL_CLIENT_ID": "", "NUMER_PAYPAL_SECRET": ""}):
            token = self.captured_lead()
            self.assertIn("Get my full reading", self.client.get(f"/r/{token}").get_data(as_text=True))

    def test_refund_reverses_the_fee(self):
        self.connect()
        token = self.captured_lead()
        self.buy(token)
        with mock.patch.object(paypal, "capture_order", return_value=completed("order-1")):
            self.client.get("/paypal/return?token=PP123")
        self.login("owner@example.com")
        page = self.client.get("/admin").get_data(as_text=True)
        self.client.post(f"/admin/orders/{self.order()['id']}/refunded", data={"csrf_token": token_from(page)})
        with numer.open_db() as connection:
            total = connection.execute("SELECT SUM(amount_cents) FROM ledger WHERE user_id = ?",
                                       (self.practitioner_id,)).fetchone()[0]
        self.assertEqual(total, 0)
