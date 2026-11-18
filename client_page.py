"""Client page: one private page (/me/<token>) with everything a client has on numer.life.

- Their numerology report, their personal calendar with the day journal (or a button to start
  the free calendar), and any full written reading that has been delivered.
- The link is the sign-in, as for the other client pages (no client accounts yet).
- It goes out in the report email, and the practitioner can email it (again) from the lead
  page or replace it if it was shared by mistake.
- Admins can announce it (the calendar and journal) to every client who opted in to emails.
  Only opted-in clients get the announcement (CASL); every announcement carries an unsubscribe link.
"""
from __future__ import annotations

import datetime as dt
import secrets

from flask import abort, flash, redirect, render_template, request, url_for

from app import BASE_URL, app, db, iso, login_required, now_utc, open_db, role_required

T = {
    "en": {
        "title": "Your numer.life page",
        "hi": "Hi {first_name},",
        "intro": "Everything {who} has prepared for you, in one place. Keep this link: it's your private access.",
        "report": "Your numerology report", "report_desc": "Your numbers and what they mean.",
        "calendar": "Your personal calendar & journal",
        "calendar_desc": "Your personal day, every day: what to focus on, what to avoid, lucky numbers and colours, "
                         "moon phases. Tap any day to write a note in your journal; {who} reads your notes.",
        "calendar_open": "Open my calendar", "calendar_start": "Start my free calendar",
        "reading": "Your full written reading", "open": "Open",
        "private": "This page is private to you. Please don't forward it.",
        "your_numerologist": "your numerologist",
        "welcome_subject": "Your numer.life page, {first_name}",
        "announce_subject": "New for you, {first_name}: your personal calendar and journal",
        "email_lead": "Hi {first_name}, your private numer.life page is ready.",
        "email_announce_lead": "Hi {first_name}, there's something new on your private numer.life page.",
        "features": ["Your personal day, every day: what to focus on, what to avoid, lucky numbers and colours",
                     "Moon phases and special days in your month",
                     "A private journal: tap any day to write how you feel. {who} reads your notes and can guide you",
                     "Your report and readings, always at hand"],
        "button": "Open my page",
        "why": "You're receiving this because you asked for your numerology report at numer.life.",
        "unsubscribe": "Stop these emails",
    },
    "es": {
        "title": "Tu página de numer.life",
        "hi": "Hola {first_name}:",
        "intro": "Todo lo que {who} ha preparado para ti, en un solo lugar. Guarda este enlace: es tu acceso privado.",
        "report": "Tu informe de numerología", "report_desc": "Tus números y lo que significan.",
        "calendar": "Tu calendario personal y diario",
        "calendar_desc": "Tu día personal, cada día: en qué enfocarte, qué evitar, números y colores de la suerte, "
                         "fases de la luna. Toca cualquier día para escribir una nota en tu diario; {who} lee tus notas.",
        "calendar_open": "Abrir mi calendario", "calendar_start": "Empezar mi calendario gratis",
        "reading": "Tu lectura completa por escrito", "open": "Abrir",
        "private": "Esta página es solo para ti. Por favor, no la reenvíes.",
        "your_numerologist": "tu numeróloga",
        "welcome_subject": "Tu página de numer.life, {first_name}",
        "announce_subject": "Novedad para ti, {first_name}: tu calendario personal y diario",
        "email_lead": "Hola {first_name}, tu página privada de numer.life está lista.",
        "email_announce_lead": "Hola {first_name}, hay algo nuevo en tu página privada de numer.life.",
        "features": ["Tu día personal, cada día: en qué enfocarte, qué evitar, números y colores de la suerte",
                     "Las fases de la luna y los días especiales de tu mes",
                     "Un diario privado: toca cualquier día para escribir cómo te sientes. {who} lee tus notas y puede guiarte",
                     "Tu informe y tus lecturas, siempre a mano"],
        "button": "Abrir mi página",
        "why": "Recibes este correo porque pediste tu informe de numerología en numer.life.",
        "unsubscribe": "Dejar de recibir estos correos",
    },
}


def _lang(lead) -> str:
    import orders
    lang = orders._lead_lang(lead)
    return lang if lang in T else "en"


def _first_name(lead) -> str:
    return (lead["full_name"].split() or ["there"])[0]


