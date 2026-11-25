import datetime as dt
import json
from unittest import mock
from zoneinfo import ZoneInfo

import bookings
import calendar_sync
from test_app import PASSWORD, numer, token_from
from test_bookings import BookingTestCase

EDMONTON = ZoneInfo("America/Edmonton")
GOOGLE = "https://calendar.google.com/calendar/ical/liliana%40gmail.com/private-abc/basic.ics"


def feed(*events: str) -> bytes:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "".join(events) + "END:VCALENDAR\r\n").encode()


def event(uid, start, end, extra=""):
    return f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTART{start}\r\nDTEND{end}\r\n{extra}SUMMARY:Private\r\nEND:VEVENT\r\n"


class CalendarLinkTest(BookingTestCase):
    def test_only_known_calendar_hosts_over_https(self):
        self.assertEqual(calendar_sync.normalize("webcal://p12-caldav.icloud.com/published/2/abc"),
                         "https://p12-caldav.icloud.com/published/2/abc")
        self.assertEqual(calendar_sync.normalize(GOOGLE), GOOGLE)
        for bad in ("http://calendar.google.com/x.ics", "https://evil.example.com/x.ics", "https://localhost/x",
                    "https://calendar.google.com.evil.com/x", "https://user:pw@calendar.google.com/x",
                    "https://calendar.google.com:8443/x", "file:///etc/passwd", ""):
            with self.assertRaises(calendar_sync.FeedError):
                calendar_sync.normalize(bad)

    def test_busy_times_from_a_feed(self):
        data = feed(
            event("weekly", ";TZID=America/Edmonton:20261201T100000", ";TZID=America/Edmonton:20261201T110000",
                  "RRULE:FREQ=WEEKLY;COUNT=2\r\n"),
            event("free", ";VALUE=DATE:20261203", ";VALUE=DATE:20261204", "TRANSP:TRANSPARENT\r\n"),
            event("allday", ";VALUE=DATE:20261205", ";VALUE=DATE:20261206"),
            event("cancelled", ":20261202T150000Z", ":20261202T160000Z", "STATUS:CANCELLED\r\n"),
            event("floating", ":20261204T090000", ":20261204T093000"))
        start = dt.datetime(2026, 11, 30, tzinfo=dt.timezone.utc)
        busy = calendar_sync.busy_times(data, EDMONTON, start, start + dt.timedelta(days=30))
        local = [(b.astimezone(EDMONTON).strftime("%m-%d %H:%M"), e.astimezone(EDMONTON).strftime("%m-%d %H:%M"))
                 for b, e in busy]
        self.assertEqual(local, [("12-01 10:00", "12-01 11:00"), ("12-04 09:00", "12-04 09:30"),
                                 ("12-05 00:00", "12-06 00:00"), ("12-08 10:00", "12-08 11:00")])
        with self.assertRaises(calendar_sync.FeedError):
            calendar_sync.busy_times(b"<html>not a calendar</html>", EDMONTON, start, start + dt.timedelta(days=3))

    def connect(self, data, url=GOOGLE):
        if not getattr(self, "_signed_in", False):
            self.login("liliana@example.com")
            self._signed_in = True
        page = self.client.get("/settings/bookings").get_data(as_text=True)
        with mock.patch.object(calendar_sync, "fetch", return_value=data) as fetched:
            response = self.client.post("/settings/bookings/calendar",
                                        data={"csrf_token": token_from(page), "action": "connect", "url": url})
        return response, fetched

    def test_busy_times_in_her_calendar_cannot_be_booked(self):
        self.enable()
        first = self.slots()[0]
        stamp = lambda m: m.strftime("%Y%m%dT%H%M%SZ")  # noqa: E731
        _, fetched = self.connect(feed(event("dentist", f":{stamp(first)}", f":{stamp(first + dt.timedelta(minutes=30))}")))
        self.assertEqual(fetched.call_args.args[0], GOOGLE)
        self.assertNotIn(first, self.slots())
        with numer.open_db() as connection:
            stored = connection.execute("SELECT * FROM busy_feeds").fetchone()
            self.assertNotIn("private-abc", stored["url"])                        # the link is encrypted
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM busy_times").fetchone()[0], 1)
        page = self.client.get("/settings/bookings").get_data(as_text=True)
        self.assertIn("Connected", page)

    def test_a_broken_link_keeps_the_last_busy_times(self):
        self.enable()
        first = self.slots()[0]
        stamp = lambda m: m.strftime("%Y%m%dT%H%M%SZ")  # noqa: E731
        self.connect(feed(event("x", f":{stamp(first)}", f":{stamp(first + dt.timedelta(minutes=60))}")))
        with numer.open_db() as connection:
            connection.execute("UPDATE busy_feeds SET fetched_at = '2000-01-01T00:00:00+00:00'")
        with mock.patch.object(calendar_sync, "fetch", side_effect=calendar_sync.FeedError("refused")), \
                numer.app.app_context():
            self.assertEqual(calendar_sync.refresh_due(), {"feeds": 1, "feed_errors": 1})
        self.assertNotIn(first, self.slots())                                    # still blocked
        self.assertIn("refused", self.client.get("/settings/appointments").get_data(as_text=True))

    def feed_row(self):
        with numer.open_db() as connection:
            return connection.execute("SELECT * FROM busy_feeds").fetchone()

    def test_bad_link_is_not_saved_and_can_be_removed(self):
        self.enable()
        response, fetched = self.connect(b"", url="https://example.com/cal.ics")
        self.assertFalse(fetched.called)
        self.assertIsNone(self.feed_row())
        self.connect(feed())
        page = self.client.get("/settings/bookings").get_data(as_text=True)
        self.client.post("/settings/bookings/calendar", data={"csrf_token": token_from(page), "action": "disconnect"})
        self.assertIsNone(self.feed_row())

    def test_job_refreshes_links(self):
        self.enable()
        self.connect(feed())
        with numer.open_db() as connection:
            connection.execute("UPDATE busy_feeds SET fetched_at = '2000-01-01T00:00:00+00:00'")
        with mock.patch.object(calendar_sync, "fetch", return_value=feed()):
            self.assertEqual(bookings.run_job()["feeds"], 1)


