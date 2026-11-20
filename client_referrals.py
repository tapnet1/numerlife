"""Clients refer friends: a share panel on the client page with their own link.

- Each client gets a short code; their link is their practitioner's link with ?r=<code>
  (numer.life/liliana?r=abc123), so the friend's report goes to the same practitioner.
- Opening the link remembers the code in the visitor's session; the report they start is marked
  referred_by that client. A friend "joined" once they unlock their report.
- The client sees the first names of friends who joined; the practitioner sees who referred whom
  on the lead page.
"""
from __future__ import annotations

import re
import secrets

from flask import request, session

from app import BASE_URL, app, db, open_db

CODE_RE = re.compile(r"^[A-Za-z0-9_-]{6,16}$")

T = {
    "en": {"title": "Share numer.life with a friend",
           "desc": "Send a friend their free numerology report. When they get theirs, you'll see them here.",
           "share": "📤 Share…", "copy": "Copy message", "email": "Email",
           "message": "I got my free numerology report on numer.life with {who} and loved it. Get yours here:",
           "joined": "Friends who got their report: {names}", "none": "No friends yet. Your link is ready to share."},
    "es": {"title": "Comparte numer.life con un amigo",
           "desc": "Envía a un amigo su informe de numerología gratis. Cuando reciba el suyo, lo verás aquí.",
           "share": "📤 Compartir…", "copy": "Copiar mensaje", "email": "Correo",
           "message": "Recibí mi informe de numerología gratis en numer.life con {who} y me encantó. Pide el tuyo aquí:",
           "joined": "Amigos que recibieron su informe: {names}", "none": "Aún no hay amigos. Tu enlace está listo para compartir."},
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