def _who(practitioner, lang: str) -> str:
    return practitioner["display_name"] if practitioner is not None and practitioner["display_name"] \
        else T[lang]["your_numerologist"]


# --------------------------------------------------------------------------
# Pages and links
# --------------------------------------------------------------------------
def page_for_lead(lead_id: int):
    return db().execute("SELECT * FROM client_pages WHERE lead_id = ?", (lead_id,)).fetchone()


def ensure_page(connection, lead_id: int):
    connection.execute("INSERT OR IGNORE INTO client_pages (lead_id, token, created_at) VALUES (?, ?, ?)",
                       (lead_id, secrets.token_urlsafe(24), iso(now_utc())))
    return connection.execute("SELECT * FROM client_pages WHERE lead_id = ?", (lead_id,)).fetchone()


def page_url(page) -> str:
    return f"{BASE_URL}/me/{page['token']}"


def page_url_for_lead(lead_id: int) -> str | None:
    page = page_for_lead(lead_id)
    return page_url(page) if page is not None else None


app.jinja_env.globals.update(client_page=page_for_lead, client_page_url=page_url)


# --------------------------------------------------------------------------
# Emails
# --------------------------------------------------------------------------
def build_email(lead, practitioner, page, mode: str = "welcome") -> tuple[str, str, str, dict]:
    """(subject, text, html, headers). mode: welcome (one client) | announce (bulk, with unsubscribe)."""
    import emails
    lang = _lang(lead)
    t = T[lang]
    who = _who(practitioner, lang)
    first_name = _first_name(lead)
    unsubscribe = emails.unsubscribe_url("client", lead["email"]) if mode == "announce" else None
    context = {"t": t, "lang": lang, "email_lang": lang, "mode": mode, "first_name": first_name, "who": who,
               "features": [f.format(who=who) for f in t["features"]], "page_url": page_url(page),
               "unsubscribe_url": unsubscribe,
               "card": emails._card(practitioner) if practitioner is not None and practitioner["is_active"] else None}
    text, html = emails._render_pair("client_page", **context)
    subject = t["announce_subject" if mode == "announce" else "welcome_subject"].format(first_name=first_name)
    return subject, text, html, ({"List-Unsubscribe": f"<{unsubscribe}>"} if unsubscribe else {})


def _practitioner(connection, lead):
    return connection.execute("SELECT * FROM users WHERE id = ?", (lead["practitioner_id"],)).fetchone() \
        if lead["practitioner_id"] else None


def queue_email(connection, lead, mode: str = "welcome") -> None:
    import emails
    page = ensure_page(connection, lead["id"])
    subject, text, html, headers = build_email(lead, _practitioner(connection, lead), page, mode)
    emails.enqueue(connection, f"client_{mode}", lead["email"], subject, text, html, headers, lead_id=lead["id"])
    column = "announced_at" if mode == "announce" else "emailed_at"
    connection.execute(f"UPDATE client_pages SET {column} = ? WHERE id = ?", (iso(now_utc()), page["id"]))


def announce_candidates(connection) -> list:
    """Clients who opted in to emails and haven't had the announcement yet."""
    return connection.execute(
        "SELECT leads.* FROM leads LEFT JOIN client_pages ON client_pages.lead_id = leads.id"
        " WHERE leads.status = 'captured' AND leads.email != '' AND leads.marketing_opt_in = 1"
        " AND client_pages.announced_at IS NULL ORDER BY leads.id").fetchall()


def still_wanted(connection, row) -> bool:
    if row["kind"] != "client_announce":
        return True
    lead = connection.execute("SELECT marketing_opt_in FROM leads WHERE id = ?", (row["lead_id"],)).fetchone()
    return bool(lead and lead["marketing_opt_in"])


def _announce_count() -> int:
    return len(announce_candidates(db()))


app.jinja_env.globals["client_announce_count"] = _announce_count


# --------------------------------------------------------------------------
# Client page
# --------------------------------------------------------------------------
def _public_page(token: str):
    if not token or len(token) > 64:
        abort(404)
    page = db().execute("SELECT * FROM client_pages WHERE token = ?", (token,)).fetchone()
    if page is None:
        abort(404)
    lead = db().execute("SELECT * FROM leads WHERE id = ?", (page["lead_id"],)).fetchone()
    if lead is None or lead["status"] != "captured":
        abort(404)
    return page, lead


