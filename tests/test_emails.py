import datetime as dt
import json
import os
import re
import smtplib
from unittest import mock

from test_app import PASSWORD, numer, token_from
from test_intake import IntakeTestCase

import emails
import mailer

SMTP_ENV = {"NUMER_SMTP_HOST": "mail.example.com", "NUMER_SMTP_PORT": "465", "NUMER_SMTP_USER": "reports@numer.life",
            "NUMER_SMTP_PASSWORD": "s3cret-pass", "NUMER_MAIL_FROM": "numer.life <reports@numer.life>"}


class FakeSMTP:
    def __init__(self, fail=None):
        self.sent, self.fail = [], fail

    def __call__(self):
        return self

    def send_message(self, message):
        if self.fail:
            raise self.fail
        self.sent.append(message)

    def quit(self):
        pass


def body(message, subtype):
    return message.get_body(preferencelist=(subtype,)).get_content()


class EmailTestCase(IntakeTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, SMTP_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.smtp = FakeSMTP()

    def unlocked_lead(self, email="john@example.com", **fields):
        response = self.submit("/liliana")
        token = response.headers["Location"].rsplit("/", 1)[-1]
        self.unlock(token, email=email, **fields)
        return self.leads()[-1]

    def outbox(self):
        with numer.open_db() as connection:
            return connection.execute("SELECT * FROM email_outbox ORDER BY id").fetchall()

    def age_lead(self, lead_id, hours):
        stamp = numer.iso(numer.now_utc() - dt.timedelta(hours=hours))
        with numer.open_db() as connection:
            connection.execute("UPDATE leads SET captured_at = ? WHERE id = ?", (stamp, lead_id))


class UnlockEmailTest(EmailTestCase):
    def test_unlock_sends_report_to_visitor_and_alert_to_practitioner(self):
        lead = self.unlocked_lead()
        kinds = [(row["kind"], row["to_addr"]) for row in self.outbox()]
        self.assertEqual(kinds, [("report", "john@example.com"), ("lead_alert", "liliana@example.com")])
        stats = emails.send_pending(smtp_factory=self.smtp)
        self.assertEqual(stats["sent"], 2)
        report, alert = self.smtp.sent
        self.assertEqual(report["From"].addresses[0].addr_spec, "reports@numer.life")
        self.assertEqual(report["From"].addresses[0].display_name, "numer.life")
        self.assertIn("Life Path 5", report["Subject"])
        html, text = body(report, "html"), body(report, "plain")
        self.assertIn(f"http://localhost/r/{lead['token']}", html)
        self.assertIn(f"http://localhost/r/{lead['token']}", text)
        self.assertIn("The Adventurer", html)
        self.assertIn("Liliana", html)
        self.assertIn(f"http://localhost/leads/{lead['id']}", body(alert, "html"))
        self.assertTrue(all(row["sent_at"] for row in self.outbox()))
        self.assertEqual(emails.send_pending(smtp_factory=self.smtp)["sent"], 0)  # never sent twice

    def test_practitioner_can_turn_off_alerts(self):
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET email_alerts = 0 WHERE id = ?", (self.practitioner_id,))
        self.unlocked_lead()
        self.assertEqual([row["kind"] for row in self.outbox()], ["report"])

    def test_not_configured_waits_without_sending(self):
        self.unlocked_lead()
        with mock.patch.dict(os.environ, {"NUMER_SMTP_HOST": ""}):
            self.assertEqual(emails.send_pending(smtp_factory=self.smtp)["skipped"], "SMTP not configured")
        self.assertFalse(any(row["sent_at"] for row in self.outbox()))

    def test_failures_are_retried_and_never_expose_the_password(self):
        self.unlocked_lead()
        failing = FakeSMTP(fail=smtplib.SMTPAuthenticationError(535, b"bad credentials s3cret-pass"))
        self.assertEqual(emails.send_pending(smtp_factory=failing)["failed"], 2)
        rows = self.outbox()
        self.assertTrue(all(row["sent_at"] is None for row in rows))
        self.assertIn("sign-in failed", rows[0]["last_error"])
        self.assertNotIn("s3cret", rows[0]["last_error"])
        self.assertEqual(emails.send_pending(smtp_factory=self.smtp)["sent"], 2)

    def test_stale_queued_email_expires(self):
        self.unlocked_lead()
        with numer.open_db() as connection:
            connection.execute("UPDATE email_outbox SET created_at = ?",
                               (numer.iso(numer.now_utc() - dt.timedelta(days=4)),))
        stats = emails.send_pending(smtp_factory=self.smtp)
        self.assertEqual((stats["sent"], stats["expired"]), (0, 2))


class NudgeTest(EmailTestCase):
    def test_off_by_default(self):
        self.assertEqual(emails.run_nudges()["skipped"], "automatic nudges are off")

    def test_practitioner_reminded_about_waiting_leads_at_most_every_three_days(self):
        emails.set_setting("auto_nudges", "on")
        lead = self.unlocked_lead()
        self.assertEqual(emails.run_nudges()["practitioners"], 0)  # too fresh
        self.age_lead(lead["id"], 50)
        self.assertEqual(emails.run_nudges()["practitioners"], 1)
        self.assertEqual(emails.run_nudges()["practitioners"], 0)  # already reminded
        nudge = [row for row in self.outbox() if row["kind"] == "practitioner_nudge"][0]
        self.assertEqual(nudge["to_addr"], "liliana@example.com")
        self.assertIn("/unsubscribe/", nudge["body_html"])
        self.assertIn("List-Unsubscribe", nudge["headers"])

    def test_contacted_leads_do_not_trigger_reminders(self):
        emails.set_setting("auto_nudges", "on")
        lead = self.unlocked_lead()
        self.age_lead(lead["id"], 50)
        with numer.open_db() as connection:
            connection.execute("UPDATE leads SET stage = 'contacted' WHERE id = ?", (lead["id"],))
        self.assertEqual(emails.run_nudges(), {"practitioners": 0, "clients": 0})

    def set_booking(self):
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET profile = ? WHERE id = ?",
                               (json.dumps({"booking_url": "https://calendly.com/liliana"}), self.practitioner_id))

    def test_client_nudge_needs_opt_in_and_booking_link(self):
        emails.set_setting("auto_nudges", "on")
        no_opt_in = self.unlocked_lead(email="a@example.com")
        opted = self.unlocked_lead(email="b@example.com", marketing_opt_in="yes")
        for lead in (no_opt_in, opted):
            self.age_lead(lead["id"], 60)
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET email_nudges = 0")
        self.assertEqual(emails.run_nudges()["clients"], 0)  # no booking link yet
        self.set_booking()
        self.assertEqual(emails.run_nudges()["clients"], 1)
        self.assertEqual(emails.run_nudges()["clients"], 0)
        nudge = [row for row in self.outbox() if row["kind"] == "client_nudge"][0]
        self.assertEqual(nudge["to_addr"], "b@example.com")
        self.assertIn("https://calendly.com/liliana", nudge["body_html"])

    def test_unsubscribe_links(self):
        url = emails.unsubscribe_url("client", "B@example.com").replace("http://localhost", "")
        self.unlocked_lead(email="b@example.com", marketing_opt_in="yes")
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertIn("noindex", page.headers.get("X-Robots-Tag", ""))
        self.assertEqual(self.leads()[-1]["marketing_opt_in"], 1)  # a GET (link scanner) changes nothing
        self.client.post(url, data={"csrf_token": token_from(page.get_data(as_text=True))})
        self.assertEqual(self.leads()[-1]["marketing_opt_in"], 0)
        url = emails.unsubscribe_url("practitioner", self.practitioner_id).replace("http://localhost", "")
        page = self.client.get(url)
        self.client.post(url, data={"csrf_token": token_from(page.get_data(as_text=True))})
        with numer.open_db() as connection:
            self.assertEqual(connection.execute("SELECT email_nudges FROM users WHERE id = ?",
                                                (self.practitioner_id,)).fetchone()[0], 0)
        self.assertEqual(self.client.get("/unsubscribe/forged-token").status_code, 404)


