import datetime as dt
import os
from unittest import mock

import paypal
from test_app import numer, token_from
from test_calendars import CalendarTestCase


class FakePayPal:
    """Answers the PayPal REST calls the subscription code makes."""

    def __init__(self):
        self.calls = []
        self.status = "ACTIVE"
        self.custom_id = None
        self.next_billing = None
        self.last_payment = None
        self.transactions = []

    def __call__(self, method, path, body=None, account=None, request_id=None):
        self.calls.append((method, path, body, account))
        if path == "/v1/catalogs/products":
            return {"id": "PROD-1"}
        if path == "/v1/billing/plans":
            return {"id": "P-1"}
        if path == "/v1/billing/subscriptions" and method == "POST":
            self.custom_id = body["custom_id"]
            return {"id": "I-SUB1", "status": "APPROVAL_PENDING",
                    "links": [{"rel": "approve", "href": "https://www.paypal.com/approve/I-SUB1"}]}
        if path.endswith("/transactions") or "/transactions?" in path:
            return {"transactions": self.transactions}
        if path.startswith("/v1/billing/subscriptions/I-SUB1"):
            info = {}
            if self.next_billing:
                info["next_billing_time"] = self.next_billing
            if self.last_payment:
                info["last_payment"] = {"time": self.last_payment, "amount": {"value": "100.00", "currency_code": "CAD"}}
            return {"id": "I-SUB1", "status": self.status, "custom_id": self.custom_id, "billing_info": info}
        raise AssertionError(path)


class CalendarSubscriptionTest(CalendarTestCase):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {"NUMER_PAYPAL_CLIENT_ID": "id", "NUMER_PAYPAL_SECRET": "secret"})
        env.start()
        self.addCleanup(env.stop)
        self.tapnet_id = numer.create_user("tapnet@example.com", "correct horse battery", "practitioner", "TAPNET",
                                           slug="tapnet")
        with numer.open_db() as connection:
            connection.execute("INSERT INTO settings (key, value) VALUES ('platform_user_id', ?)", (str(self.tapnet_id),))
        self.lead = self.lead_via_liliana()
        self.login("liliana@example.com")
        self.start(self.lead["id"], kind="numerology", lang="en")
        self.cal = self.calendar()
        self.fake = FakePayPal()
        patcher = mock.patch.object(paypal, "call", side_effect=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.visitor = numer.app.test_client()

    def subscribe(self):
        page = self.visitor.get(f"/c/{self.cal['token']}").get_data(as_text=True)
        self.assertIn("Subscribe: CAD $250 a year", page)                 # banner during the free month
        return self.visitor.post(f"/c/{self.cal['token']}/subscribe", data={"csrf_token": token_from(page)})

    def sub(self):
        with numer.open_db() as connection:
            return connection.execute("SELECT * FROM calendar_subscriptions ORDER BY id DESC").fetchone()

    def test_subscribe_creates_plan_and_starts_after_the_free_month(self):
        response = self.subscribe()
        self.assertEqual(response.headers["Location"], "https://www.paypal.com/approve/I-SUB1")
        plan = next(c for c in self.fake.calls if c[1] == "/v1/billing/plans")[2]
        cycles = plan["billing_cycles"]
        self.assertEqual([(c["tenure_type"], c["total_cycles"], c["pricing_scheme"]["fixed_price"]["value"])
                          for c in cycles], [("TRIAL", 1, "100.00"), ("REGULAR", 10, "15.00")])
        body = next(c for c in self.fake.calls if c[1] == "/v1/billing/subscriptions")[2]
        free_until = numer.calendar_access.access_until(self.cal)
        self.assertEqual(body["start_time"], f"{free_until.isoformat()}T18:00:00Z")
        self.assertEqual(self.sub()["status"], "APPROVAL_PENDING")
        self.assertEqual(self.sub()["paypal_account"], "tapnet")

    def test_return_activates_extends_access_credits_once_and_alerts(self):
        self.subscribe()
        today = dt.date.today()
        self.fake.last_payment = f"{today.isoformat()}T18:00:00Z"
        self.fake.next_billing = f"{(today + dt.timedelta(days=30)).isoformat()}T18:00:00Z"
        self.fake.transactions = [{"id": "TX1", "status": "COMPLETED", "time": self.fake.last_payment,
                                   "amount_with_breakdown": {"gross_amount": {"value": "100.00", "currency_code": "CAD"}}}]
        response = self.visitor.get("/paypal/subscription/return?subscription_id=I-SUB1")
        self.assertIn(f"/c/{self.cal['token']}", response.headers["Location"])
        sub = self.sub()
        self.assertEqual((sub["status"], sub["paid_cents"]), ("ACTIVE", 10000))
        with numer.open_db() as connection:
            cal = connection.execute("SELECT * FROM calendars WHERE id = ?", (self.cal["id"],)).fetchone()
            ledger = connection.execute("SELECT user_id, kind, amount_cents FROM ledger ORDER BY id").fetchall()
            alerts = connection.execute("SELECT COUNT(*) FROM email_outbox WHERE kind = 'calendar_subscribed'").fetchone()[0]
        self.assertGreaterEqual(dt.date.fromisoformat(cal["access_until"]), today + dt.timedelta(days=31))
        self.assertEqual([tuple(r) for r in ledger],
                         [(self.tapnet_id, "platform_share", 2500), (self.practitioner_id, "sale_share", 7500)])
        self.assertEqual(alerts, 1)
        with numer.app.app_context():
            numer.calendar_subscriptions.sync_open()                        # job again: nothing doubled
        self.assertEqual(self.sub()["paid_cents"], 10000)
        with numer.open_db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ledger").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM email_outbox WHERE kind = 'calendar_subscribed'")
                             .fetchone()[0], 1)

    def test_wrong_custom_id_is_ignored(self):
        self.subscribe()
        self.fake.custom_id = "calsub-999"
        self.fake.last_payment = "2026-01-01T00:00:00Z"
        self.visitor.get("/paypal/subscription/return?subscription_id=I-SUB1")
        self.assertEqual(self.sub()["status"], "APPROVAL_PENDING")

    def test_locked_month_offers_subscription(self):
        later = numer.calendar_access.access_until(self.cal) + dt.timedelta(days=70)
        html = self.visitor.get(f"/c/{self.cal['token']}/{later.strftime('%Y-%m')}").get_data(as_text=True)
        self.assertIn("part of your full year", html)
        self.assertIn("Subscribe: CAD $250 a year", html)

    def test_lead_page_shows_plan_status(self):
        self.subscribe()
        html = self.client.get(f"/leads/{self.lead['id']}").get_data(as_text=True)
        self.assertIn("Yearly plan:", html)
