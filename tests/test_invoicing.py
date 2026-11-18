import datetime as dt
import os
from unittest import mock

import paypal
from test_app import numer, token_from
from test_practitioner_paypal import PractitionerPayPalTest
from test_orders import completed


class InvoicingTest(PractitionerPayPalTest):
    def paid_sale_to_liliana(self):
        self.connect()
        token = self.captured_lead()
        self.buy(token)
        with mock.patch.object(paypal, "capture_order", return_value=completed("order-1")):
            self.client.get("/paypal/return?token=PP123")
        return token

    def invoices(self):
        with numer.open_db() as connection:
            return connection.execute("SELECT * FROM invoices ORDER BY id").fetchall()

    def admin_page(self):
        self.login("owner@example.com")
        return self.client.get("/admin").get_data(as_text=True)

    def test_prepare_send_and_paid(self):
        self.paid_sale_to_liliana()
        page = self.admin_page()
        self.assertIn("Monthly invoices (25%)", page)
        self.client.post("/admin/invoices/prepare", data={"csrf_token": token_from(page), "period": "2026-10"})
        rows = self.invoices()
        self.assertEqual([(r["user_id"], r["fee_cents"], r["plan_cents"], r["status"]) for r in rows],
                         [(self.practitioner_id, 725, 0, "draft")])                  # Liliana: complimentary plan
        self.client.post("/admin/invoices/prepare", data={"csrf_token": token_from(page), "period": "2026-10"})
        self.assertEqual(len(self.invoices()), 1)                                   # once per month
        calls = []

        def fake(method, path, body=None, account=None, request_id=None):
            calls.append((method, path, body, account))
            if path == "/v2/invoicing/invoices":
                return {"id": "INV2-AAAA"}
            if path.endswith("/send"):
                return {}
            return {"id": "INV2-AAAA", "status": "SENT",
                    "detail": {"metadata": {"recipient_view_url": "https://www.paypal.com/invoice/p/#INV2"}}}
        with mock.patch.object(paypal, "call", side_effect=fake):
            self.client.post(f"/admin/invoices/{rows[0]['id']}/send", data={"csrf_token": token_from(page)})
        created = calls[0][2]
        self.assertEqual(calls[0][3].key, "tapnet")
        self.assertEqual(created["items"][0]["unit_amount"], {"currency_code": "CAD", "value": "7.25"})
        self.assertEqual(created["primary_recipients"][0]["billing_info"]["email_address"], "liliana@example.com")
        row = self.invoices()[0]
        self.assertEqual((row["status"], row["paypal_invoice_id"]), ("sent", "INV2-AAAA"))
        # nothing new is billed while it's open
        with numer.app.app_context():
            self.assertEqual(numer.invoicing.prepare("2026-11"), 0)
            with mock.patch.object(paypal, "call", return_value={"status": "PAID"}):
                numer.invoicing.sync_open()
        self.assertEqual(self.invoices()[0]["status"], "paid")
        with numer.open_db() as connection:
            self.assertEqual(numer.billing.balance(self.practitioner_id, connection), 0)

    def test_overdue_reminds_then_pauses_sales(self):
        self.paid_sale_to_liliana()
        with numer.app.app_context():
            numer.invoicing.prepare("2026-10")
        due = dt.date(2026, 11, 1)
        with numer.open_db() as connection:
            connection.execute("UPDATE invoices SET status = 'sent', paypal_invoice_id = 'INV2-X', due_date = ?",
                               (due.isoformat(),))
        with numer.app.app_context(), mock.patch.object(paypal, "call", return_value={"status": "SENT"}) as call:
            numer.invoicing.sync_open(today=due + dt.timedelta(days=7))
            self.assertTrue(any(c.args[1].endswith("/remind") for c in call.call_args_list))
            self.assertFalse(numer.invoicing.is_paused(self.practitioner_id, today=due + dt.timedelta(days=13)))
            self.assertTrue(numer.invoicing.is_paused(self.practitioner_id, today=due + dt.timedelta(days=14)))
        token = self.captured_lead()
        self.assertNotIn("Get my full reading", self.client.get(f"/r/{token}").get_data(as_text=True))

    def test_tapnet_collected_sales_are_not_invoiced(self):
        token = self.captured_lead()
        self.buy(token)
        with mock.patch.object(paypal, "capture_order", return_value=completed("order-1")):
            self.client.get("/paypal/return?token=PP123")
        with numer.app.app_context():
            self.assertEqual(numer.invoicing.prepare("2026-10"), 0)               # TAPNET owes Liliana instead
