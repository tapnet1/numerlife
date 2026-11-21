"""Data retention: personal data is not kept longer than it is used (Marco, 2026-11-21).

Run daily by the `purge-data` job:
- A client (unlocked report) with no activity for a year is deleted with everything about them:
  calendars, journal notes and replies, readings, paid readings, subscriptions, client page,
  referral rewards and their emails. Activity is anything the client or their practitioner did:
  report unlocked, client opened one of their pages or emails, wrote a note, a reply, a reading,
  an order, a practitioner edit. Clients with paid calendar time left, or an open PayPal
  subscription, are never deleted.
- A started report (name and birth date given, report never unlocked) with no activity for
  30 days is deleted the same way.
- Database backups (.bak) older than 90 days are deleted; the 5 newest are always kept.
Money records stay: the ledger and monthly invoices hold amounts and references, not client details.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from app import app, iso, now_utc

CLIENT_DAYS = 365
STARTED_DAYS = 30
BACKUP_DAYS = 90
KEEP_BACKUPS = 5


def touch(connection, lead_id: int) -> None:
    """Record that the client opened one of their pages (at most once a day)."""
    today = now_utc().date().isoformat()
    connection.execute("UPDATE leads SET client_seen_at = ? WHERE id = ?"
                       " AND (client_seen_at IS NULL OR client_seen_at < ?)", (iso(now_utc()), lead_id, today))
    connection.commit()


# A lead is kept if any of these happened on or after :cutoff, or it has paid calendar time left (:today).
_RECENT = """
    julianday(leads.created_at) >= julianday(:cutoff)
    OR julianday(COALESCE(leads.captured_at, leads.created_at)) >= julianday(:cutoff)
    OR julianday(COALESCE(leads.updated_at, leads.created_at)) >= julianday(:cutoff)
    OR julianday(COALESCE(leads.viewed_at, leads.created_at)) >= julianday(:cutoff)
    OR julianday(COALESCE(leads.client_at, leads.created_at)) >= julianday(:cutoff)
    OR julianday(COALESCE(leads.client_seen_at, leads.created_at)) >= julianday(:cutoff)
    OR EXISTS (SELECT 1 FROM feedback_entries f WHERE f.lead_id = leads.id
               AND julianday(f.created_at) >= julianday(:cutoff))
    OR EXISTS (SELECT 1 FROM journal_replies r WHERE r.lead_id = leads.id
               AND julianday(r.created_at) >= julianday(:cutoff))
    OR EXISTS (SELECT 1 FROM orders o WHERE o.lead_id = leads.id
               AND julianday(COALESCE(o.updated_at, o.created_at)) >= julianday(:cutoff))
    OR EXISTS (SELECT 1 FROM readings g WHERE g.lead_id = leads.id
               AND (julianday(COALESCE(g.updated_at, g.created_at)) >= julianday(:cutoff)
                    OR julianday(g.scheduled_at) >= julianday(:cutoff)))
    OR EXISTS (SELECT 1 FROM calendars c WHERE c.lead_id = leads.id
               AND (c.access_until >= :today OR julianday(c.updated_at) >= julianday(:cutoff)))
    OR EXISTS (SELECT 1 FROM calendar_subscriptions s WHERE s.lead_id = leads.id
               AND (s.status IN ('APPROVAL_PENDING', 'APPROVED', 'ACTIVE', 'SUSPENDED')
                    OR julianday(s.updated_at) >= julianday(:cutoff)))
    OR EXISTS (SELECT 1 FROM email_outbox m JOIN email_events e ON e.track = m.track
               WHERE m.lead_id = leads.id AND m.track IS NOT NULL AND m.track != ''
               AND julianday(e.created_at) >= julianday(:cutoff))
    OR EXISTS (SELECT 1 FROM referral_rewards w WHERE w.referrer_lead_id = leads.id
               AND julianday(w.created_at) >= julianday(:cutoff))
