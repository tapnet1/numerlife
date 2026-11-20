"""Clients refer friends: a share panel on the client page with their own link.

- Each client gets a short code; their link is their practitioner's link with ?r=<code>
  (numer.life/liliana?r=abc123), so the friend's report goes to the same practitioner.
- Opening the link remembers the code in the visitor's session; the report they start is marked
  referred_by that client. A friend "joined" once they unlock their report.
- The client sees the first names of friends who joined; the practitioner sees who referred whom
  on the lead page.
- Reward: each friend who requests their report (gives their email to get it) adds one free month
  to the referrer's calendar access, up to MAX_REWARD_MONTHS. Not counted: the referrer's own email,
  an email that already asked for a report before, or the same friend twice. If the referrer
  hasn't started a calendar yet, the months wait and are added when they do.
"""
from __future__ import annotations

import datetime as dt
import re
import secrets

from flask import request, session

from app import BASE_URL, app, db, iso, now_utc, open_db

MAX_REWARD_MONTHS = 12

CODE_RE = re.compile(r"^[A-Za-z0-9_-]{6,16}$")

T = {
    "en": {"title": "Share numer.life with a friend",
           "desc": "Send a friend their free numerology report. When they get theirs, you'll see them here.",
           "share": "📤 Share…", "copy": "Copy message", "email": "Email",
           "message": "I got my free numerology report on numer.life with {who} and loved it. Get yours here:",
           "joined": "Friends who got their report: {names}", "none": "No friends yet. Your link is ready to share.",
           "reward": "🎁 Each friend who requests their free report adds a free month to your calendar.",
           "earned": "You've earned {n} free month(s) of your calendar so far."},
    "es": {"title": "Comparte numer.life con un amigo",
           "desc": "Envía a un amigo su informe de numerología gratis. Cuando reciba el suyo, lo verás aquí.",
           "share": "📤 Compartir…", "copy": "Copiar mensaje", "email": "Correo",
           "message": "Recibí mi informe de numerología gratis en numer.life con {who} y me encantó. Pide el tuyo aquí:",
           "joined": "Amigos que recibieron su informe: {names}", "none": "Aún no hay amigos. Tu enlace está listo para compartir.",
           "reward": "🎁 Cada amigo que pida su informe gratis te da un mes gratis más de tu calendario.",
           "earned": "Hasta ahora has ganado {n} mes(es) gratis de tu calendario."},
}


def labels(lang: str) -> dict:
    return T.get(lang) or T["en"]


