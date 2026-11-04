"""Client feedback journal: how the client feels in the days after a reading.

After a full written reading is delivered, or a reading (session) is completed, the client gets a
private link (/f/<token>). There they can write, whenever they like, how they feel: the day after,
a few days later, a week on. Nothing is fixed; days 1, 2, 3, 5 and 7 are only suggested. Each entry
records the day it describes, so the practitioner sees "Day 3 after your reading".

- One journal (one link) per client; entries can be about any of their delivered/completed sessions.
- The practitioner and her team see entries on the lead page and get a push + email alert.
- No client account: the private link is the sign-in, as for the paid reading and the calendar.
  The practitioner can replace the link if it was shared by mistake.
"""
from __future__ import annotations

import datetime as dt
import secrets
from zoneinfo import ZoneInfo

from flask import abort, flash, redirect, render_template, request, url_for

import calendar_engine as engine
from app import BASE_URL, app, db, iso, login_required, now_utc, open_db

MAX_BODY = 4000
SUBMIT_LIMIT_PER_HOUR = 30
SUGGESTED_DAYS = (1, 2, 3, 5, 7)

T = {
    "en": {
        "title": "How are you feeling, {first_name}?",
        "intro": "Write whenever you feel like it: the day after your reading, a few days later, or a week on. "
                 "There's no schedule; many people find days 1, 2, 3, 5 and 7 a good rhythm. Only {who} and their team see what you write.",
        "session": "Which session is this about?",
        "day": "Which day are you writing about?",
        "day_hint": "Usually today. You can also write about an earlier day.",
        "feeling": "Overall, how do you feel?",
        "feelings": {1: "Very low", 2: "Low", 3: "Okay", 4: "Good", 5: "Very good"},
        "body": "What have you noticed?",
        "body_hint": "Feelings, thoughts, dreams, changes, anything that came up.",
        "save": "Send to {who}",
        "saved": "Thank you. {who} will see it.",
        "error_empty": "Please write a few words or choose how you feel.",
        "error_day": "Please choose a day between your session and today.",
        "error_limit": "Too many entries from your connection. Please try again in an hour.",
        "history": "What you've shared",
        "same_day": "Same day", "day_after": "Day {n} after", "day_before": "Before the session",
        "full_reading": "Full written reading",
        "kinds": {"numerology": "Numerology reading", "magialogia": "Magialogía Numérica reading", "other": "Session"},
        "your_numerologist": "your numerologist",
        "invite_subject": "How are you feeling after your reading, {first_name}?",
        "invite_lead": "Hi {first_name}, {who} would love to hear how you feel in the days after your reading.",
        "invite_body": "Write whenever you like: tomorrow, in a few days, or a week from now. Your private page keeps everything together, so you can come back as often as you want.",
        "invite_button": "Share how I feel",
        "invite_private": "This link is private to you. Please don't forward it.",
        "ready_feedback": "In the days after you read it, tell {who} how you feel: {url}",
        "card_title": "How are you feeling?",
        "card_body": "In the days after your reading, tell {who} how you feel, whenever you like.",
        "card_button": "Share how I feel",
    },
    "es": {
        "title": "¿Cómo te sientes, {first_name}?",
        "intro": "Escribe cuando quieras: el día después de tu lectura, unos días más tarde o una semana después. "
                 "No hay horario; a muchas personas les ayudan los días 1, 2, 3, 5 y 7. Solo {who} y su equipo ven lo que escribes.",
        "session": "¿Sobre qué sesión escribes?",
        "day": "¿Sobre qué día escribes?",
        "day_hint": "Normalmente hoy. También puedes escribir sobre un día anterior.",
        "feeling": "En general, ¿cómo te sientes?",
        "feelings": {1: "Muy mal", 2: "Mal", 3: "Regular", 4: "Bien", 5: "Muy bien"},
        "body": "¿Qué has notado?",
        "body_hint": "Emociones, pensamientos, sueños, cambios, lo que haya surgido.",
        "save": "Enviar a {who}",
        "saved": "Gracias. {who} lo verá.",
        "error_empty": "Escribe unas palabras o elige cómo te sientes.",
        "error_day": "Elige un día entre tu sesión y hoy.",
        "error_limit": "Demasiados envíos desde tu conexión. Inténtalo de nuevo en una hora.",
        "history": "Lo que has compartido",
        "same_day": "El mismo día", "day_after": "Día {n} después", "day_before": "Antes de la sesión",
        "full_reading": "Lectura completa por escrito",
        "kinds": {"numerology": "Lectura de numerología", "magialogia": "Lectura de Magialogía Numérica", "other": "Sesión"},
        "your_numerologist": "tu numeróloga",
        "invite_subject": "¿Cómo te sientes después de tu lectura, {first_name}?",
        "invite_lead": "Hola {first_name}, a {who} le encantaría saber cómo te sientes en los días después de tu lectura.",
        "invite_body": "Escribe cuando quieras: mañana, en unos días o dentro de una semana. Tu página privada lo guarda todo junto, así que puedes volver cuantas veces quieras.",
        "invite_button": "Contar cómo me siento",
        "invite_private": "Este enlace es solo para ti. Por favor, no lo reenvíes.",
        "ready_feedback": "En los días después de leerla, cuéntale a {who} cómo te sientes: {url}",
        "card_title": "¿Cómo te sientes?",
        "card_body": "En los días después de tu lectura, cuéntale a {who} cómo te sientes, cuando quieras.",
        "card_button": "Contar cómo me siento",
    },
}
STAFF_FEELINGS = T["en"]["feelings"]