"""


def expired_leads(connection, now: dt.datetime | None = None) -> list[int]:
    now = now or now_utc()
    today = now.date().isoformat()
    found = []
    for status, days in (("captured", CLIENT_DAYS), ("started", STARTED_DAYS)):
        cutoff = iso(now - dt.timedelta(days=days))
        rows = connection.execute(f"SELECT id FROM leads WHERE status = :status AND NOT ({_RECENT})",
                                  {"status": status, "cutoff": cutoff, "today": today}).fetchall()
        found += [row[0] for row in rows]
    return found


def delete_lead(connection, lead_id: int) -> None:
    """Delete one lead and every row about them (inside the caller's transaction)."""
    one = (lead_id,)
    cal_ids = "SELECT id FROM calendars WHERE lead_id = ?"
    tracks = ("SELECT track FROM email_outbox WHERE lead_id = ? AND track IS NOT NULL AND track != ''")
    connection.execute(f"DELETE FROM email_events WHERE track IN ({tracks})", one)
    connection.execute("DELETE FROM email_outbox WHERE lead_id = ?", one)
    connection.execute("DELETE FROM journal_replies WHERE lead_id = ?"
                       " OR entry_id IN (SELECT id FROM feedback_entries WHERE lead_id = ?)", (lead_id, lead_id))
    connection.execute("DELETE FROM feedback_entries WHERE lead_id = ?", one)
    connection.execute("DELETE FROM feedback_journals WHERE lead_id = ?", one)
    connection.execute("DELETE FROM calendar_subscriptions WHERE lead_id = ?"
                       f" OR calendar_id IN ({cal_ids})", (lead_id, lead_id))
    connection.execute(f"DELETE FROM calendar_months WHERE calendar_id IN ({cal_ids})", one)
    connection.execute(f"DELETE FROM calendar_notes WHERE calendar_id IN ({cal_ids})", one)
    connection.execute("DELETE FROM calendars WHERE lead_id = ?", one)
    connection.execute("DELETE FROM readings WHERE lead_id = ?", one)
    connection.execute("DELETE FROM orders WHERE lead_id = ?", one)
    connection.execute("DELETE FROM client_pages WHERE lead_id = ?", one)
    connection.execute("DELETE FROM referral_rewards WHERE referrer_lead_id = ? OR friend_lead_id = ?",
                       (lead_id, lead_id))
    connection.execute("UPDATE leads SET referred_by = NULL WHERE referred_by = ?", one)
    connection.execute("DELETE FROM leads WHERE id = ?", one)


def purge_leads(connection, now: dt.datetime | None = None) -> int:
    ids = expired_leads(connection, now)
    for lead_id in ids:
        delete_lead(connection, lead_id)
    return len(ids)


def purge_backups(db_path: str, now: dt.datetime | None = None) -> int:
    """Delete <db>.*.bak files older than BACKUP_DAYS, always keeping the KEEP_BACKUPS newest."""
    live = Path(db_path)
    if not live.parent.is_dir():
        return 0
    backups = sorted((p for p in live.parent.iterdir()
                      if p.is_file() and p.name.startswith(live.name + ".") and p.name.endswith(".bak")),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    limit = ((now or now_utc()) - dt.timedelta(days=BACKUP_DAYS)).timestamp()
    removed = 0
    for path in backups[KEEP_BACKUPS:]:
        if path.stat().st_mtime < limit:
            path.unlink()
            removed += 1
    return removed


def run() -> dict:
    from app import DB, initialize, open_db

    initialize()
    with open_db() as connection:
        leads = purge_leads(connection)
    return {"leads_deleted": leads, "backups_deleted": purge_backups(DB)}


@app.before_request
def _note_client_visit():
    """Client pages (/r/, /c/, /me/, /p/, /f/ + token) count as the client's activity."""
    from flask import request

    if request.method != "GET" or not request.view_args or "token" not in request.view_args:
        return None
    path = request.path
    table = {"/r/": "leads", "/c/": "calendars", "/me/": "client_pages", "/p/": "orders",
             "/f/": "feedback_journals"}
    for prefix, name in table.items():
        if path.startswith(prefix):
            from app import db

            connection = db()
            column = "id" if name == "leads" else "lead_id"
            row = connection.execute(f"SELECT {column} FROM {name} WHERE token = ?",
                                     (request.view_args["token"],)).fetchone()
            if row is not None:
                touch(connection, row[0])
            break
    return None
