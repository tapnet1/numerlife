import datetime as dt
import json
import os
from unittest import mock
from zoneinfo import ZoneInfo

import bookings
import client_page
import paypal
import retention
from test_app import PASSWORD, numer, token_from
from test_orders import OrdersTestCase, completed
from test_practitioner_paypal import CLIENT_ID, SECRET

HOURS = {str(d): "09:00-17:00" for d in range(7)}


class BookingTestCase(OrdersTestCase):
    def setUp(self):
        super().setUp()
        key = mock.patch.dict(os.environ, {"NUMER_CREDENTIAL_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="})
        key.start()
        self.addCleanup(key.stop)
        import secretbox
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET paypal_client_id = ?, paypal_secret = ?, paypal_env = 'live'"
                               " WHERE id = ?", (CLIENT_ID, secretbox.encrypt(SECRET), self.practitioner_id))

    def enable(self, **overrides):
        settings = {**bookings.DEFAULTS, "enabled": True, "hours": HOURS, "details": "https://meet.example.com/lili",
                    **overrides}
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET booking = ? WHERE id = ?", (json.dumps(settings), self.practitioner_id))

    def client_token(self):
        self.captured_lead()
        with numer.open_db() as connection:
            lead = connection.execute("SELECT * FROM leads ORDER BY id DESC").fetchone()
            return client_page.ensure_page(connection, lead["id"])["token"], lead

    def practitioner(self):
        with numer.open_db() as connection:
            return connection.execute("SELECT * FROM users WHERE id = ?", (self.practitioner_id,)).fetchone()

    def slots(self):
        with numer.open_db() as connection:
            return bookings.free_slots(connection, self.practitioner())

    def checkout(self, token, slot, paypal_id="PPB1", client=None):
        client = client or self.client
        page = client.get(f"/me/{token}/book").get_data(as_text=True)
        with mock.patch.object(paypal, "create_order", return_value=(paypal_id, "https://paypal.test/approve")) as create:
            response = client.post(f"/me/{token}/book", data={"csrf_token": token_from(page), "slot": slot.isoformat()})
        return response, create

    def pay(self, paypal_id="PPB1", booking_id=1):
        with mock.patch.object(paypal, "capture_order",
                               return_value=completed(f"booking-{booking_id}", value="199.00")):
            return self.client.get(f"/paypal/booking/return?token={paypal_id}")

    def booking(self, booking_id=1):
        with numer.open_db() as connection:
            return connection.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()

    def reading(self, reading_id):
        with numer.open_db() as connection:
            return connection.execute("SELECT * FROM readings WHERE id = ?", (reading_id,)).fetchone()

    def booked(self):
        token, lead = self.client_token()
        slot = self.slots()[0]
        self.checkout(token, slot)
        self.pay()
        return token, lead, slot


class SettingsTest(BookingTestCase):
    def test_practitioner_sets_price_and_hours(self):
        self.login("liliana@example.com")
        page = self.client.get("/settings/bookings").get_data(as_text=True)
        self.assertIn('value="199.00"', page)                       # CAD $199 to start
        data = {"csrf_token": token_from(page), "enabled": "yes", "price": "150", "duration_min": "90",
                "buffer_min": "15", "notice_hours": "12", "window_days": "21", "kind": "numerology", "mode": "online",
                "details": "Zoom: https://zoom.example.com/j/1", "tz": "America/Toronto", "days_off": "2026-12-25",
                **{f"hours_{d}": "" for d in range(7)}, "hours_0": "10:00-13:00, 15:00-18:00"}
        self.client.post("/settings/bookings", data=data)
        saved = bookings.settings(self.practitioner())
        self.assertEqual((saved["enabled"], saved["price_cents"], saved["duration_min"], saved["tz"], saved["hours"]["0"]),
                         (True, 15000, 90, "America/Toronto", "10:00-13:00, 15:00-18:00"))

    def test_bad_hours_are_explained_not_saved(self):
        self.login("liliana@example.com")
        page = self.client.get("/settings/bookings").get_data(as_text=True)
        data = {"csrf_token": token_from(page), "enabled": "yes", "price": "199", "duration_min": "60",
                "buffer_min": "15", "notice_hours": "24", "window_days": "30", "kind": "numerology", "mode": "online",
                "tz": "America/Edmonton", "days_off": "", **{f"hours_{d}": "" for d in range(7)}, "hours_2": "9 to 5"}
        response = self.client.post("/settings/bookings", data=data).get_data(as_text=True)
        self.assertIn("isn&#39;t a time range", response)
        self.assertFalse(bookings.settings(self.practitioner())["enabled"])

    def test_team_members_cannot_change_bookings(self):
        frank = numer.create_user("frank@example.com", PASSWORD, "practitioner", "Frank")
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET team_of = ? WHERE id = ?", (self.practitioner_id, frank))
        self.login("frank@example.com")
        self.assertEqual(self.client.get("/settings/bookings").status_code, 403)

    def test_windows_and_free_times(self):
        self.assertEqual(bookings.parse_windows("10-12, 13:30-15"),
                         [(dt.time(10), dt.time(12)), (dt.time(13, 30), dt.time(15))])
        for bad in ("12-10", "25:00-26:00", "noon"):
            with self.assertRaises(ValueError):
                bookings.parse_windows(bad)
        self.enable(hours={str(d): "09:00-12:00" for d in range(7)}, buffer_min=0)
        now = dt.datetime(2026, 12, 1, 12, 0, tzinfo=dt.timezone.utc)   # 05:00 in Edmonton
        with numer.open_db() as connection:
            slots = bookings.free_slots(connection, self.practitioner(), now=now)
        local = [s.astimezone(ZoneInfo("America/Edmonton")) for s in slots]
        self.assertEqual(local[0], dt.datetime(2026, 12, 2, 9, 0, tzinfo=ZoneInfo("America/Edmonton")))  # 24h notice
        self.assertEqual([t.hour for t in local[:3]], [9, 10, 11])


