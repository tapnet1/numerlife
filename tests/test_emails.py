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
        with numer.open_db() as connection:  # skip the back-off wait
            connection.execute("UPDATE email_outbox SET claimed_at = NULL")
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
        self.assertIn("Sent 16 test emails to tapnet1@gmail.com", response.get_data(as_text=True))
        self.assertEqual({m["To"] for m in self.smtp.sent}, {"tapnet1@gmail.com"})
        self.assertTrue(all(m["Subject"].startswith("[Test] ") for m in self.smtp.sent))
        self.assertIn("Alex", body(self.smtp.sent[1], "html"))  # [0] is the invitation sample
        self.assertIn("Recent emails", response.get_data(as_text=True))

    def test_works_before_any_practitioner_exists(self):
        with numer.open_db() as connection:
            connection.execute("DELETE FROM users WHERE role = 'practitioner'")
        self.login("owner@example.com")
        with mock.patch.object(mailer, "connect", self.smtp):
            response = self.post_test(to="tapnet1@gmail.com")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Sent 16 test emails", response.get_data(as_text=True))

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


class ReviewFollowupTest(EmailTestCase):
    def test_failed_mail_backs_off_and_keeps_retrying_until_expiry(self):
        self.unlocked_lead()
        failing = FakeSMTP(fail=OSError("down"))
        self.assertEqual(emails.send_pending(smtp_factory=failing)["failed"], 2)
        self.assertEqual(emails.send_pending(smtp_factory=self.smtp)["sent"], 0)  # waiting out the back-off
        for attempt in range(8):  # many failures: rows are never abandoned before expiry
            with numer.open_db() as connection:
                connection.execute("UPDATE email_outbox SET claimed_at = NULL")
            emails.send_pending(smtp_factory=failing)
        self.assertTrue(all(row["sent_at"] is None for row in self.outbox()))
        with numer.open_db() as connection:
            connection.execute("UPDATE email_outbox SET claimed_at = NULL")
        self.assertEqual(emails.send_pending(smtp_factory=self.smtp)["sent"], 2)

    def test_unsubscribe_cancels_queued_nudges(self):
        emails.set_setting("auto_nudges", "on")
        self.set_booking()
        lead = self.unlocked_lead(email="b@example.com", marketing_opt_in="yes")
        self.age_lead(lead["id"], 60)
        emails.run_nudges()
        kinds = {row["kind"] for row in self.outbox() if row["sent_at"] is None}
        self.assertEqual(kinds, {"report", "lead_alert", "practitioner_nudge", "client_nudge"})
        for kind, ident in (("client", "B@example.com"), ("practitioner", self.practitioner_id)):
            url = emails.unsubscribe_url(kind, ident).replace("http://localhost", "")
            page = self.client.get(url)
            self.client.post(url, data={"csrf_token": token_from(page.get_data(as_text=True))})
        emails.send_pending(smtp_factory=self.smtp)
        self.assertEqual(sorted(m["To"] for m in self.smtp.sent), ["b@example.com", "liliana@example.com"])
        self.assertEqual({row["last_error"] for row in self.outbox() if "nudge" in row["kind"]},
                         {"cancelled: unsubscribed"})

    def test_opt_out_after_claim_still_wins(self):
        emails.set_setting("auto_nudges", "on")
        self.set_booking()
        lead = self.unlocked_lead(email="b@example.com", marketing_opt_in="yes")
        self.age_lead(lead["id"], 60)
        emails.run_nudges()
        emails.send_pending(smtp_factory=self.smtp)  # report, alert, both nudges
        self.smtp.sent.clear()
        with numer.open_db() as connection:  # requeue the nudges, then opt out without touching the outbox
            connection.execute("INSERT INTO email_outbox (kind, to_addr, subject, body_text, body_html, lead_id,"
                               " user_id, created_at) SELECT kind, to_addr, subject, body_text, body_html, lead_id,"
                               " user_id, ? FROM email_outbox WHERE kind LIKE '%nudge'", (numer.iso(numer.now_utc()),))
            connection.execute("UPDATE leads SET marketing_opt_in = 0")
            connection.execute("UPDATE users SET email_nudges = 0")
        emails.send_pending(smtp_factory=self.smtp)
        self.assertEqual(self.smtp.sent, [])

    set_booking = NudgeTest.set_booking