class AdminTestSendTest(EmailTestCase):
    def post_test(self, **data):
        page = self.client.get("/admin")
        data["csrf_token"] = token_from(page.get_data(as_text=True))
        return self.client.post("/admin/email/test", data=data, follow_redirects=True)

    def test_only_admins(self):
        self.login("liliana@example.com")
        page = self.client.get("/dashboard")
        response = self.client.post("/admin/email/test", data={"csrf_token": token_from(page.get_data(as_text=True))})
        self.assertEqual(response.status_code, 403)

    def test_sends_every_sample_to_the_chosen_address(self):
        self.login("owner@example.com")
        with mock.patch.object(mailer, "connect", self.smtp):
            response = self.post_test(to="tapnet1@gmail.com")
        self.assertIn("Sent 4 test emails to tapnet1@gmail.com", response.get_data(as_text=True))
        self.assertEqual({m["To"] for m in self.smtp.sent}, {"tapnet1@gmail.com"})
        self.assertTrue(all(m["Subject"].startswith("[Test] ") for m in self.smtp.sent))
        self.assertIn("Alex", body(self.smtp.sent[0], "html"))
        self.assertIn("Recent emails", response.get_data(as_text=True))

    def test_reports_problems(self):
        self.login("owner@example.com")
        with mock.patch.object(mailer, "connect", FakeSMTP(fail=OSError("down"))):
            response = self.post_test(to="tapnet1@gmail.com", kinds="report")
        self.assertIn("could not reach the mail server", response.get_data(as_text=True))
        with mock.patch.dict(os.environ, {"NUMER_SMTP_HOST": ""}):
            response = self.post_test(to="tapnet1@gmail.com")
        self.assertIn("isn&#39;t set up yet", response.get_data(as_text=True))

    def test_nudge_switch(self):
        self.login("owner@example.com")
        page = self.client.get("/admin")
        self.client.post("/admin/email/settings", data={"csrf_token": token_from(page.get_data(as_text=True)),
                                                          "auto_nudges": "on"})
        self.assertTrue(emails.nudges_enabled())


class EmailPreferencesTest(EmailTestCase):
    def test_profile_saves_email_preferences(self):
        self.login("liliana@example.com")
        page = self.client.get("/settings/profile")
        html = page.get_data(as_text=True)
        self.assertIn('name="email_alerts" value="yes" checked', html)
        self.client.post("/settings/profile", data={"csrf_token": token_from(html), "display_name": "Liliana",
                                                    "slug": "liliana", "email_nudges": "yes"})
        with numer.open_db() as connection:
            row = connection.execute("SELECT email_alerts, email_nudges FROM users WHERE id = ?",
                                     (self.practitioner_id,)).fetchone()
        self.assertEqual((row[0], row[1]), (0, 1))
