"""The practitioner's own calendar blocks booking times (read-only calendar link).

The practitioner pastes the private address of her calendar (Google Calendar: Settings > the
calendar > Integrate calendar > "Secret address in iCal format"; Apple iCloud: share the calendar
publicly and copy the link; Outlook: Publish calendar > ICS link). The `bookings` job reads it every
5 minutes; every event marked busy in the coming weeks then blocks those booking times.

- Only busy times are kept (start and end); event titles, places and guests are never stored.
- The link itself is a secret (anyone with it can read her calendar), so it's stored encrypted
  (secretbox) and only fetched from the hosts below (no arbitrary URLs from the server).
- Nothing is written to her calendar: she adds paid bookings with "Add to my calendar" on the
  Appointments page (or from the push notification).
- If the link stops working, the last busy times are kept and the Bookings and Appointments pages
  say so, so a broken link never opens up times she's busy.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from flask import flash, redirect, request, url_for

import secretbox
from app import app, db, iso, login_required, now_utc, open_db

ALLOWED_HOSTS = ("calendar.google.com", "icloud.com", "outlook.live.com", "outlook.office365.com",
                 "outlook.office.com")
MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 15
REFRESH_EVERY = dt.timedelta(minutes=4)
STALE_AFTER = dt.timedelta(hours=2)   # warn the practitioner when the last good read is older
LOOK_AHEAD_EXTRA_DAYS = 2


class FeedError(Exception):
    """The calendar link can't be used. The message is shown to the practitioner."""


def normalize(url: str) -> str:
    """Check a pasted calendar link; returns the https URL to fetch. Raises FeedError."""
    url = (url or "").strip()
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password or parts.port not in (None, 443):
        raise FeedError("Paste the full calendar link: it starts with https:// or webcal://.")
    if not any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_HOSTS):
        raise FeedError("Use the private link from Google Calendar, Apple iCloud or Outlook.")
    if len(url) > 1000:
        raise FeedError("That link is too long to be a calendar link.")
    return url


def _public_host(host: str) -> None:
    """Refuse hosts that resolve to private addresses (defence in depth on top of the allow-list)."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise FeedError("We couldn't reach your calendar. Check the link and try again.") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise FeedError("That calendar link isn't reachable from the internet.")


def fetch(url: str) -> bytes:
    url = normalize(url)
    _public_host(urllib.parse.urlsplit(url).hostname or "")
    request = urllib.request.Request(url, headers={"User-Agent": "numer.life calendar check", "Accept": "text/calendar"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 (host allow-listed)
            final = urllib.parse.urlsplit(response.geturl()).hostname or ""
            if not any(final == h or final.endswith("." + h) for h in ALLOWED_HOSTS):
                raise FeedError("Your calendar link redirected somewhere unexpected.")
            data = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404):
            raise FeedError("Your calendar provider refused the link. It may have been reset: copy it again.") from None
        raise FeedError(f"Your calendar provider answered with an error ({exc.code}). We'll keep trying.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise FeedError("We couldn't reach your calendar just now. We'll keep trying.") from None
    if len(data) > MAX_BYTES:
        raise FeedError("Your calendar is too large to read (over 5 MB).")
    return data


def busy_times(data: bytes, tz: ZoneInfo, start: dt.datetime, end: dt.datetime) -> list[tuple[dt.datetime, dt.datetime]]:
    """Busy intervals (UTC) between start and end. Free ("show as available") and cancelled events
    don't count. All-day events count as busy all day unless marked free."""
    import icalendar
    import recurring_ical_events
    try:
        calendar = icalendar.Calendar.from_ical(data)
        events = recurring_ical_events.of(calendar).between(start, end)
    except Exception as exc:  # noqa: BLE001  (any malformed feed)
        raise FeedError("That link didn't return a calendar. Copy the iCal (.ics) address.") from exc
    result = []
    for event in events:
        if str(event.get("TRANSP", "")).upper() == "TRANSPARENT" or str(event.get("STATUS", "")).upper() == "CANCELLED":
            continue
        try:
            begin, finish = event.start, event.end
        except Exception:  # noqa: BLE001
            continue
        begin, finish = _as_utc(begin, tz), _as_utc(finish, tz)
        if finish <= begin:
            continue
        result.append((begin, finish))
    return sorted(set(result))


def _as_utc(value, tz: ZoneInfo) -> dt.datetime:
    if isinstance(value, dt.datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=tz)   # floating time = her own time
    else:
        moment = dt.datetime.combine(value, dt.time(0), tz)             # all-day: her local midnight
    return moment.astimezone(dt.timezone.utc)


# --------------------------------------------------------------------------
# Stored feed and busy times
# --------------------------------------------------------------------------
def feed_for(user_id: int):
    return db().execute("SELECT * FROM busy_feeds WHERE user_id = ?", (user_id,)).fetchone()


