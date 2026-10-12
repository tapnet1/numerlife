import datetime as dt
import sqlite3

from test_app import PASSWORD, AppTestCase, numer, token_from

import billing


def _in_app(fn, *args):
    with numer.app.test_request_context("/"):
        return fn(*args)


class BillingTestCase(AppTestCase):
    def setUp(self):
        super().setUp()
        self.tapnet_id = numer.create_user("tapnet@example.com", PASSWORD, "practitioner", "TAPNET", slug="tapnet")
        with numer.open_db() as connection:
            connection.execute("INSERT INTO settings (key, value) VALUES ('platform_user_id', ?)", (str(self.tapnet_id),))
        self.maria_id = numer.create_user("maria@example.com", PASSWORD, "practitioner", "Maria",
                                          invited_by=self.practitioner_id, slug="maria")

    def set_plan(self, user_id, plan, paid_through=None):
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET plan = ?, paid_through = ? WHERE id = ?",
                               (plan, paid_through, user_id))

    def balances(self):
        return {uid: billing.balance(uid, numer.connect()) for uid in (self.tapnet_id, self.practitioner_id, self.maria_id)}


class LedgerTest(BillingTestCase):
    def test_money_format(self):
        self.assertEqual(billing.money(500), "$5.00")
        self.assertEqual(billing.money(-250), "-$2.50")
        self.assertEqual(billing.money(123456), "$1,234.56")

    def test_ledger_is_append_only_and_idempotent(self):
        with numer.open_db() as connection:
            self.assertTrue(billing.post(connection, self.maria_id, "adjustment", 100, "ref-1"))
            self.assertFalse(billing.post(connection, self.maria_id, "adjustment", 100, "ref-1"))
        with numer.open_db() as connection:
            for sql in ("UPDATE ledger SET amount_cents = 999", "DELETE FROM ledger"):
                with self.assertRaises(sqlite3.DatabaseError):
                    connection.execute(sql)
        self.assertEqual(self.balances()[self.maria_id], 100)

    def test_sale_split_25_75(self):
        self.assertEqual(billing.split(2000), (500, 1500))
        self.assertEqual(billing.split(999), (249, 750))  # practitioner keeps the odd cent
        with numer.open_db() as connection:
            billing.record_sale(connection, self.maria_id, 2000, "paypal:ORDER1")
            billing.record_sale(connection, self.maria_id, 2000, "paypal:ORDER1")  # webhook retried
        balances = self.balances()
        self.assertEqual((balances[self.maria_id], balances[self.tapnet_id]), (1500, 500))


class ReferralTest(BillingTestCase):
    def test_referral_reward_goes_to_inviter_once_a_month(self):
        today = dt.date(2026, 11, 3)
        self.set_plan(self.maria_id, "active", "2026-11-30")
        self.assertEqual(billing.accrue_referrals(today)["credited"], 1)
        self.assertEqual(billing.accrue_referrals(today)["already"], 1)
        self.assertEqual(billing.accrue_referrals(dt.date(2026, 11, 20))["credited"], 0)
        self.assertEqual(self.balances()[self.practitioner_id], 100)   # Liliana invited Maria
        self.set_plan(self.maria_id, "active", "2026-12-31")
        billing.accrue_referrals(dt.date(2026, 12, 2))
        self.assertEqual(self.balances()[self.practitioner_id], 200)

    def test_no_reward_for_complimentary_unpaid_or_lapsed(self):
        self.set_plan(self.maria_id, "complimentary")
        self.assertEqual(billing.accrue_referrals(dt.date(2026, 11, 3))["credited"], 0)
        self.set_plan(self.maria_id, "active", "2026-10-31")  # paid period ended
        self.assertEqual(billing.accrue_referrals(dt.date(2026, 11, 3))["credited"], 0)

    def test_tapnet_is_the_top_of_the_chain(self):
        # Liliana was invited from the admin account: her referrals belong to TAPNET.
        self.set_plan(self.practitioner_id, "active", "2026-11-30")
        # An inactive inviter's reward also goes to TAPNET.
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET is_active = 0 WHERE id = ?", (self.practitioner_id,))
        self.set_plan(self.maria_id, "active", "2026-11-30")
        billing.accrue_referrals(dt.date(2026, 11, 3))
        self.assertEqual(self.balances()[self.tapnet_id], 100)  # Maria's (Liliana inactive)
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET is_active = 1 WHERE id = ?", (self.practitioner_id,))
        billing.accrue_referrals(dt.date(2026, 11, 3))
        self.assertEqual(self.balances()[self.tapnet_id], 200)  # + Liliana's (invited by admin)
        chain = billing.upline(numer.connect(), self.maria_id)
        self.assertEqual([row["id"] for row in chain], [self.practitioner_id, self.admin_id])

    def test_tapnet_link_is_reserved(self):
        import intake
        with self.assertRaises(ValueError):
            intake.validate_slug("tapnet")