def _lang(lead) -> str:
    import orders
    return orders._lead_lang(lead)


def _who(practitioner, lang: str) -> str:
    return practitioner["display_name"] if practitioner is not None and practitioner["display_name"] \
        else T[lang]["your_numerologist"]


def _first_name(lead) -> str:
    return (lead["full_name"].split() or ["there"])[0]


def _local_date(utc_iso: str) -> str:
    moment = dt.datetime.fromisoformat(utc_iso)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(ZoneInfo(engine.DEFAULT_TZ)).date().isoformat()


# --------------------------------------------------------------------------
# Journals, sessions, entries
# --------------------------------------------------------------------------
def journal_for_lead(lead_id: int):
    return db().execute("SELECT * FROM feedback_journals WHERE lead_id = ?", (lead_id,)).fetchone()


def ensure_journal(connection, lead_id: int):
    """The client's journal, created on first use (one per client)."""
    connection.execute("INSERT OR IGNORE INTO feedback_journals (lead_id, token, created_at) VALUES (?, ?, ?)",
                       (lead_id, secrets.token_urlsafe(24), iso(now_utc())))
    return connection.execute("SELECT * FROM feedback_journals WHERE lead_id = ?", (lead_id,)).fetchone()


def journal_url(journal) -> str:
    return f"{BASE_URL}/f/{journal['token']}"


def sessions_for_lead(lead_id: int, lang: str = "en") -> list[dict]:
    """Delivered full readings and completed readings, newest first: what feedback can be about."""
    t = T[lang]
    sessions = []
    for order in db().execute("SELECT id, delivered_at FROM orders WHERE lead_id = ? AND status = 'delivered'"
                              " AND delivered_at IS NOT NULL", (lead_id,)):
        sessions.append({"key": f"order-{order['id']}", "kind": "order", "id": order["id"],
                         "date": _local_date(order["delivered_at"]), "label": t["full_reading"]})
    for reading in db().execute("SELECT id, kind, scheduled_at FROM readings WHERE lead_id = ? AND status = 'completed'",
                                (lead_id,)):
        sessions.append({"key": f"reading-{reading['id']}", "kind": "reading", "id": reading["id"],
                         "date": reading["scheduled_at"][:10], "label": t["kinds"].get(reading["kind"], t["kinds"]["other"])})
    sessions.sort(key=lambda s: (s["date"], s["key"]), reverse=True)
    return sessions


def can_invite(lead) -> bool:
    return lead["status"] == "captured" and bool(lead["email"]) and bool(sessions_for_lead(lead["id"]))


def entries_for_lead(lead_id: int) -> list:
    return db().execute("SELECT * FROM feedback_entries WHERE lead_id = ? ORDER BY entry_date DESC, id DESC",
                        (lead_id,)).fetchall()


def day_after(entry) -> int | None:
    if not entry["session_date"]:
        return None
    return (dt.date.fromisoformat(entry["entry_date"]) - dt.date.fromisoformat(entry["session_date"])).days


def day_label(entry, lang: str = "en") -> str:
    days = day_after(entry)
    if days is None:
        return ""
    t = T[lang]
    if days < 0:
        return t["day_before"]
    return t["same_day"] if days == 0 else t["day_after"].format(n=days)


def session_label(entry, lang: str = "en") -> str:
    t = T[lang]
    if entry["session_kind"] == "order":
        return t["full_reading"]
    if entry["session_kind"] == "reading":
        row = db().execute("SELECT kind FROM readings WHERE id = ?", (entry["session_id"],)).fetchone()
        return t["kinds"].get(row["kind"] if row else "other", t["kinds"]["other"])
    return ""


