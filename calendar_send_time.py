"""Daily calendar email: sent at the time each client actually reads it.

- Every daily calendar email carries a tracking pixel (same as the follow-up series). Only the
  first open of each email counts, and an open within 2 minutes of sending is ignored (mail
  apps that preload images on delivery, such as Apple Mail Privacy Protection).
- Once a week, from the client's last 21 daily emails with at least MIN_OPENS real opens, we
  take the hour they most often open it (ties: the earliest) and send one hour before it, so it
  is waiting at the top of their inbox. Clamped to 5 am - 9 pm (Edmonton time, like the rest of
  the calendar). Until then: 7 am.
- Admin > Email shows open rates before and after each client's time was adjusted.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter
from zoneinfo import ZoneInfo

import calendar_engine as engine
from app import app, db, iso, now_utc

DEFAULT_HOUR = 7
EARLIEST_HOUR = 5
LATEST_HOUR = 21
MIN_OPENS = 5
LOOKBACK_EMAILS = 21
PREFETCH_SECONDS = 120
RECHECK_DAYS = 7


def _utc(value: str) -> dt.datetime:
    moment = dt.datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)


def first_opens(connection, lead_id: int, limit: int = LOOKBACK_EMAILS) -> list[tuple[dt.datetime, dt.datetime | None]]:
    """[(sent_at, first real open or None)] for the client's latest daily calendar emails."""
    rows = connection.execute(
        "SELECT o.sent_at, (SELECT MIN(e.created_at) FROM email_events e WHERE e.track = o.track"
        "   AND e.event IN ('open', 'click') AND julianday(e.created_at) > julianday(o.sent_at) + ?) AS opened"
        " FROM email_outbox o WHERE o.kind = 'calendar_daily' AND o.lead_id = ? AND o.sent_at IS NOT NULL"
        " AND o.track IS NOT NULL ORDER BY o.sent_at DESC LIMIT ?",
        (PREFETCH_SECONDS / 86400, lead_id, limit)).fetchall()
    return [(_utc(r["sent_at"]), _utc(r["opened"]) if r["opened"] else None) for r in rows]


def learned_hour(connection, lead_id: int) -> int | None:
    zone = ZoneInfo(engine.DEFAULT_TZ)
    hours = [opened.astimezone(zone).hour for _, opened in first_opens(connection, lead_id) if opened]
    if len(hours) < MIN_OPENS:
        return None
    counts = Counter(hours)
    best = max(counts.values())
    usual = min(h for h, n in counts.items() if n == best)
    return max(EARLIEST_HOUR, min(LATEST_HOUR, usual - 1))


def send_hour(cal) -> int:
    """This calendar's daily send hour (local time)."""
    hour = cal["send_hour"] if "send_hour" in cal.keys() else None
    return hour if hour is not None else DEFAULT_HOUR


def refresh() -> int:
    """Relearn the send hour of daily calendars not checked for a week. Returns how many changed.
    Runs before the hourly send, in its own short transactions."""
    from app import open_db
    cutoff = iso(now_utc() - dt.timedelta(days=RECHECK_DAYS))
    rows = db().execute("SELECT id, lead_id, send_hour FROM calendars WHERE email_freq = 'daily' AND active = 1"
                        " AND (send_hour_checked_at IS NULL OR send_hour_checked_at < ?)", (cutoff,)).fetchall()
    changed = 0
    for cal in rows:
        with open_db() as connection:
            learned = learned_hour(connection, cal["lead_id"])
            if learned is not None and learned != cal["send_hour"]:
                connection.execute("UPDATE calendars SET send_hour = ?, send_hour_set_at = COALESCE(send_hour_set_at, ?),"
                                   " send_hour_checked_at = ? WHERE id = ?",
                                   (learned, iso(now_utc()), iso(now_utc()), cal["id"]))
                changed += 1
            else:
                connection.execute("UPDATE calendars SET send_hour_checked_at = ? WHERE id = ?",
                                   (iso(now_utc()), cal["id"]))
    return changed


def _rate(rows) -> tuple[int, int]:
    sent = len(rows)
    return sent, sum(1 for _, opened in rows if opened)


def report() -> dict:
    """Open rates of daily calendar emails: last 30 days, and before/after each adjusted send time."""
    connection = db()
    since = iso(now_utc() - dt.timedelta(days=30))
    total = connection.execute(
        "SELECT COUNT(*) AS sent, SUM(EXISTS (SELECT 1 FROM email_events e WHERE e.track = o.track"
        "  AND e.event IN ('open', 'click') AND julianday(e.created_at) > julianday(o.sent_at) + ?)) AS opened"
        " FROM email_outbox o WHERE o.kind = 'calendar_daily' AND o.sent_at >= ? AND o.track IS NOT NULL",
        (PREFETCH_SECONDS / 86400, since)).fetchone()
    before = [0, 0]
    after = [0, 0]
    clients = []
    for cal in connection.execute("SELECT calendars.*, leads.full_name FROM calendars JOIN leads"
                                  " ON leads.id = calendars.lead_id WHERE send_hour_set_at IS NOT NULL"):
        set_at = _utc(cal["send_hour_set_at"])
        rows = first_opens(connection, cal["lead_id"], limit=200)
        b = [r for r in rows if r[0] < set_at]
        a = [r for r in rows if r[0] >= set_at]
        for target, part in ((before, b), (after, a)):
            s, o = _rate(part)
            target[0] += s
            target[1] += o
        clients.append({"name": cal["full_name"], "hour": cal["send_hour"], "before": _rate(b), "after": _rate(a)})
    pct = lambda s, o: round(100 * o / s) if s else None  # noqa: E731
    return {"sent": total["sent"] or 0, "opened": total["opened"] or 0,
            "rate": pct(total["sent"] or 0, total["opened"] or 0),
            "before": {"sent": before[0], "rate": pct(*before)}, "after": {"sent": after[0], "rate": pct(*after)},
            "clients": clients, "min_opens": MIN_OPENS}


def label(cal) -> str:
    hour = cal["send_hour"] if "send_hour" in cal.keys() and cal["send_hour"] is not None else DEFAULT_HOUR
    learned = "send_hour" in cal.keys() and cal["send_hour"] is not None
    text = dt.time(hour).strftime("%-I %p").lower()
    return f"{text} (learned from when they open it)" if learned else f"{text} (default until we learn their time)"


app.jinja_env.globals.update(daily_open_report=report, daily_send_label=label)