class AdminBillingTest(BillingTestCase):
    def admin_post(self, path, **data):
        page = self.client.get("/admin")
        data["csrf_token"] = token_from(page.get_data(as_text=True))
        return self.client.post(path, data=data, follow_redirects=True)

    def test_admin_only(self):
        self.login("liliana@example.com")
        page = self.client.get("/dashboard")
        response = self.client.post("/admin/billing/plan", data={
            "csrf_token": token_from(page.get_data(as_text=True)), "user_id": self.practitioner_id, "plan": "active"})
        self.assertEqual(response.status_code, 403)

    def test_set_plans_and_adjust(self):
        self.login("owner@example.com")
        self.admin_post("/admin/billing/plan", user_id=self.practitioner_id, plan="complimentary")
        self.admin_post("/admin/billing/plan", user_id=self.maria_id, plan="active", paid_through="2026-11-30")
        with numer.open_db() as connection:
            rows = dict(connection.execute("SELECT id, plan FROM users").fetchall())
        self.assertEqual((rows[self.practitioner_id], rows[self.maria_id]), ("complimentary", "active"))
        html = self.admin_post("/admin/billing/adjust", user_id=self.maria_id, amount="-2.50",
                               note="Correction").get_data(as_text=True)
        self.assertIn("Adjustment of -$2.50 recorded", html)
        self.assertIn("Billing &amp; referrals", html)
        self.assertEqual(self.balances()[self.maria_id], -250)
        self.assertIn("1 paying practitioner", html)

    def test_create_platform_account(self):
        with numer.open_db() as connection:
            connection.execute("DELETE FROM settings WHERE key = 'platform_user_id'")
            connection.execute("DELETE FROM users WHERE id = ?", (self.tapnet_id,))
        self.login("owner@example.com")
        self.assertIn("Create numer.life/tapnet", self.client.get("/admin").get_data(as_text=True))
        self.admin_post("/admin/billing/platform", email="tapnet1@gmail.com", password="short")
        self.assertIsNone(billing.platform_account(numer.connect()))
        self.admin_post("/admin/billing/platform", email="tapnet1@gmail.com", password=PASSWORD)
        platform = billing.platform_account(numer.connect())
        self.assertEqual((platform["display_name"], platform["plan"], platform["round_robin"]),
                         ("TAPNET", "complimentary", 0))
        self.assertEqual(self.client.get("/tapnet").status_code, 200)  # link works for visitors
        self.assertIn("TAPNET", self.client.get("/tapnet").get_data(as_text=True))