def unseen_feedback(user, limit: int = 8) -> list:
    """Clients with feedback the practitioner hasn't opened yet (for the dashboard)."""
    import workspace
    condition, params = workspace.lead_scope(user)
    return db().execute(
        "SELECT leads.id, leads.full_name, COUNT(*) AS n, MAX(feedback_entries.created_at) AS latest"
        " FROM feedback_entries JOIN leads ON leads.id = feedback_entries.lead_id"
        f" WHERE feedback_entries.seen_at IS NULL AND {condition}"
        " GROUP BY leads.id ORDER BY latest DESC LIMIT ?", [*params, limit]).fetchall()


def mark_seen(lead_id: int) -> None:
    connection = db()
    connection.execute("UPDATE feedback_entries SET seen_at = ? WHERE lead_id = ? AND seen_at IS NULL",
                       (iso(now_utc()), lead_id))
    connection.commit()


def feedback_url_for_lead(lead_id: int) -> str | None:
    journal = journal_for_lead(lead_id)
    return journal_url(journal) if journal is not None else None


app.jinja_env.globals.update(
    feedback_journal=journal_for_lead, feedback_url=journal_url, feedback_entries=entries_for_lead,
    feedback_day_label=day_label, feedback_session_label=session_label, feedback_can_invite=can_invite,
    feedback_feelings=STAFF_FEELINGS, unseen_feedback=unseen_feedback, feedback_url_for_lead=feedback_url_for_lead,
    feedback_t=T)


# --------------------------------------------------------------------------
# Client page
# --------------------------------------------------------------------------
def _public_journal(token: str):
    if not token or len(token) > 64:
        abort(404)
    journal = db().execute("SELECT * FROM feedback_journals WHERE token = ?", (token,)).fetchone()
    if journal is None:
        abort(404)
    lead = db().execute("SELECT * FROM leads WHERE id = ?", (journal["lead_id"],)).fetchone()
    if lead is None or lead["status"] != "captured":
        abort(404)
    practitioner = db().execute("SELECT * FROM users WHERE id = ?", (lead["practitioner_id"],)).fetchone() \
        if lead["practitioner_id"] else None
    return journal, lead, practitioner


def _render_public(journal, lead, practitioner, errors=(), form=None, status=200):
    lang = _lang(lead)
    t = T[lang]
    sessions = sessions_for_lead(lead["id"], lang)
    today = engine.local_today()
    form = form or {}
    return render_template(
        "feedback_public.html", journal=journal, lead=lead, practitioner=practitioner, t=t, lang=lang,
        who=_who(practitioner, lang), first_name=_first_name(lead), sessions=sessions,
        entries=entries_for_lead(lead["id"]), errors=list(errors), form=form,
        today=today.isoformat(), max_day=(today + dt.timedelta(days=1)).isoformat(),
        min_day=min([s["date"] for s in sessions], default=(lead["captured_at"] or lead["created_at"])[:10]),
    ), status


@app.get("/f/<token>")
def feedback_public(token):
    journal, lead, practitioner = _public_journal(token)
    return _render_public(journal, lead, practitioner)


@app.post("/f/<token>")
def feedback_submit(token):
    import intake
    journal, lead, practitioner = _public_journal(token)
    if request.form.get("website"):  # honeypot
        return redirect(url_for("feedback_public", token=token))
    lang = _lang(lead)
    t = T[lang]
    sessions = {s["key"]: s for s in sessions_for_lead(lead["id"], lang)}
    session = sessions.get(request.form.get("session", "")) or next(iter(sessions.values()), None)
    body = (request.form.get("body") or "").replace("\r\n", "\n").strip()[:MAX_BODY]
    feeling = request.form.get("feeling", type=int)
    if feeling not in T[lang]["feelings"]:
        feeling = None
    form = {"session": session["key"] if session else "", "day": request.form.get("day", ""), "feeling": feeling,
            "body": body}
    errors = []
    today = engine.local_today()
    try:
        day = dt.date.fromisoformat(request.form.get("day", ""))
    except ValueError:
        day = None
    earliest = dt.date.fromisoformat(session["date"]) if session else today - dt.timedelta(days=365)
    if day is None or not earliest <= day <= today + dt.timedelta(days=1):  # +1: the client may be ahead of us
        errors.append(t["error_day"])
    if not body and feeling is None:
        errors.append(t["error_empty"])
    if errors:
        return _render_public(journal, lead, practitioner, errors, form, 400)
    if not intake.reserve_attempt("feedback", SUBMIT_LIMIT_PER_HOUR):
        return _render_public(journal, lead, practitioner, [t["error_limit"]], form, 429)
    with open_db() as connection:
        entry_id = connection.execute(
            "INSERT INTO feedback_entries (lead_id, session_kind, session_id, session_date, entry_date, feeling,"
            " body, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (lead["id"], session["kind"] if session else None, session["id"] if session else None,
             session["date"] if session else None, day.isoformat(), feeling, body, iso(now_utc()))).lastrowid
        entry = connection.execute("SELECT * FROM feedback_entries WHERE id = ?", (entry_id,)).fetchone()
        recipients = _queue_alerts(connection, lead, entry)
    import emails
    import notify
    emails.kick()
    when = day_label(entry) or day.isoformat()
    notify.queue([r["id"] for r in recipients], {
        "title": f"Feedback from {_first_name(lead)}", "body": f"{when}: {(body or STAFF_FEELINGS[feeling])[:120]}",
        "url": f"/leads/{lead['id']}#feedback", "tag": f"feedback-{lead['id']}"})
    flash(t["saved"].format(who=_who(practitioner, lang)), "info")
    return redirect(url_for("feedback_public", token=token) + "#history")