class ClientBookingTest(BookingTestCase):
    def test_tile_shows_only_when_bookings_are_on(self):
        token, _ = self.client_token()
        self.assertNotIn("Book a session", self.client.get(f"/me/{token}").get_data(as_text=True))
        self.enable()
        page = self.client.get(f"/me/{token}").get_data(as_text=True)
        self.assertIn("Book a session", page)
        self.assertIn("CAD $199.00", page)
        choose = self.client.get(f"/me/{token}/book")
        self.assertEqual(choose.headers["Cache-Control"], "no-store")
        self.assertIn('name="slot"', choose.get_data(as_text=True))

    def test_checkout_holds_the_time_and_pays_the_practitioner(self):
        self.enable()
        token, lead = self.client_token()
        slot = self.slots()[0]
        response, create = self.checkout(token, slot)
        self.assertEqual(response.headers["Location"], "https://paypal.test/approve")
        kwargs = create.call_args.kwargs
        self.assertEqual((kwargs["cents"], kwargs["currency"], kwargs["reference"], kwargs["account"].key),
                         (19900, "CAD", "booking-1", f"user:{self.practitioner_id}"))
        self.assertNotIn(slot, self.slots())                          # held while they pay
        with numer.open_db() as connection:
            connection.execute("UPDATE bookings SET held_until = ?", (numer.iso(numer.now_utc()),))
        self.assertIn(slot, self.slots())                             # released after 30 minutes

    def test_a_taken_time_cannot_be_booked(self):
        self.enable()
        token, lead = self.client_token()
        page = self.client.get(f"/me/{token}/book").get_data(as_text=True)   # the client opens the page...
        slot = self.slots()[0]
        local = slot.astimezone(ZoneInfo("America/Edmonton")).strftime("%Y-%m-%dT%H:%M")
        with numer.open_db() as connection:   # ...and meanwhile the practitioner schedules someone then
            connection.execute("INSERT INTO readings (lead_id, practitioner_id, kind, mode, scheduled_at, duration_min,"
                               " status, created_at) VALUES (?, ?, 'numerology', 'online', ?, 60, 'scheduled', ?)",
                               (lead["id"], self.practitioner_id, local, numer.iso(numer.now_utc())))
        with mock.patch.object(paypal, "create_order", return_value=("PPB1", "https://paypal.test/approve")) as create:
            response = self.client.post(f"/me/{token}/book", data={"csrf_token": token_from(page),
                                                                   "slot": slot.isoformat()})
        self.assertFalse(create.called)
        self.assertIn("/book", response.headers["Location"])
        self.assertNotIn(slot, self.slots())

    def test_payment_books_the_reading_and_tells_everyone(self):
        self.enable()
        token, lead, slot = self.booked()
        booking = self.booking()
        self.assertEqual((booking["status"], booking["paypal_capture_id"], booking["conflict"]), ("paid", "CAP1", 0))
        reading = self.reading(booking["reading_id"])
        local = slot.astimezone(ZoneInfo("America/Edmonton")).strftime("%Y-%m-%dT%H:%M")
        self.assertEqual((reading["status"], reading["scheduled_at"], reading["duration_min"]), ("scheduled", local, 60))
        self.assertEqual(self.ledger()[(self.practitioner_id, "platform_fee")], -4975)   # 25% on the invoice
        confirmed = self.outbox("booking_confirmed")
        self.assertEqual(len(confirmed), 1)
        self.assertIn("calendar.google.com", confirmed[0]["body_text"])
        self.assertIn("appointment.ics", confirmed[0]["body_text"])
        self.assertIn("https://meet.example.com/lili", confirmed[0]["body_text"])
        alert = self.outbox("booking_alert")
        self.assertEqual([a["to_addr"] for a in alert], ["liliana@example.com"])
        with numer.open_db() as connection:
            push = [json.loads(r[0]) for r in connection.execute("SELECT payload FROM push_outbox")]
        self.assertTrue(any(p["title"] == "New paid booking" for p in push))
        page = self.client.get(f"/b/{booking['token']}")
        self.assertEqual(page.headers["X-Robots-Tag"], "noindex, nofollow")
        html = page.get_data(as_text=True)
        self.assertIn("Confirmed", html)
        self.assertIn("meet.example.com/lili", html)
        ics = self.client.get(f"/b/{booking['token']}/appointment.ics")
        self.assertEqual(ics.mimetype, "text/calendar")
        body = ics.get_data(as_text=True)
        self.assertIn("UID:booking-1@numer.life", body)
        self.assertIn(f"DTSTART:{slot.strftime('%Y%m%dT%H%M%SZ')}", body)
        self.assertIn("STATUS:CONFIRMED", body)
        self.assertIn("✓", self.client.get(f"/me/{token}").get_data(as_text=True))  # upcoming on the client page

    def test_paid_after_the_time_was_taken_asks_the_practitioner_to_reschedule(self):
        self.enable()
        token, lead = self.client_token()
        slot = self.slots()[0]
        self.checkout(token, slot)
        local = slot.astimezone(ZoneInfo("America/Edmonton")).strftime("%Y-%m-%dT%H:%M")
        with numer.open_db() as connection:
            connection.execute("UPDATE bookings SET held_until = ?", (numer.iso(numer.now_utc()),))
            connection.execute("INSERT INTO readings (lead_id, practitioner_id, kind, mode, scheduled_at, duration_min,"
                               " status, created_at) VALUES (?, ?, 'numerology', 'online', ?, 60, 'scheduled', ?)",
                               (lead["id"], self.practitioner_id, local, numer.iso(numer.now_utc())))
        self.pay()
        booking = self.booking()
        self.assertEqual((booking["status"], booking["conflict"], booking["reading_id"]), ("paid", 1, None))
        self.assertEqual(len(self.outbox("booking_conflict")), 1)
        self.assertIn("needs a new time", self.outbox("booking_alert")[0]["subject"])

    def test_cancelled_checkout_releases_the_time(self):
        self.enable()
        token, _ = self.client_token()
        slot = self.slots()[0]
        self.checkout(token, slot)
        response = self.client.get("/paypal/booking/cancel?token=PPB1")
        self.assertIn(f"/me/{token}/book", response.headers["Location"])
        self.assertEqual(self.booking()["status"], "cancelled")
        self.assertIn(slot, self.slots())

    def test_job_captures_a_buyer_who_never_came_back(self):
        self.enable()
        token, _ = self.client_token()
        self.checkout(token, self.slots()[0])
        with numer.open_db() as connection:
            connection.execute("UPDATE bookings SET created_at = ?",
                               (numer.iso(numer.now_utc() - dt.timedelta(minutes=10)),))
        with mock.patch.object(paypal, "get_order", return_value={"status": "APPROVED"}), \
                mock.patch.object(paypal, "capture_order", return_value=completed("booking-1", value="199.00")):
            result = bookings.run_job()
        self.assertEqual(result["paid"], 1)
        self.assertEqual(self.booking()["status"], "paid")


