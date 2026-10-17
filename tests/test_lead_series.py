import datetime as dt
import re

from test_app import numer, token_from
from test_intake import IntakeTestCase

import emails
import lead_series


def later(days, hours=0):
    return numer.now_utc() + dt.timedelta(days=days, hours=hours)


class LeadSeriesTest(IntakeTestCase):
    def captured_lead(self, opt_in=True, lang_header="es-MX,es;q=0.9"):
        page = self.client.get("/liliana", headers={"Accept-Language": lang_header})
        from test_intake import form_data
        data = form_data()
        data["csrf_token"] = token_from(page.get_data(as_text=True))
        response = self.client.post("/report", data=data, headers={"Accept-Language": lang_header})
        token = response.headers["Location"].rsplit("/", 1)[-1]
        extra = {"marketing_opt_in": "yes"} if opt_in else {}
        self.unlock(token, **extra)
        return self.leads()[-1]

    def series(self):
        with numer.open_db() as connection:
            return connection.execute("SELECT * FROM email_outbox WHERE kind = 'lead_series' ORDER BY id").fetchall()

    def turn_on(self):
        emails.set_setting("lead_series", "on")

    def test_language_comes_from_the_browser(self):
        self.assertEqual(self.captured_lead()["lang"], "es")
        self.assertEqual(self.captured_lead(lang_header="en-CA,en;q=0.9")["lang"], "en")
        self.assertEqual(self.captured_lead(lang_header="fr-FR")["lang"], "en")

    def test_off_by_default_and_needs_opt_in(self):
        self.captured_lead()
        self.assertEqual(lead_series.run(later(2))["sent"], 0)
        self.turn_on()
        self.captured_lead(opt_in=False)
        self.assertEqual(lead_series.run(later(2))["sent"], 1)  # only the opted-in lead

    def test_series_runs_in_order_with_gaps_and_spanish(self):
        self.turn_on()
        lead = self.captured_lead()
        self.assertEqual(lead_series.run(later(0, 12))["sent"], 0)        # day 1 not reached
        sent = 0
        for day in range(1, 30):
            sent += lead_series.run(later(day, 1))["sent"]
            with numer.open_db() as connection:  # delivered
                connection.execute("UPDATE email_outbox SET sent_at = created_at WHERE sent_at IS NULL")
        rows = self.series()
        self.assertGreaterEqual(len(rows), 6)
        self.assertEqual(rows[0]["campaign"], "expression")
        self.assertEqual(rows[-1]["campaign"], "last")
        self.assertIn("tu Expresión", rows[0]["subject"])
        self.assertIn("Hola John:", rows[0]["body_text"])
        self.assertEqual(len({r["subject"] for r in rows}), len(rows))    # every subject is different
        self.assertEqual(lead_series.run(later(40))["sent"], 0)           # finished
        times = [r["created_at"] for r in rows]
        self.assertEqual(times, sorted(times))

    def test_tracking_opens_clicks_and_unsubscribe(self):
        self.turn_on()
        lead = self.captured_lead(lang_header="en")
        lead_series.run(later(2))
        row = self.series()[0]
        self.assertIn("your Expression", row["subject"])
        pixel = self.client.get(f"/e/o/{row['track']}.gif")
        self.assertEqual(pixel.mimetype, "image/gif")
        click = re.search(r"/e/c/([^\s\"]+)", row["body_text"]).group(1)
        response = self.client.get(f"/e/c/{click}")
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/r/{lead['token']}", response.headers["Location"])
        self.assertEqual(self.client.get("/e/c/forged").status_code, 404)
        with numer.app.test_request_context():
            stats = {s["key"]: s for s in lead_series.step_stats()}
        self.assertEqual(stats["expression"]["sent"], 0)                    # rates only count sent mail
        with numer.open_db() as connection:
            connection.execute("UPDATE email_outbox SET sent_at = created_at WHERE id = ?", (row["id"],))
        with numer.app.test_request_context():
            stats = {s["key"]: s for s in lead_series.step_stats()}
        self.assertEqual((stats["expression"]["sent"], stats["expression"]["opened"],
                          stats["expression"]["clicked"]), (1, 1, 1))
        unsub = re.search(r"/unsubscribe/(\S+)", row["body_text"]).group(1)
        page = self.client.get(f"/unsubscribe/{unsub}").get_data(as_text=True)
        self.client.post(f"/unsubscribe/{unsub}", data={"csrf_token": token_from(page)})
        with numer.app.test_request_context():
            stats = {s["key"]: s for s in lead_series.step_stats()}
        self.assertEqual(stats["expression"]["unsubscribed"], 1)
        self.assertEqual(lead_series.run(later(4))["sent"], 0)

    def test_stops_when_client_or_reading(self):
        self.turn_on()
        lead = self.captured_lead()
        lead_series.run(later(2))
        with numer.open_db() as connection:
            connection.execute("UPDATE leads SET stage = 'client' WHERE id = ?", (lead["id"],))
            row = self.series()[0]
            self.assertFalse(emails._still_wanted(connection, row))
        self.assertEqual(lead_series.run(later(4))["sent"], 0)

    def test_practitioner_changes_email_language(self):
        lead = self.captured_lead()
        self.login("liliana@example.com")
        page = self.client.get(f"/leads/{lead['id']}").get_data(as_text=True)
        self.assertIn("Follow-up emails:", page)
        self.client.post(f"/leads/{lead['id']}", data={"csrf_token": token_from(page), "stage": "new", "lang": "en"})
        self.assertEqual(self.leads()[-1]["lang"], "en")

    def test_admin_results_and_switch(self):
        self.login("owner@example.com")
        page = self.client.get("/admin").get_data(as_text=True)
        self.assertIn("Follow-up series results", page)
        self.client.post("/admin/email/settings", data={"csrf_token": token_from(page), "lead_series": "on"})
        self.assertTrue(lead_series.enabled())

    def test_relations(self):
        import series_content as sc
        self.assertEqual(sc.relation(4, 8), "support")
        self.assertEqual(sc.relation(4, 5), "tension")
        self.assertEqual(sc.relation(11, 2), "support")
        self.assertEqual(sc.relation(7, 7), "same")
        for lang in sc.LANGS:
            self.assertEqual(set(sc.TRAITS[lang]), set(sc.TRAITS["en"]))
            self.assertEqual(set(sc.TEXT[lang]), set(sc.TEXT["en"]))

    def test_no_tracking_after_unsubscribe_and_links_outlast_series(self):
        import intake
        self.assertGreaterEqual(intake.REPORT_LINK_DAYS * 24,
                                (lead_series.START_WINDOW.days + lead_series.STEPS[-1][1] + 7) * 24)
        self.turn_on()
        lead = self.captured_lead(lang_header="en")
        lead_series.run(later(2))
        row = self.series()[0]
        with numer.open_db() as connection:
            connection.execute("UPDATE leads SET marketing_opt_in = 0 WHERE id = ?", (lead["id"],))
        self.client.get(f"/e/o/{row['track']}.gif")
        with numer.open_db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM email_events").fetchone()[0], 0)

    def test_metrics_count_delivered_mail_and_keep_conversions(self):
        self.turn_on()
        lead = self.captured_lead(lang_header="en")
        lead_series.run(later(2))
        with numer.open_db() as connection:
            connection.execute("UPDATE email_outbox SET sent_at = created_at, last_error = 'expired'")
        with numer.app.test_request_context():
            self.assertEqual(lead_series.step_stats()[0]["sent"], 0)
            self.assertEqual(lead_series.conversions()["reached"], 0)
        with numer.open_db() as connection:
            connection.execute("UPDATE email_outbox SET last_error = ''")
        self.login("liliana@example.com")
        for stage in ("client", "archived"):
            page = self.client.get(f"/leads/{lead['id']}").get_data(as_text=True)
            self.client.post(f"/leads/{lead['id']}", data={"csrf_token": token_from(page), "stage": stage})
        with numer.app.test_request_context():
            self.assertEqual(lead_series.conversions(), {"reached": 1, "converted": 1})

    def test_report_link_counts_from_unlock(self):
        lead = self.captured_lead()
        with numer.open_db() as connection:
            connection.execute("UPDATE leads SET created_at = ? WHERE id = ?",
                               ((numer.now_utc() - dt.timedelta(days=100)).isoformat(), lead["id"]))
        self.assertEqual(self.client.get(f"/r/{lead['token']}").status_code, 200)

    def test_waits_for_previous_delivery_and_keeps_old_results(self):
        self.turn_on()
        self.captured_lead(lang_header="en")
        self.assertEqual(lead_series.run(later(2))["sent"], 1)
        self.assertEqual(lead_series.run(later(4))["sent"], 0)      # first one still waiting in the outbox
        with numer.open_db() as connection:
            connection.execute("UPDATE email_outbox SET sent_at = created_at, created_at = '2000-01-01T00:00:00+00:00'")
        with numer.open_db() as connection:
            emails.purge_old(connection)
        rows = self.series()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["body_html"], "")                  # content dropped, record kept