def _queue_alerts(connection, lead, entry) -> list:
    import emails
    import orders
    recipients = orders._staff_recipients(connection, lead["practitioner_id"])
    for recipient in recipients:
        if not recipient["email_alerts"]:
            continue
        context = {"recipient_name": (recipient["display_name"] or "").split(" ")[0], "full_name": lead["full_name"],
                   "when": day_label(entry) or entry["entry_date"], "session": session_label(entry),
                   "feeling": STAFF_FEELINGS.get(entry["feeling"]) if entry["feeling"] else "",
                   "body": entry["body"], "lead_url": f"{BASE_URL}/leads/{lead['id']}#feedback"}
        text, html = emails._render_pair("feedback_alert", **context)
        emails.enqueue(connection, "feedback_alert", recipient["email"], f"Feedback from {lead['full_name']}",
                       text, html, lead_id=lead["id"], user_id=recipient["id"])
    return list(recipients)


# --------------------------------------------------------------------------
# Practitioner: invite, link
# --------------------------------------------------------------------------
def queue_invite(connection, lead, journal) -> None:
    import emails
    practitioner = connection.execute("SELECT * FROM users WHERE id = ?", (lead["practitioner_id"],)).fetchone() \
        if lead["practitioner_id"] else None
    lang = _lang(lead)
    t = T[lang]
    first_name = _first_name(lead)
    context = {"t": t, "lang": lang, "email_lang": lang, "first_name": first_name, "who": _who(practitioner, lang),
               "page_url": journal_url(journal),
               "card": emails._card(practitioner) if practitioner is not None and practitioner["is_active"] else None}
    text, html = emails._render_pair("feedback_invite", **context)
    emails.enqueue(connection, "feedback_invite", lead["email"], t["invite_subject"].format(first_name=first_name),
                   text, html, lead_id=lead["id"])
    connection.execute("UPDATE feedback_journals SET invited_at = ? WHERE id = ?", (iso(now_utc()), journal["id"]))


def _scoped_lead(lead_id: int):
    import workspace
    return workspace.scoped_lead(lead_id)


@app.post("/leads/<int:lead_id>/feedback")
@login_required
def feedback_invite(lead_id):
    lead = _scoped_lead(lead_id)
    back = url_for("lead_detail", lead_id=lead_id) + "#feedback"
    if not can_invite(lead):
        flash("Feedback links are for clients with a completed reading or a delivered full reading.", "error")
        return redirect(back)
    send = request.form.get("send") == "yes"
    with open_db() as connection:
        journal = ensure_journal(connection, lead_id)
        if send:
            queue_invite(connection, lead, journal)
    if send:
        import emails
        emails.kick()
        flash(f"Emailed {_first_name(lead)} their feedback link.", "info")
    else:
        flash("Feedback link ready. Copy it and send it however you like.", "info")
    return redirect(back)


@app.post("/leads/<int:lead_id>/feedback/new-link")
@login_required
def feedback_new_link(lead_id):
    _scoped_lead(lead_id)
    with open_db() as connection:
        changed = connection.execute("UPDATE feedback_journals SET token = ? WHERE lead_id = ?",
                                     (secrets.token_urlsafe(24), lead_id)).rowcount
    if not changed:
        abort(404)
    flash("New link made. The old link no longer works; send the new one if they should keep writing.", "info")
    return redirect(url_for("lead_detail", lead_id=lead_id) + "#feedback")


@app.after_request
def _feedback_headers(response):
    if request.endpoint in ("feedback_public", "feedback_submit"):
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"  # the link's token never leaks onward
    return response