class RemindersAndChangesTest(BookingTestCase):
    def test_day_and_hour_reminders_once_each(self):
        self.enable()
        _, _, slot = self.booked()
        with numer.open_db() as connection:   # booked well ahead: no reminder flags yet
            connection.execute("UPDATE bookings SET reminded_day_for = NULL, reminded_hour_for = NULL")
        with numer.app.app_context():
            self.assertEqual(bookings.send_reminders(now=slot - dt.timedelta(hours=23)), {"day": 1, "hour": 0})
            self.assertEqual(bookings.send_reminders(now=slot - dt.timedelta(hours=22)), {"day": 0, "hour": 0})
            self.assertEqual(bookings.send_reminders(now=slot - dt.timedelta(minutes=60)), {"day": 0, "hour": 1})
            self.assertEqual(bookings.send_reminders(now=slot - dt.timedelta(minutes=30)), {"day": 0, "hour": 0})
        self.assertEqual(len(self.outbox("booking_day")), 1)
        self.assertEqual(len(self.outbox("booking_hour")), 1)
        with numer.open_db() as connection:
            push = [json.loads(r[0]) for r in connection.execute("SELECT payload FROM push_outbox")]
        self.assertTrue(any(p["title"] == "Session in about an hour" for p in push))

    def test_booking_inside_24_hours_gets_no_tomorrow_email(self):
        self.enable(notice_hours=2)
        token, lead = self.client_token()
        soon = [s for s in self.slots() if s - numer.now_utc() < dt.timedelta(hours=20)]
        if not soon:
            self.skipTest("no slot within 20 hours at this time of day")
        self.checkout(token, soon[0])
        self.pay()
        with numer.app.app_context():
            self.assertEqual(bookings.send_reminders(now=soon[0] - dt.timedelta(hours=3))["day"], 0)

    def save_reading(self, reading_id, lead_id, **fields):
        page = self.client.get(f"/leads/{lead_id}/readings/{reading_id}").get_data(as_text=True)
        reading = self.reading(reading_id)
        data = {"csrf_token": token_from(page), "status": reading["status"], "kind": reading["kind"],
                "mode": reading["mode"], "date": reading["scheduled_at"][:10], "time": reading["scheduled_at"][11:],
                "duration": str(reading["duration_min"]), "notes": ""}
        data.update(fields)
        return self.client.post(f"/leads/{lead_id}/readings/{reading_id}", data=data)

    def test_moving_or_cancelling_the_reading_tells_the_client(self):
        self.enable()
        _, lead, slot = self.booked()
        booking = self.booking()
        self.login("liliana@example.com")
        page = self.client.get(f"/leads/{lead['id']}/readings/{booking['reading_id']}").get_data(as_text=True)
        self.assertIn("Booked and paid online", page)
        new_local = (slot + dt.timedelta(days=1)).astimezone(ZoneInfo("America/Edmonton"))
        self.save_reading(booking["reading_id"], lead["id"], date=new_local.strftime("%Y-%m-%d"),
                          time=new_local.strftime("%H:%M"))
        self.assertEqual(len(self.outbox("booking_moved")), 1)
        self.assertEqual(self.booking()["sequence"], 1)
        ics = self.client.get(f"/b/{booking['token']}/appointment.ics").get_data(as_text=True)
        self.assertIn(f"DTSTART:{(slot + dt.timedelta(days=1)).strftime('%Y%m%dT%H%M%SZ')}", ics)
        self.assertIn("SEQUENCE:1", ics)
        self.save_reading(booking["reading_id"], lead["id"], notes="Prepared.")    # no change of time: no email
        self.assertEqual(len(self.outbox("booking_moved")), 1)
        self.save_reading(booking["reading_id"], lead["id"], status="cancelled")
        self.assertEqual(len(self.outbox("booking_cancelled")), 1)
        self.assertIn("STATUS:CANCELLED", self.client.get(f"/b/{booking['token']}/appointment.ics").get_data(as_text=True))
        self.assertIn(slot, self.slots())                              # the time is free again

    def test_admin_marks_a_refund(self):
        self.enable()
        self.booked()
        booking = self.booking()
        self.login("owner@example.com")
        page = self.client.get(f"/leads/{booking['lead_id']}/readings/{booking['reading_id']}").get_data(as_text=True)
        self.client.post(f"/admin/bookings/{booking['id']}/refunded", data={"csrf_token": token_from(page)})
        self.assertEqual(self.booking()["status"], "refunded")
        self.assertEqual(self.reading(booking["reading_id"])["status"], "cancelled")
        self.assertEqual(self.ledger()[(self.practitioner_id, "platform_fee")]
                         + self.ledger().get((self.practitioner_id, "adjustment"), 0), 0)