def status(user_id: int) -> dict | None:
    """For the settings and appointments pages: is the link working?"""
    feed = feed_for(user_id)
    if feed is None:
        return None
    ok_at = dt.datetime.fromisoformat(feed["ok_at"]) if feed["ok_at"] else None
    stale = ok_at is None or now_utc() - ok_at > STALE_AFTER
    return {"ok_at": feed["ok_at"], "error": feed["error"], "stale": stale, "events": feed["events"],
            "fetched_at": feed["fetched_at"]}


def stored_busy(connection, user_id: int, start: dt.datetime, end: dt.datetime) -> list[tuple[dt.datetime, dt.datetime]]:
    rows = connection.execute("SELECT starts_at, ends_at FROM busy_times WHERE user_id = ? AND ends_at > ?"
                              " AND starts_at < ?", (user_id, start.isoformat(), end.isoformat())).fetchall()
    return [(dt.datetime.fromisoformat(r["starts_at"]), dt.datetime.fromisoformat(r["ends_at"])) for r in rows]


def _window(user) -> tuple[ZoneInfo, dt.datetime, dt.datetime]:
    import bookings
    s = bookings.settings(user)
    start = now_utc() - dt.timedelta(days=1)
    return bookings.zone(s), start, now_utc() + dt.timedelta(days=s["window_days"] + LOOK_AHEAD_EXTRA_DAYS)


def _store(user_id: int, intervals, error: str | None) -> None:
    now = iso(now_utc())
    with open_db() as connection:
        if error is None:
            connection.execute("DELETE FROM busy_times WHERE user_id = ?", (user_id,))
            connection.executemany("INSERT INTO busy_times (user_id, starts_at, ends_at) VALUES (?, ?, ?)",
                                   [(user_id, b.isoformat(), e.isoformat()) for b, e in intervals])
            connection.execute("UPDATE busy_feeds SET fetched_at = ?, ok_at = ?, error = NULL, events = ?"
                               " WHERE user_id = ?", (now, now, len(intervals), user_id))
        else:  # keep the last busy times: a broken link must never open up times she's busy
            connection.execute("UPDATE busy_feeds SET fetched_at = ?, error = ? WHERE user_id = ?",
                               (now, error[:300], user_id))


def connect(user, url: str) -> int:
    """Check a new link now (fetch + read) and save it. Returns how many busy times it found.
    Raises FeedError, in which case nothing is saved."""
    url = normalize(url)
    tz, start, end = _window(user)
    intervals = busy_times(fetch(url), tz, start, end)   # network: no database lock held
    with open_db() as connection:
        connection.execute("DELETE FROM busy_feeds WHERE user_id = ?", (user["id"],))
        connection.execute("INSERT INTO busy_feeds (user_id, url, added_at) VALUES (?, ?, ?)",
                           (user["id"], secretbox.encrypt(url), iso(now_utc())))
    _store(user["id"], intervals, None)
    return len(intervals)


def disconnect(user_id: int) -> None:
    with open_db() as connection:
        connection.execute("DELETE FROM busy_times WHERE user_id = ?", (user_id,))
        connection.execute("DELETE FROM busy_feeds WHERE user_id = ?", (user_id,))


def refresh(user_id: int) -> bool:
    feed = feed_for(user_id)
    user = db().execute("SELECT * FROM users WHERE id = ? AND is_active = 1", (user_id,)).fetchone()
    if feed is None or user is None:
        return False
    try:
        url = secretbox.decrypt(feed["url"])
    except Exception:  # noqa: BLE001
        app.logger.error("Could not decrypt the calendar link of user %s", user_id)
        _store(user_id, [], "We couldn't read your saved calendar link. Please paste it again.")
        return False
    tz, start, end = _window(user)
    try:
        intervals = busy_times(fetch(url), tz, start, end)
    except FeedError as exc:
        app.logger.warning("Calendar link of user %s failed: %s", user_id, exc)
        _store(user_id, [], str(exc))
        return False
    _store(user_id, intervals, None)
    return True


def refresh_due() -> dict:
    """Job: re-read every calendar link not read in the last few minutes."""
    cutoff = iso(now_utc() - REFRESH_EVERY)
    ids = [r[0] for r in db().execute("SELECT user_id FROM busy_feeds WHERE fetched_at IS NULL OR fetched_at < ?",
                                      (cutoff,))]
    result = {"feeds": 0, "feed_errors": 0}
    for user_id in ids:
        result["feeds"] += 1
        if not refresh(user_id):
            result["feed_errors"] += 1
    return result


app.jinja_env.globals["calendar_feed_status"] = status



@app.post("/settings/bookings/calendar")
@login_required
def calendar_link_save():
    import bookings
    user = bookings._owner()
    back = url_for("booking_settings_page") + "#calendar-link"
    if request.form.get("action") == "disconnect":
        disconnect(user["id"])
        flash("Calendar link removed. Only your hours and scheduled readings block times now.", "info")
        return redirect(back)
    try:
        found = connect(user, request.form.get("url", ""))
    except FeedError as exc:
        flash(str(exc), "error")
        return redirect(back)
    flash(f"Calendar connected: {found} busy time{'' if found == 1 else 's'} in the coming weeks are now blocked.",
          "info")
    return redirect(back)