class AppointmentsTest(BookingTestCase):
    def test_booked_session_shows_with_add_to_my_calendar(self):
        self.enable()
        _, lead, slot = self.booked()
        booking = self.booking()
        with numer.open_db() as connection:
            push = [json.loads(r[0]) for r in connection.execute("SELECT payload FROM push_outbox")]
        new = next(p for p in push if p["title"] == "New paid booking")
        self.assertEqual(new["url"], f"/settings/appointments/{booking['reading_id']}")   # tap the push → add it
        self.assertIn(f"/settings/appointments/{booking['reading_id']}", self.outbox("booking_alert")[0]["body_text"])
        self.login("liliana@example.com")
        listing = self.client.get("/settings/appointments").get_data(as_text=True)
        self.assertIn("Marco James Bonanni", listing)
        self.assertIn("Paid online", listing)
        detail = self.client.get(new["url"]).get_data(as_text=True)
        self.assertIn("Add to my calendar", detail)
        self.assertIn("calendar.google.com", detail)
        ics = self.client.get(f"/settings/appointments/{booking['reading_id']}.ics")
        self.assertEqual(ics.mimetype, "text/calendar")
        body = ics.get_data(as_text=True)
        self.assertIn(f"UID:reading-{booking['reading_id']}-practitioner@numer.life", body)
        self.assertIn(f"DTSTART:{slot.strftime('%Y%m%dT%H%M%SZ')}", body)
        self.assertIn("john@example.com", body)

    def test_hand_scheduled_readings_show_too_and_team_sees_them(self):
        _, lead = self.client_token()
        when = (dt.datetime.now(EDMONTON) + dt.timedelta(days=2)).replace(hour=14, minute=0)
        with numer.open_db() as connection:
            reading_id = connection.execute(
                "INSERT INTO readings (lead_id, practitioner_id, kind, mode, scheduled_at, duration_min, status,"
                " created_at) VALUES (?, ?, 'magialogia', 'phone', ?, 90, 'scheduled', ?)",
                (lead["id"], self.practitioner_id, when.strftime("%Y-%m-%dT%H:%M"), numer.iso(numer.now_utc()))).lastrowid
        frank = numer.create_user("frank@example.com", PASSWORD, "practitioner", "Frank")
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET team_of = ? WHERE id = ?", (self.practitioner_id, frank))
        self.login("frank@example.com")
        listing = self.client.get("/settings/appointments").get_data(as_text=True)
        self.assertIn("2:00 pm–3:30 pm", listing)
        self.assertNotIn("Availability &amp; booking settings", listing)     # settings are the practitioner's
        self.assertEqual(self.client.get(f"/settings/appointments/{reading_id}.ics").status_code, 200)

    def test_other_practitioners_cannot_see_them(self):
        self.enable()
        self.booked()
        numer.create_user("other@example.com", PASSWORD, "practitioner", "Other")
        self.login("other@example.com")
        reading_id = self.booking()["reading_id"]
        self.assertNotIn("Marco James Bonanni", self.client.get("/settings/appointments").get_data(as_text=True))
        self.assertEqual(self.client.get(f"/settings/appointments/{reading_id}").status_code, 404)
        self.assertEqual(self.client.get(f"/settings/appointments/{reading_id}.ics").status_code, 404)