class HousekeepingTest(BookingTestCase):
    def test_booking_paths_cannot_be_practitioner_links(self):
        import intake
        for slug in ("b", "book", "bookings"):
            with self.assertRaises(ValueError):
                intake.validate_slug(slug)

    def test_deleting_a_client_deletes_their_bookings(self):
        self.enable()
        _, lead, _ = self.booked()
        with numer.open_db() as connection:
            retention.delete_lead(connection, lead["id"])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM bookings").fetchone()[0], 0)

    def test_a_future_booking_keeps_a_client(self):
        self.enable()
        _, lead, _ = self.booked()
        with numer.open_db() as connection:
            old = numer.iso(numer.now_utc() - dt.timedelta(days=500))
            for table, column in (("leads", "created_at"), ("leads", "captured_at"), ("leads", "updated_at"),
                                  ("leads", "viewed_at"), ("leads", "client_at"), ("leads", "client_seen_at"),
                                  ("readings", "created_at"), ("readings", "updated_at"), ("bookings", "created_at"),
                                  ("bookings", "updated_at"), ("email_outbox", "created_at"),
                                  ("readings", "scheduled_at")):
                connection.execute(f"UPDATE {table} SET {column} = ?", (old,))
            self.assertNotIn(lead["id"], retention.expired_leads(connection))   # bookings.starts_at is ahead