@app.get("/me/<token>")
def client_page_public(token):
    import calendar_engine as engine
    page, lead = _public_page(token)
    connection = db()
    practitioner = _practitioner(connection, lead)
    lang = _lang(lead)
    t = T[lang]
    cal = connection.execute("SELECT * FROM calendars WHERE lead_id = ?", (lead["id"],)).fetchone()
    month_key = engine.local_today().strftime("%Y-%m")
    readings = connection.execute("SELECT token, delivered_at FROM orders WHERE lead_id = ? AND status = 'delivered'"
                                  " ORDER BY delivered_at DESC", (lead["id"],)).fetchall()
    from practitioner_profile import contact_card
    return render_template(
        "client_page.html", page=page, lead=lead, t=t, lang=lang, who=_who(practitioner, lang),
        first_name=_first_name(lead), cal=cal, month_key=month_key, readings=readings,
        card=contact_card(practitioner) if practitioner is not None and practitioner["is_active"] else None)


@app.post("/me/<token>/calendar")
def client_page_start_calendar(token):
    """The client starts their free numerology calendar (no emails until they choose them)."""
    import calendar_content as content
    page, lead = _public_page(token)
    lang = _lang(lead)
    with open_db() as connection:
        cal = connection.execute("SELECT * FROM calendars WHERE lead_id = ?", (lead["id"],)).fetchone()
        if cal is None:
            connection.execute(
                "INSERT INTO calendars (lead_id, practitioner_id, kind, lang, token, created_at)"
                " VALUES (?, ?, 'numerology', ?, ?, ?)",
                (lead["id"], lead["practitioner_id"], lang if lang in content.LANGUAGES else "en",
                 secrets.token_urlsafe(18), iso(now_utc())))
            cal = connection.execute("SELECT * FROM calendars WHERE lead_id = ?", (lead["id"],)).fetchone()
    if not cal["active"]:
        abort(404)  # the practitioner paused this client's calendar
    return redirect(url_for("calendar_public", token=cal["token"]))


@app.after_request
def _client_page_headers(response):
    if request.endpoint in ("client_page_public", "client_page_start_calendar"):
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


# --------------------------------------------------------------------------
# Practitioner and admin
# --------------------------------------------------------------------------
@app.post("/leads/<int:lead_id>/client-page")
@login_required
def client_page_send(lead_id):
    import emails
    import workspace
    lead = workspace.scoped_lead(lead_id)
    back = url_for("lead_detail", lead_id=lead_id) + "#client-page"
    if lead["status"] != "captured":
        flash("The client page is for visitors who unlocked their report.", "error")
        return redirect(back)
    send = request.form.get("send") == "yes" and bool(lead["email"])
    with open_db() as connection:
        if send:
            queue_email(connection, lead, "welcome")
        else:
            ensure_page(connection, lead_id)
    if send:
        emails.kick()
        flash(f"Emailed {_first_name(lead)} their client page.", "info")
    else:
        flash("Client page link ready. Copy it and send it however you like.", "info")
    return redirect(back)


@app.post("/leads/<int:lead_id>/client-page/new-link")
@login_required
def client_page_new_link(lead_id):
    import workspace
    workspace.scoped_lead(lead_id)
    with open_db() as connection:
        changed = connection.execute("UPDATE client_pages SET token = ? WHERE lead_id = ?",
                                     (secrets.token_urlsafe(24), lead_id)).rowcount
    if not changed:
        abort(404)
    flash("New link made. The old link no longer works; send the new one.", "info")
    return redirect(url_for("lead_detail", lead_id=lead_id) + "#client-page")


@app.post("/admin/email/announce-client-pages")
@role_required("admin")
def admin_announce_client_pages():
    import emails
    if request.form.get("confirm") != "yes":
        flash("Tick the box to confirm the announcement.", "error")
        return redirect(url_for("admin") + "#email")
    with open_db() as connection:
        leads = announce_candidates(connection)
        for lead in leads:
            queue_email(connection, lead, "announce")
    emails.kick()
    flash(f"Announcement queued for {len(leads)} client{'s' if len(leads) != 1 else ''} who opted in to emails.",
          "info")
    return redirect(url_for("admin") + "#email")


def sample_email(lead, practitioner, mode: str):
    """For Admin > Email test sends: a dummy page link, never a real token."""
    return build_email(lead, practitioner, {"token": "sample"}, mode)