def code_for(lead_id: int) -> str:
    row = db().execute("SELECT ref_code FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if row and row["ref_code"]:
        return row["ref_code"]
    code = secrets.token_urlsafe(6)
    with open_db() as connection:
        connection.execute("UPDATE leads SET ref_code = ? WHERE id = ? AND ref_code IS NULL", (code, lead_id))
        return connection.execute("SELECT ref_code FROM leads WHERE id = ?", (lead_id,)).fetchone()["ref_code"]


def share_url(lead) -> str:
    code = code_for(lead["id"])
    slug = None
    if lead["practitioner_id"]:
        row = db().execute("SELECT slug FROM users WHERE id = ? AND is_active = 1 AND team_of IS NULL",
                           (lead["practitioner_id"],)).fetchone()
        slug = row["slug"] if row and row["slug"] else None
    return f"{BASE_URL}/{slug}?r={code}" if slug else f"{BASE_URL}/?r={code}"


def friends(lead_id: int) -> list:
    return db().execute("SELECT id, full_name, status FROM leads WHERE referred_by = ? AND status = 'captured'"
                        " ORDER BY id", (lead_id,)).fetchall()


def referrer(lead):
    if not lead["referred_by"]:
        return None
    return db().execute("SELECT id, full_name FROM leads WHERE id = ?", (lead["referred_by"],)).fetchone()


def first_names(rows) -> str:
    return ", ".join((r["full_name"].split() or ["?"])[0] for r in rows)


app.jinja_env.globals.update(referral_labels=labels, referral_url=share_url, referral_friends=friends,
                             referral_referrer=referrer, referral_first_names=first_names)


@app.before_request
def _remember_client_ref():
    code = request.args.get("r")
    if code and request.method == "GET" and request.endpoint in ("home", "practitioner_link") and CODE_RE.match(code):
        row = db().execute("SELECT id FROM leads WHERE ref_code = ?", (code,)).fetchone()
        if row is not None:
            session["client_ref"] = row["id"]


def attach(connection, new_lead_id: int) -> None:
    """Called when a visitor starts a report: credit the client whose link brought them."""
    referrer_id = session.get("client_ref")
    if referrer_id and referrer_id != new_lead_id:
        connection.execute("UPDATE leads SET referred_by = ? WHERE id = ? AND referred_by IS NULL",
                           (referrer_id, new_lead_id))


# --------------------------------------------------------------------------
# Reward: a free calendar month per friend who requests their report
# --------------------------------------------------------------------------
def _add_month(day: dt.date) -> dt.date:
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    last = [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28, 31, 30, 31, 30,
            31, 31, 30, 31, 30, 31][month - 1]
    return dt.date(year, month, min(day.day, last))


def reward(friend_lead_id: int) -> bool:
    """Called when a visitor unlocks their report. Returns True if a month was earned."""
    friend = db().execute("SELECT * FROM leads WHERE id = ?", (friend_lead_id,)).fetchone()
    if friend is None or not friend["referred_by"] or friend["status"] != "captured":
        return False
    referrer = db().execute("SELECT * FROM leads WHERE id = ?", (friend["referred_by"],)).fetchone()
    email = (friend["email"] or "").strip().lower()
    if referrer is None or not email or email == (referrer["email"] or "").strip().lower():
        return False
    if db().execute("SELECT 1 FROM leads WHERE lower(email) = ? AND id < ? AND status = 'captured'",
                    (email, friend_lead_id)).fetchone():
        return False  # this person had already asked for a report
    with open_db() as connection:
        earned = connection.execute("SELECT COALESCE(SUM(months), 0) FROM referral_rewards WHERE referrer_lead_id = ?",
                                    (referrer["id"],)).fetchone()[0]
        if earned >= MAX_REWARD_MONTHS:
            return False
        if connection.execute("INSERT OR IGNORE INTO referral_rewards (referrer_lead_id, friend_lead_id, months,"
                              " created_at) VALUES (?, ?, 1, ?)",
                              (referrer["id"], friend_lead_id, iso(now_utc()))).rowcount != 1:
            return False
    apply_pending(referrer["id"])
    return True


def apply_pending(lead_id: int) -> int:
    """Add earned months to the client's calendar access (once each). Returns months added."""
    import calendar_access
    cal = db().execute("SELECT * FROM calendars WHERE lead_id = ?", (lead_id,)).fetchone()
    if cal is None:
        return 0
    added = 0
    with open_db() as connection:
        rows = connection.execute("SELECT * FROM referral_rewards WHERE referrer_lead_id = ? AND applied_at IS NULL",
                                  (lead_id,)).fetchall()
        if not rows:
            return 0
        cal = connection.execute("SELECT * FROM calendars WHERE id = ?", (cal["id"],)).fetchone()
        until = max(calendar_access.access_until(cal), now_utc().date())
        for row in rows:
            for _ in range(row["months"]):
                until = _add_month(until)
                added += 1
            connection.execute("UPDATE referral_rewards SET applied_at = ? WHERE id = ?", (iso(now_utc()), row["id"]))
        connection.execute("UPDATE calendars SET access_until = ? WHERE id = ?", (until.isoformat(), cal["id"]))
    return added


def months_earned(lead_id: int) -> int:
    return db().execute("SELECT COALESCE(SUM(months), 0) FROM referral_rewards WHERE referrer_lead_id = ?",
                        (lead_id,)).fetchone()[0]


app.jinja_env.globals["referral_months_earned"] = months_earned
