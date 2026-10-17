"""Calendar emails.

- Daily: each morning (calendar time zone), the client's personal day with its guidance.
- Monthly: at the start of each month, the month's overview and links.
- Welcome: when a client subscribes, this month's overview straight away.
- Published: when the practitioner publishes a Magialogía month and chooses to tell the client.

A Magialogía calendar only ever emails months the practitioner has published. Every email
carries a one-click way to stop them (signed link, no account needed).
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import calendar_content as content
import calendar_engine as engine
from app import BASE_URL, app, iso, now_utc, open_db

FREQS = ("none", "daily", "monthly")
SEND_HOUR = 7  # local time; the cron runs hourly and sends once the calendar's morning has started
MAIL_KINDS = ("calendar_daily", "calendar_monthly", "calendar_published")

EMAIL_LABELS = {
    "en": {"hi": "Hi {name},", "today": "Today", "open_calendar": "Open your calendar",
           "month_subject": "Your calendar for {month}", "welcome": "Your personal calendar is ready.",
           "published": "{name} has prepared your calendar for {month}.",
           "why": "You're receiving this because you asked for your personal calendar by email.",
           "unsubscribe": "Stop these emails", "email_me": "Calendar by email", "daily": "Every morning",
           "monthly": "Once a month", "none": "No emails", "save": "Save", "saved": "Saved."},
    "es": {"hi": "Hola {name}:", "today": "Hoy", "open_calendar": "Abrir tu calendario",
           "month_subject": "Tu calendario de {month}", "welcome": "Tu calendario personal está listo.",
           "published": "{name} ha preparado tu calendario de {month}.",
           "why": "Recibes este correo porque pediste tu calendario personal por correo electrónico.",
           "unsubscribe": "Dejar de recibir estos correos", "email_me": "Calendario por correo",
           "daily": "Cada mañana", "monthly": "Una vez al mes", "none": "Sin correos", "save": "Guardar",
           "saved": "Guardado."},
    "fr": {"hi": "Bonjour {name},", "today": "Aujourd'hui", "open_calendar": "Ouvrir votre calendrier",
           "month_subject": "Votre calendrier de {month}", "welcome": "Votre calendrier personnel est prêt.",
           "published": "{name} a préparé votre calendrier de {month}.",
           "why": "Vous recevez cet e-mail car vous avez demandé votre calendrier personnel par e-mail.",
           "unsubscribe": "Ne plus recevoir ces e-mails", "email_me": "Calendrier par e-mail",
           "daily": "Chaque matin", "monthly": "Une fois par mois", "none": "Pas d'e-mails", "save": "Enregistrer",
           "saved": "Enregistré."},
    "pt": {"hi": "Olá {name},", "today": "Hoje", "open_calendar": "Abrir seu calendário",
           "month_subject": "Seu calendário de {month}", "welcome": "Seu calendário pessoal está pronto.",
           "published": "{name} preparou seu calendário de {month}.",
           "why": "Você está recebendo isto porque pediu seu calendário pessoal por e-mail.",
           "unsubscribe": "Parar de receber estes e-mails", "email_me": "Calendário por e-mail",
           "daily": "Todas as manhãs", "monthly": "Uma vez por mês", "none": "Sem e-mails", "save": "Salvar",
           "saved": "Salvo."},
    "it": {"hi": "Ciao {name},", "today": "Oggi", "open_calendar": "Apri il tuo calendario",
           "month_subject": "Il tuo calendario di {month}", "welcome": "Il tuo calendario personale è pronto.",
           "published": "{name} ha preparato il tuo calendario di {month}.",
           "why": "Ricevi questa email perché hai chiesto il tuo calendario personale via email.",
           "unsubscribe": "Non ricevere più queste email", "email_me": "Calendario via email",
           "daily": "Ogni mattina", "monthly": "Una volta al mese", "none": "Nessuna email", "save": "Salva",
           "saved": "Salvato."},
    "de": {"hi": "Hallo {name},", "today": "Heute", "open_calendar": "Kalender öffnen",
           "month_subject": "Dein Kalender für {month}", "welcome": "Dein persönlicher Kalender ist fertig.",
           "published": "{name} hat deinen Kalender für {month} vorbereitet.",
           "why": "Du erhältst diese E-Mail, weil du deinen persönlichen Kalender per E-Mail angefordert hast.",
           "unsubscribe": "Diese E-Mails abbestellen", "email_me": "Kalender per E-Mail",
           "daily": "Jeden Morgen", "monthly": "Einmal im Monat", "none": "Keine E-Mails", "save": "Speichern",
           "saved": "Gespeichert."},
}


def email_labels(lang: str) -> dict:
    return EMAIL_LABELS.get(lang) or EMAIL_LABELS["en"]


app.jinja_env.globals["calendar_email_labels"] = email_labels
app.jinja_env.globals["calendar_freqs"] = FREQS


def _unsubscribe(cal) -> str:
    import emails
    return emails.unsubscribe_url("calendar", cal["id"])


def _build(cal, lead, year: int, month: int, mode: str, day: dt.date | None = None):
    """(subject, text, html, headers) for one calendar email."""
    import calendars
    import emails
    view = calendars.month_view(cal, lead, year, month)
    el = email_labels(view["lang"])
    unsubscribe = _unsubscribe(cal)
    month_title = f"{view['month_name']} {year}"
    today = next((d for d in view["days"] if d["date"] == day), None) if day else None
    practitioner_name = view["card"]["name"] if view["card"] else ""
    if mode == "daily":
        subject = f"{view['labels']['personal_day']} {today['number']} · {today['guide']['title']}"
    elif mode == "published" and practitioner_name:
        subject = el["published"].format(name=practitioner_name, month=month_title)
    else:
        subject = el["month_subject"].format(month=month_title)
    context = {**view, "el": el, "mode": mode, "today": today, "month_title": month_title,
               "first_name": view["client_first"], "practitioner_name": practitioner_name,
               "unsubscribe_url": unsubscribe, "email_lang": view["lang"],
               "pdf_url": f"{BASE_URL}/c/{cal['token']}/{view['key']}.pdf",
               "card": emails._card_for_id(lead["practitioner_id"])}
    text, html = emails._render_pair("calendar", **context)
    return subject, text, html, {"List-Unsubscribe": f"<{unsubscribe}>"}


def _queue(connection, kind: str, cal, lead, built) -> None:
    import emails
    subject, text, html, headers = built
    emails.enqueue(connection, kind, lead["email"], subject, text, html, headers, lead_id=lead["id"])


def _lead(connection, cal):
    lead = connection.execute("SELECT * FROM leads WHERE id = ?", (cal["lead_id"],)).fetchone()
    return lead if lead and lead["status"] == "captured" and lead["email"] else None


def _month_published(connection, cal, key: str):
    return connection.execute("SELECT * FROM calendar_months WHERE calendar_id = ? AND month = ?"
                              " AND published_at IS NOT NULL", (cal["id"], key)).fetchone()


def set_frequency(cal_id: int, freq: str) -> None:
    """Change how often a client gets their calendar; subscribing sends this month's overview now."""
    import emails
    if freq not in FREQS:
        raise ValueError(freq)
    today = engine.local_today()
    with app.test_request_context("/", base_url=BASE_URL), open_db() as connection:
        cal = connection.execute("SELECT * FROM calendars WHERE id = ?", (cal_id,)).fetchone()
        if cal is None:
            return
        previous = cal["email_freq"]
        marker = {"daily": today.isoformat(), "monthly": today.strftime("%Y-%m")}.get(freq)
        connection.execute("UPDATE calendars SET email_freq = ?, last_emailed = ?, updated_at = ? WHERE id = ?",
                           (freq, marker, iso(now_utc()), cal_id))
        if freq == "none":
            emails.cancel_calendar_mail(connection, cal["lead_id"])
            return
        lead = _lead(connection, cal)
        if previous == "none" and lead and cal["active"] and (
                cal["kind"] == "numerology" or _month_published(connection, cal, today.strftime("%Y-%m"))):
            _queue(connection, "calendar_monthly", cal, lead, _build(cal, lead, today.year, today.month, "welcome"))
    emails.kick()