class ReviewFollowupTest(BillingTestCase):
    def test_one_reward_per_paid_period_even_across_months(self):
        self.set_plan(self.maria_id, "active", "2026-12-01")  # activated Oct 31 for 31 days
        billing.accrue_referrals(dt.date(2026, 10, 31))
        billing.accrue_referrals(dt.date(2026, 11, 1))
        self.assertEqual(self.balances()[self.practitioner_id], 100)
        self.set_plan(self.maria_id, "active", "2027-01-01")  # renewed
        billing.accrue_referrals(dt.date(2026, 12, 2))
        self.assertEqual(self.balances()[self.practitioner_id], 200)

    def test_platform_is_an_explicit_marker_not_a_slug(self):
        with numer.open_db() as connection:
            connection.execute("DELETE FROM settings WHERE key = 'platform_user_id'")
        self.assertIsNone(billing.platform_account(numer.connect()))  # a legacy "tapnet" owner isn't TAPNET
        self.login("owner@example.com")
        page = self.client.get("/admin")
        html = self.client.post("/admin/billing/platform", follow_redirects=True, data={
            "csrf_token": token_from(page.get_data(as_text=True)), "email": "new@example.com",
            "password": PASSWORD}).get_data(as_text=True)
        self.assertIn("already used by TAPNET", html)
        self.assertIsNone(billing.platform_account(numer.connect()))

    def test_expired_paid_plans_are_not_counted_as_paying(self):
        self.set_plan(self.maria_id, "active", "2020-01-31")
        self.assertEqual(_in_app(billing.earnings_summary, self.practitioner_id)["paying"], 0)
        self.assertEqual(_in_app(billing.billing_overview)["paying"], 0)
        self.set_plan(self.maria_id, "active", "2999-01-31")
        self.assertEqual(_in_app(billing.earnings_summary, self.practitioner_id)["paying"], 1)
        self.assertEqual(_in_app(billing.billing_overview)["mrr"], 500)

    def test_cli_activation_sets_paid_through(self):
        runner = numer.app.test_cli_runner()
        result = runner.invoke(args=["set-plan", "--email", "maria@example.com", "--plan", "active"])
        self.assertIn("Plan updated.", result.output)
        with numer.open_db() as connection:
            paid_through = connection.execute("SELECT paid_through FROM users WHERE id = ?",
                                              (self.maria_id,)).fetchone()[0]
        self.assertGreater(paid_through, dt.date.today().isoformat())
        runner.invoke(args=["set-plan", "--email", "maria@example.com", "--plan", "complimentary"])
        with numer.open_db() as connection:
            self.assertIsNone(connection.execute("SELECT paid_through FROM users WHERE id = ?",
                                                 (self.maria_id,)).fetchone()[0])


class SecondReviewTest(BillingTestCase):
    def test_admin_can_adopt_an_unmarked_tapnet_account(self):
        with numer.open_db() as connection:
            connection.execute("DELETE FROM settings WHERE key = 'platform_user_id'")
        self.login("owner@example.com")
        page = self.client.get("/admin").get_data(as_text=True)
        self.assertIn("Make this the top of the referral chain", page)
        self.client.post("/admin/billing/platform/adopt", data={"csrf_token": token_from(page),
                                                                "user_id": self.tapnet_id})
        self.assertEqual(billing.platform_account(numer.connect())["id"], self.tapnet_id)
        response = self.client.post("/admin/billing/platform/adopt", data={"csrf_token": token_from(page),
                                                                           "user_id": self.maria_id})
        self.assertEqual(response.status_code, 400)

    def test_cli_rejects_a_malformed_date(self):
        result = numer.app.test_cli_runner().invoke(
            args=["set-plan", "--email", "maria@example.com", "--plan", "active", "--paid-through", "2026-02-30"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("YYYY-MM-DD", result.output)
        with numer.open_db() as connection:
            self.assertIsNone(connection.execute("SELECT paid_through FROM users WHERE id = ?",
                                                 (self.maria_id,)).fetchone()[0])


class DashboardEarningsTest(BillingTestCase):
    def test_practitioner_sees_credits_and_referrals(self):
        self.set_plan(self.maria_id, "active", "2026-11-30")
        billing.accrue_referrals(dt.date(2026, 11, 3))
        self.login("liliana@example.com")
        html = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("Earnings", html)
        self.assertIn("CAD $1.00", html)
        self.assertIn("1 paying now", html)

    def test_platform_account_can_save_its_profile(self):
        self.login("tapnet@example.com")
        page = self.client.get("/settings/profile")
        response = self.client.post("/settings/profile", data={
            "csrf_token": token_from(page.get_data(as_text=True)), "display_name": "TAPNET", "slug": "tapnet"})
        self.assertEqual(response.status_code, 302)