class InviteEmailTest(EmailTestCase):
    def create_invite(self, **data):
        page = self.client.get("/invite")
        data["csrf_token"] = token_from(page.get_data(as_text=True))
        return self.client.post("/invite", data=data)

    def test_invite_with_email_is_sent(self):
        self.login("liliana@example.com")
        html = self.create_invite(name="Frank Moreau", email="frank@example.com").get_data(as_text=True)
        self.assertIn("emailed the invitation to <strong>frank@example.com</strong>", html)
        emails.send_pending(smtp_factory=self.smtp)
        message = self.smtp.sent[-1]
        self.assertEqual(message["To"], "frank@example.com")
        self.assertEqual(message["Reply-To"], "liliana@example.com")
        self.assertIn("Liliana invited you", message["Subject"])
        with numer.open_db() as connection:
            token = connection.execute("SELECT token, emailed_at FROM invites").fetchone()
        self.assertIn(f"http://localhost/join/{token['token']}", body(message, "html"))
        self.assertIn("Hi Frank", body(message, "plain"))
        self.assertIsNotNone(token["emailed_at"])

    def test_invite_without_email_sends_nothing(self):
        self.login("liliana@example.com")
        self.create_invite(name="Frank")
        self.assertEqual([row["kind"] for row in self.outbox()], [])

    def test_send_existing_invite_adding_an_address(self):
        self.login("liliana@example.com")
        self.create_invite(name="Frank")
        with numer.open_db() as connection:
            invite_id = connection.execute("SELECT id FROM invites").fetchone()[0]
        page = self.client.get("/invite").get_data(as_text=True)
        self.assertIn("Send by email", page)
        html = self.client.post(f"/invite/{invite_id}/email", follow_redirects=True, data={
            "csrf_token": token_from(page), "email": "frank@example.com"}).get_data(as_text=True)
        self.assertIn("Invitation emailed to frank@example.com", html)
        self.assertIn("Resend invitation to frank@example.com", html)
        self.assertEqual([(r["kind"], r["to_addr"]) for r in self.outbox()], [("invite", "frank@example.com")])

    def test_cannot_email_someone_elses_or_used_invite(self):
        self.login("liliana@example.com")
        self.create_invite(name="Frank", email="frank@example.com")
        with numer.open_db() as connection:
            invite_id = connection.execute("SELECT id FROM invites").fetchone()[0]
        self.client.post("/logout", data={"csrf_token": token_from(self.client.get("/invite").get_data(as_text=True))})
        self.login("owner@example.com")
        page = self.client.get("/invite").get_data(as_text=True)
        self.assertEqual(self.client.post(f"/invite/{invite_id}/email",
                                          data={"csrf_token": token_from(page)}).status_code, 404)

    def test_no_smtp_falls_back_to_sharing(self):
        self.login("liliana@example.com")
        with mock.patch.dict(os.environ, {"NUMER_SMTP_HOST": ""}):
            html = self.create_invite(name="Frank", email="frank@example.com").get_data(as_text=True)
        self.assertNotIn("emailed the invitation", html)
        self.assertIn("Send them the link yourself", html)
        self.assertEqual(self.outbox(), [])


class InviteEmailReviewTest(InviteEmailTest):
    def test_initial_send_is_rate_limited(self):
        self.login("liliana@example.com")
        import invites
        with mock.patch.object(invites, "INVITE_EMAILS_PER_HOUR", 1):
            self.create_invite(name="A", email="a@example.com")
            html = self.create_invite(name="B", email="b@example.com").get_data(as_text=True)
        self.assertNotIn("emailed the invitation to <strong>b@example.com", html)
        self.assertEqual([r["to_addr"] for r in self.outbox()], ["a@example.com"])

    def test_expired_invite_is_not_emailed(self):
        self.login("liliana@example.com")
        self.create_invite(name="Frank")
        with numer.open_db() as connection:
            connection.execute("UPDATE invites SET expires_at = ?", (numer.iso(numer.now_utc() - dt.timedelta(days=1)),))
            invite_id = connection.execute("SELECT id FROM invites").fetchone()[0]
        self.assertFalse(emails.queue_invite(self.practitioner_id, invite_id))

    def test_revoking_cancels_a_queued_invitation(self):
        self.login("liliana@example.com")
        self.create_invite(name="Frank", email="frank@example.com")
        with numer.open_db() as connection:
            invite_id = connection.execute("SELECT id FROM invites").fetchone()[0]
        page = self.client.get("/invite").get_data(as_text=True)
        self.client.post(f"/invite/{invite_id}/revoke", data={"csrf_token": token_from(page)})
        emails.send_pending(smtp_factory=self.smtp)
        self.assertEqual(self.smtp.sent, [])
        self.assertEqual(self.outbox()[0]["last_error"], "cancelled: invite closed")