def queue_published(cal_id: int, key: str) -> bool:
    """Tell the client a Magialogía month is ready (practitioner's choice when publishing)."""
    import emails
    year, month = engine.parse_month(key)
    with app.test_request_context("/", base_url=BASE_URL), open_db() as connection:
        cal = connection.execute("SELECT * FROM calendars WHERE id = ?", (cal_id,)).fetchone()
        lead = _lead(connection, cal) if cal else None
        if not lead or not cal["active"] or not _month_published(connection, cal, key):
            return False
        _queue(connection, "calendar_published", cal, lead, _build(cal, lead, year, month, "published"))
        connection.execute("UPDATE calendar_months SET emailed_at = ? WHERE calendar_id = ? AND month = ?",
                           (iso(now_utc()), cal_id, key))
    emails.kick()
    return True


def run(now: dt.datetime | None = None) -> dict:
    """Hourly: queue today's daily emails and this month's monthly emails that haven't gone yet."""
    now = now or now_utc()
    local = now.astimezone(ZoneInfo(engine.DEFAULT_TZ))
    if local.hour < SEND_HOUR:
        return {"daily": 0, "monthly": 0, "waiting": "before morning"}
    today, key = local.date(), local.strftime("%Y-%m")
    counts = {"daily": 0, "monthly": 0}
    with app.test_request_context("/", base_url=BASE_URL), open_db() as connection:
        rows = connection.execute("SELECT * FROM calendars WHERE active = 1 AND email_freq IN ('daily', 'monthly')"
                                  " AND (last_emailed IS NULL OR last_emailed NOT IN (?, ?))",
                                  (today.isoformat(), key)).fetchall()
        for cal in rows:
            lead = _lead(connection, cal)
            if lead is None:
                continue
            published = _month_published(connection, cal, key) if cal["kind"] == "magialogia" else None
            if cal["kind"] == "magialogia" and published is None:
                continue  # nothing the client can see yet; try again next hour
            if cal["email_freq"] == "daily":
                _queue(connection, "calendar_daily", cal, lead,
                       _build(cal, lead, today.year, today.month, "daily", day=today))
                connection.execute("UPDATE calendars SET last_emailed = ? WHERE id = ?", (today.isoformat(), cal["id"]))
                counts["daily"] += 1
            else:
                if not (published and published["emailed_at"]):  # the publish email already covered this month
                    _queue(connection, "calendar_monthly", cal, lead,
                           _build(cal, lead, today.year, today.month, "monthly"))
                    counts["monthly"] += 1
                connection.execute("UPDATE calendars SET last_emailed = ? WHERE id = ?", (key, cal["id"]))
    return counts


SAMPLE_MODES = {"calendar_daily": ("daily", "es"), "calendar_monthly": ("welcome", "en"),
                "calendar_published": ("published", "es")}


def sample_email(kind: str, lead: dict, practitioner):
    """An admin test-send example of a calendar email, built from the sample client (nothing stored)."""
    mode, lang = SAMPLE_MODES[kind]
    today = engine.local_today()
    cal = {"id": 0, "lead_id": 0, "kind": "numerology", "lang": lang, "token": "sample", "params": "{}",
           "active": 1, "email_freq": "daily"}
    lead = {**lead, "practitioner_id": practitioner["id"] if practitioner is not None else None}
    return _build(cal, lead, today.year, today.month, mode, day=today if mode == "daily" else None)
