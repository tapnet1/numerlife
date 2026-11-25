"""Paid appointments: clients book a session with their practitioner and pay online.

1. The practitioner turns bookings on (/settings/bookings): price (CAD $199 to start), length,
   session type, online/phone/in person with the details clients get after paying, weekly
   hours, days off, minimum notice and how far ahead clients can book.
2. The client opens "Book a session" on their private client page (/me/<token>/book), picks a
   free time and pays with PayPal, into the practitioner's own PayPal when connected (same rule as
   the calendar plan and paid readings: orders.seller_account). The time is held for 30 minutes
   while they pay.
3. The server captures the payment itself (PayPal's answer is the only proof of payment). Then:
   a scheduled reading is added to the client's record, the client gets a confirmation email with
   "Add to calendar" (Google, Outlook, Apple/.ics) and their booking page (/b/<token>), and the
   practitioner and team get an email and a push alert.
4. The `bookings` job (every 5 minutes) captures checkouts whose buyer didn't come back, and sends
   reminders: the client gets an email the day before and an hour before; the practitioner and
   team get a push an hour before.
5. The appointment follows the reading: if the practitioner changes its time in the reading editor,
   the client is emailed the new time (and their calendar file updates); if she cancels it, the
   client is told. Refunds are made in PayPal, then marked by an admin.

25% of each booking is TAPNET's fee (monthly invoice), as for every sale through numer.life.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import secrets
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import click
from flask import Response, abort, flash, redirect, render_template, request, url_for

import billing
import calendar_engine as engine
import paypal
from app import (BASE_URL, SITE_NAME, app, current_user, db, iso, login_required, now_utc, open_db,
                 role_required)

CURRENCY = "CAD"
DEFAULT_PRICE_CENTS = 19900
HOLD_MINUTES = 30          # a time is held this long while the client pays
OPEN_PAYMENT_DAYS = 3      # an unpaid checkout is given up after this
DAY_REMINDER = dt.timedelta(hours=24)
HOUR_REMINDER = dt.timedelta(minutes=65)  # the job runs every 5 minutes: "about an hour before"
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
TIMEZONES = ("America/Edmonton", "America/Vancouver", "America/Regina", "America/Winnipeg", "America/Toronto",
             "America/Halifax", "America/St_Johns", "America/Los_Angeles", "America/Denver", "America/Chicago",
             "America/New_York", "America/Mexico_City", "America/Bogota", "America/Lima", "America/Caracas",
             "America/Santiago", "America/Argentina/Buenos_Aires", "Europe/Madrid", "Europe/London", "Europe/Rome")
DEFAULTS = {"enabled": False, "price_cents": DEFAULT_PRICE_CENTS, "duration_min": 60, "buffer_min": 15,
            "notice_hours": 24, "window_days": 30, "kind": "numerology", "mode": "online", "details": "",
            "tz": engine.DEFAULT_TZ, "hours": {str(d): "" for d in range(7)}, "days_off": ""}

T = {
    "en": {
        "book_title": "Book a session with {who}",
        "tile_title": "Book a session", "tile_desc": "{session} with {who} · {minutes} min · {price}",
        "tile_button": "📅 Choose a time",
        "intro": "Choose a time that suits you, then pay securely with PayPal. Your time is held for 30 minutes while you pay.",
        "times_in": "Times are shown in {zone} time.", "your_time": "Your time:",
        "no_times": "There are no free times in the next weeks. Please check back soon or contact {who}.",
        "unavailable": "Online booking isn't available right now. Please contact {who}.",
        "pay": "Continue to payment · {price}",
        "choose": "Please choose a time.",
        "taken": "Sorry, that time was just taken. Please choose another.",
        "failed": "We couldn't reach PayPal just now. Please try again in a few minutes.",
        "declined": "Your payment didn't go through, so you haven't been charged. Please try again.",
        "unconfirmed": "We couldn't confirm your payment with PayPal yet. If you were charged, we'll confirm it within a few minutes and email you.",
        "cancelled_checkout": "Checkout cancelled, so the time was released. If you did complete a payment in another window, you'll receive a confirmation email within a few minutes.",
        "limit": "Too many attempts from your connection. Please try again in an hour.",
        "upcoming": "Your appointments",
        "page_title": "Your appointment with {who}",
        "status_confirmed": "Confirmed", "status_pending": "Waiting for payment", "status_cancelled": "Cancelled",
        "status_completed": "Completed", "status_conflict": "Paid: {who} will contact you to confirm the time",
        "status_refunded": "Refunded",
        "when": "When", "length": "Length", "how": "How", "paid": "Paid",
        "details": "How to join",
        "add": "Add to your calendar", "google": "Google Calendar", "outlook": "Outlook", "apple": "Apple / other (.ics)",
        "change": "Need to change the time? Contact {who}.",
        "modes": {"online": "Online (video)", "phone": "Phone", "in_person": "In person"},
        "kinds": {"numerology": "Numerology session", "magialogia": "Magialogía Numérica session", "other": "Session"},
        "your_numerologist": "your numerologist", "zone_time": "{zone} time", "minutes": "{n} minutes",
        "subject_confirmed": "Booked: your {session} on {date}",
        "subject_conflict": "Payment received: {who} will confirm your time",
        "subject_day": "Tomorrow: your {session} with {who}",
        "subject_hour": "In about an hour: your {session} with {who}",
        "subject_moved": "New time for your {session}: {date}",
        "subject_cancelled": "Cancelled: your {session} on {date}",
        "lead_confirmed": "Hi {first_name}, your {session} with {who} is booked and paid. See you soon!",
        "lead_conflict": "Hi {first_name}, we received your payment, but the time you chose was taken while you were paying. {who} will contact you to agree on a new time (or refund you).",
        "lead_day": "Hi {first_name}, a reminder that your {session} with {who} is tomorrow.",
        "lead_hour": "Hi {first_name}, your {session} with {who} starts in about an hour.",
        "lead_moved": "Hi {first_name}, {who} changed the time of your {session}. Here is the new time.",
        "lead_cancelled": "Hi {first_name}, {who} cancelled your {session}. If you paid online, {who} will contact you about a refund or a new time.",
        "calendar_note": "Add it to your calendar so you get a reminder too.",
        "open_page": "Open my appointment",
        "receipt": "Receipt", "amount": "Amount", "booking": "Booking",
        "paid_via": "Paid to {who} through PayPal.",
    },
    "es": {
        "book_title": "Reserva una sesión con {who}",
        "tile_title": "Reserva una sesión", "tile_desc": "{session} con {who} · {minutes} min · {price}",
        "tile_button": "📅 Elegir una hora",
        "intro": "Elige la hora que mejor te venga y paga de forma segura con PayPal. Te guardamos la hora 30 minutos mientras pagas.",
        "times_in": "Las horas están en la hora de {zone}.", "your_time": "Tu hora:",
        "no_times": "No hay horas libres en las próximas semanas. Vuelve a mirar pronto o escribe a {who}.",
        "unavailable": "La reserva en línea no está disponible ahora. Por favor, escribe a {who}.",
        "pay": "Continuar al pago · {price}",
        "choose": "Por favor, elige una hora.",
        "taken": "Lo sentimos, esa hora se acaba de reservar. Por favor, elige otra.",
        "failed": "No pudimos conectar con PayPal. Inténtalo de nuevo en unos minutos.",
        "declined": "Tu pago no se completó, así que no se te ha cobrado. Inténtalo de nuevo.",
        "unconfirmed": "Aún no pudimos confirmar tu pago con PayPal. Si se te cobró, lo confirmaremos en unos minutos y te enviaremos un correo.",
        "cancelled_checkout": "Pago cancelado: la hora quedó libre. Si completaste un pago en otra ventana, recibirás un correo de confirmación en unos minutos.",
        "limit": "Demasiados intentos desde tu conexión. Inténtalo de nuevo en una hora.",
        "upcoming": "Tus citas",
        "page_title": "Tu cita con {who}",
        "status_confirmed": "Confirmada", "status_pending": "Pendiente de pago", "status_cancelled": "Cancelada",
        "status_completed": "Realizada", "status_conflict": "Pagada: {who} te contactará para confirmar la hora",
        "status_refunded": "Reembolsada",
        "when": "Cuándo", "length": "Duración", "how": "Cómo", "paid": "Pagado",
        "details": "Cómo conectarte",
        "add": "Añádela a tu calendario", "google": "Google Calendar", "outlook": "Outlook", "apple": "Apple / otros (.ics)",
        "change": "¿Necesitas cambiar la hora? Escribe a {who}.",
        "modes": {"online": "En línea (videollamada)", "phone": "Por teléfono", "in_person": "En persona"},
        "kinds": {"numerology": "Sesión de numerología", "magialogia": "Sesión de Magialogía Numérica", "other": "Sesión"},
        "your_numerologist": "tu numeróloga", "zone_time": "hora de {zone}", "minutes": "{n} minutos",
        "subject_confirmed": "Reservada: tu {session} el {date}",
        "subject_conflict": "Pago recibido: {who} confirmará tu hora",
        "subject_day": "Mañana: tu {session} con {who}",
        "subject_hour": "En una hora: tu {session} con {who}",
        "subject_moved": "Nueva hora para tu {session}: {date}",
        "subject_cancelled": "Cancelada: tu {session} del {date}",
        "lead_confirmed": "Hola {first_name}, tu {session} con {who} está reservada y pagada. ¡Hasta pronto!",
        "lead_conflict": "Hola {first_name}, recibimos tu pago, pero la hora que elegiste se reservó mientras pagabas. {who} te contactará para acordar otra hora (o reembolsarte).",
        "lead_day": "Hola {first_name}, te recordamos que tu {session} con {who} es mañana.",
        "lead_hour": "Hola {first_name}, tu {session} con {who} empieza en una hora aproximadamente.",
        "lead_moved": "Hola {first_name}, {who} cambió la hora de tu {session}. Esta es la nueva hora.",
        "lead_cancelled": "Hola {first_name}, {who} canceló tu {session}. Si pagaste en línea, {who} te contactará para el reembolso o una nueva hora.",
        "calendar_note": "Añádela a tu calendario para recibir también un recordatorio.",
        "open_page": "Abrir mi cita",
        "receipt": "Recibo", "amount": "Importe", "booking": "Reserva",
        "paid_via": "Pagado a {who} con PayPal.",
    },
}
MONTHS = {"en": ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                 "November", "December"),
          "es": ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
                 "noviembre", "diciembre")}
DAYS = {"en": WEEKDAYS, "es": ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")}
STAFF_KINDS = {"numerology": "Numerology reading", "magialogia": "Magialogía Numérica reading", "other": "Session"}


# --------------------------------------------------------------------------
# Practitioner settings
# --------------------------------------------------------------------------
def settings(user) -> dict:
    stored = {}
    if user is not None and "booking" in user.keys() and user["booking"]:
        try:
            stored = json.loads(user["booking"])
        except ValueError:
            stored = {}
    merged = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
    merged["hours"] = {**DEFAULTS["hours"], **(stored.get("hours") or {})}
    return merged


def zone(s: dict) -> ZoneInfo:
    try:
        return ZoneInfo(s["tz"])
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo(engine.DEFAULT_TZ)


def zone_name(tz: str) -> str:
    return tz.split("/")[-1].replace("_", " ")


_WINDOW = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*-\s*(\d{1,2})(?::(\d{2}))?\s*$")


def parse_windows(text: str) -> list[tuple[dt.time, dt.time]]:
    """'10:00-13:00, 15:00-18:30' -> [(10:00, 13:00), (15:00, 18:30)]. Raises ValueError."""
    windows = []
    for part in (text or "").replace(";", ",").split(","):
        if not part.strip():
            continue
        match = _WINDOW.match(part)
        if not match:
            raise ValueError(part.strip())
        h1, m1, h2, m2 = (int(g or 0) for g in match.groups())
        if h1 > 23 or h2 > 24 or m1 > 59 or m2 > 59:
            raise ValueError(part.strip())
        start = dt.time(h1, m1)
        end = dt.time(23, 59) if h2 == 24 else dt.time(h2, m2)
        if end <= start:
            raise ValueError(part.strip())
        windows.append((start, end))
    return sorted(windows)


def parse_days_off(text: str) -> set[dt.date]:
    days = set()
    for part in re.split(r"[\s,;]+", text or ""):
        if part:
            days.add(dt.date.fromisoformat(part))  # ValueError for anything else
    return days


def practitioner_for_lead(lead):
    if lead is None or not lead["practitioner_id"]:
        return None
    return db().execute("SELECT * FROM users WHERE id = ? AND is_active = 1 AND role = 'practitioner'",
                        (lead["practitioner_id"],)).fetchone()


def bookable(lead) -> bool:
    """The client's practitioner takes bookings, and the client has someone to pay."""
    import orders
    practitioner = practitioner_for_lead(lead)
    if practitioner is None or not settings(practitioner)["enabled"]:
        return False
    return orders.seller_account(lead) is not None


# --------------------------------------------------------------------------
# Free times
# --------------------------------------------------------------------------
def _busy(connection, practitioner_id: int, s: dict, start: dt.datetime, end: dt.datetime,
          exclude_booking: int | None = None) -> list[tuple[dt.datetime, dt.datetime]]:
    """Intervals (UTC) the practitioner is taken: scheduled readings (her local time), busy times from
    her own calendar link, and times held by other clients who are paying right now."""
    tz = zone(s)
    busy = []
    rows = connection.execute(
        "SELECT scheduled_at, duration_min FROM readings WHERE practitioner_id = ? AND status = 'scheduled'"
        " AND scheduled_at BETWEEN ? AND ?",
        (practitioner_id, (start.astimezone(tz) - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
         (end.astimezone(tz) + dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"))).fetchall()
    for row in rows:
        begin = dt.datetime.fromisoformat(row["scheduled_at"]).replace(tzinfo=tz).astimezone(dt.timezone.utc)
        busy.append((begin, begin + dt.timedelta(minutes=row["duration_min"])))
    import calendar_sync
    busy += calendar_sync.stored_busy(connection, practitioner_id, start, end)   # her own calendar (link)
    holds = connection.execute(
        "SELECT starts_at, duration_min FROM bookings WHERE practitioner_id = ? AND status = 'pending'"
        " AND held_until > ? AND id != ?", (practitioner_id, iso(now_utc()), exclude_booking or 0)).fetchall()
    for row in holds:
        begin = dt.datetime.fromisoformat(row["starts_at"])
        busy.append((begin, begin + dt.timedelta(minutes=row["duration_min"])))
    return busy


def _clashes(begin: dt.datetime, end: dt.datetime, busy, buffer_min: int) -> bool:
    pad = dt.timedelta(minutes=buffer_min)
    return any(begin < b_end + pad and end + pad > b_begin for b_begin, b_end in busy)


def free_slots(connection, practitioner, now: dt.datetime | None = None) -> list[dt.datetime]:
    """Start times (UTC) a client can book now, earliest first."""
    s = settings(practitioner)
    tz = zone(s)
    now = now or now_utc()
    earliest = now + dt.timedelta(hours=s["notice_hours"])
    today = now.astimezone(tz).date()
    last = today + dt.timedelta(days=s["window_days"])
    try:
        days_off = parse_days_off(s["days_off"])
    except ValueError:
        days_off = set()
    length = dt.timedelta(minutes=s["duration_min"])
    step = dt.timedelta(minutes=s["duration_min"] + s["buffer_min"])
    busy = _busy(connection, practitioner["id"], s, now, dt.datetime.combine(last, dt.time(23, 59), tz))
    slots = []
    day = today
    while day <= last:
        if day not in days_off:
            try:
                windows = parse_windows(s["hours"].get(str(day.weekday()), ""))
            except ValueError:
                windows = []
            for open_at, close_at in windows:
                begin = dt.datetime.combine(day, open_at, tz)
                close = dt.datetime.combine(day, close_at, tz)
                while begin + length <= close:
                    start_utc = begin.astimezone(dt.timezone.utc)
                    if start_utc >= earliest and not _clashes(start_utc, start_utc + length, busy, s["buffer_min"]):
                        slots.append(start_utc)
                    begin += step
        day += dt.timedelta(days=1)
    return sorted(set(slots))


def group_by_day(slots: list[dt.datetime], tz: ZoneInfo, lang: str) -> list[dict]:
    days: dict = {}
    for slot in slots:
        local = slot.astimezone(tz)
        days.setdefault(local.date(), []).append({"value": slot.isoformat(), "local": local.isoformat(),
                                                  "label": time_text(local, lang)})
    return [{"label": date_text(day, lang), "slots": items} for day, items in days.items()]


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------
def time_text(moment: dt.datetime, lang: str) -> str:
    if lang == "es":
        return moment.strftime("%H:%M")
    return moment.strftime("%-I:%M %p").replace("AM", "am").replace("PM", "pm")


def date_text(day: dt.date, lang: str) -> str:
    lang = lang if lang in MONTHS else "en"
    weekday, month = DAYS[lang][day.weekday()], MONTHS[lang][day.month - 1]
    if lang == "es":
        return f"{weekday} {day.day} de {month} de {day.year}"
    return f"{weekday}, {month} {day.day}, {day.year}"


def when_text(moment: dt.datetime, tz: str, lang: str) -> str:
    t = T[lang if lang in T else "en"]
    local = moment.astimezone(ZoneInfo(tz))
    return f"{date_text(local.date(), lang)} · {time_text(local, lang)} ({t['zone_time'].format(zone=zone_name(tz))})"


def mid_sentence(name: str) -> str:
    """'Numerology session' -> 'numerology session' (proper names like Magialogía keep their capital)."""
    return name if name.startswith("Magialog") else name[:1].lower() + name[1:]


def price_text(cents: int) -> str:
    return f"CAD {billing.money(cents)}"


def _lang(lead) -> str:
    import client_page
    return client_page._lang(lead)


def _who(practitioner, lang: str) -> str:
    return practitioner["display_name"] if practitioner is not None and practitioner["display_name"] \
        else T[lang]["your_numerologist"]


def _first_name(lead) -> str:
    return (lead["full_name"].split() or ["there"])[0]


# --------------------------------------------------------------------------
# Appointment state (follows the reading once paid)
# --------------------------------------------------------------------------
def appointment(booking, connection=None) -> dict:
    """Where the appointment stands now: time (UTC), length, mode, kind and status.

    Once paid, the reading on the client's record is the source of truth: if the practitioner moves
    or cancels it, the booking page, calendar file and reminders follow."""
    connection = connection or db()
    tz = ZoneInfo(booking["tz"])
    start = dt.datetime.fromisoformat(booking["starts_at"])
    minutes, mode, kind = booking["duration_min"], booking["mode"], booking["kind"]
    status = {"pending": "pending", "cancelled": "cancelled", "refunded": "refunded"}.get(booking["status"])
    reading = None
    if booking["reading_id"]:
        reading = connection.execute("SELECT * FROM readings WHERE id = ?", (booking["reading_id"],)).fetchone()
    if reading is not None:
        start = dt.datetime.fromisoformat(reading["scheduled_at"]).replace(tzinfo=tz).astimezone(dt.timezone.utc)
        minutes, mode, kind = reading["duration_min"], reading["mode"], reading["kind"]
        if status is None:
            status = {"scheduled": "confirmed", "completed": "completed"}.get(reading["status"], "cancelled")
    elif status is None:
        status = "conflict" if booking["conflict"] else "confirmed"
    return {"start": start, "end": start + dt.timedelta(minutes=minutes), "minutes": minutes, "mode": mode,
            "kind": kind, "status": status, "tz": booking["tz"],
            "local_key": reading["scheduled_at"] if reading is not None else booking["starts_at"]}


def bookings_for_lead(lead_id: int, upcoming_only: bool = False) -> list:
    rows = db().execute("SELECT * FROM bookings WHERE lead_id = ? AND status IN ('paid', 'refunded')"
                        " ORDER BY starts_at DESC", (lead_id,)).fetchall()
    result = []
    for row in rows:
        state = appointment(row)
        if upcoming_only and (state["status"] not in ("confirmed", "conflict") or state["end"] < now_utc()):
            continue
        result.append({"row": row, **state})
    return result


def booking_for_reading(reading_id: int):
    return db().execute("SELECT * FROM bookings WHERE reading_id = ?", (reading_id,)).fetchone()


def page_url(booking) -> str:
    return f"{BASE_URL}/b/{booking['token']}"


# --------------------------------------------------------------------------
# Add to calendar
# --------------------------------------------------------------------------
def _ics_text(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\n") \
        .replace("\n", "\\n")


def _fold(line: str) -> str:
    out, chunk = [], b""
    for char in line:
        encoded = char.encode()
        if len(chunk) + len(encoded) > (75 if not out else 74):
            out.append(chunk.decode())
            chunk = b""
        chunk += encoded
    out.append(chunk.decode())
    return "\r\n ".join(out)


def _stamp(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def event_info(booking, practitioner, lang: str, state: dict | None = None) -> dict:
    t = T[lang]
    state = state or appointment(booking)
    who = _who(practitioner, lang)
    session = t["kinds"].get(state["kind"], t["kinds"]["other"])
    s = settings(practitioner)
    details = s["details"].strip()
    description = "\n\n".join(part for part in (details, f"{t['open_page']}: {page_url(booking)}") if part)
    location = details.splitlines()[0][:200] if details else t["modes"].get(state["mode"], "")
    return {"title": f"{session} · {who}", "description": description, "location": location, **state}


def ics(booking, practitioner, lang: str) -> str:
    info = event_info(booking, practitioner, lang)
    cancelled = info["status"] in ("cancelled", "refunded")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//numer.life//Bookings//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "BEGIN:VEVENT", f"UID:booking-{booking['id']}@numer.life",
             f"SEQUENCE:{booking['sequence']}", f"DTSTAMP:{_stamp(now_utc())}",
             f"DTSTART:{_stamp(info['start'])}", f"DTEND:{_stamp(info['end'])}",
             f"SUMMARY:{_ics_text(info['title'])}", f"DESCRIPTION:{_ics_text(info['description'])}",
             f"LOCATION:{_ics_text(info['location'])}", f"URL:{page_url(booking)}",
             f"STATUS:{'CANCELLED' if cancelled else 'CONFIRMED'}"]
    if not cancelled:
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_ics_text(info['title'])}", "TRIGGER:-PT1H",
                  "END:VALARM"]
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def calendar_links(booking, practitioner, lang: str, state: dict | None = None) -> dict:
    info = event_info(booking, practitioner, lang, state)
    google = "https://calendar.google.com/calendar/render?" + urlencode({
        "action": "TEMPLATE", "text": info["title"], "dates": f"{_stamp(info['start'])}/{_stamp(info['end'])}",
        "details": info["description"], "location": info["location"]})
    outlook = "https://outlook.live.com/calendar/0/deeplink/compose?" + urlencode({
        "path": "/calendar/action/compose", "rru": "addevent", "subject": info["title"],
        "startdt": info["start"].strftime("%Y-%m-%dT%H:%M:%SZ"), "enddt": info["end"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "body": info["description"], "location": info["location"]})
    return {"google": google, "outlook": outlook, "ics": f"{page_url(booking)}/appointment.ics"}


def when_for(item: dict, lang: str = "en") -> str:
    return when_text(item["start"], item["tz"], lang)


app.jinja_env.globals.update(booking_when=when_for, booking_t=T, booking_practitioner=practitioner_for_lead,
                             bookings_for_lead=bookings_for_lead, booking_for_reading=booking_for_reading,
                             booking_page_url=page_url, booking_settings=settings, booking_bookable=bookable,
                             booking_price=price_text)


# --------------------------------------------------------------------------
# Client: choose a time and pay
# --------------------------------------------------------------------------
def _client(token: str):
    import client_page
    page, lead = client_page._public_page(token)
    return page, lead


@app.get("/me/<token>/book")
def booking_choose(token):
    page, lead = _client(token)
    lang = _lang(lead)
    t = T[lang]
    practitioner = practitioner_for_lead(lead)
    who = _who(practitioner, lang)
    if not bookable(lead):
        flash(t["unavailable"].format(who=who), "error")
        return redirect(url_for("client_page_public", token=token))
    s = settings(practitioner)
    days = group_by_day(free_slots(db(), practitioner), zone(s), lang)
    return render_template("booking_choose.html", page=page, lead=lead, t=t, lang=lang, who=who, s=s, days=days,
                           session=t["kinds"].get(s["kind"], t["kinds"]["other"]), price=price_text(s["price_cents"]),
                           zone=zone_name(s["tz"]), mode=t["modes"].get(s["mode"], ""),
                           upcoming=bookings_for_lead(lead["id"], upcoming_only=True))


@app.post("/me/<token>/book")
def booking_checkout(token):
    import intake
    import orders
    page, lead = _client(token)
    lang = _lang(lead)
    t = T[lang]
    back = url_for("booking_choose", token=token)
    practitioner = practitioner_for_lead(lead)
    account = orders.seller_account(lead)
    if not bookable(lead) or account is None:
        flash(t["unavailable"].format(who=_who(practitioner, lang)), "error")
        return redirect(url_for("client_page_public", token=token))
    try:
        slot = dt.datetime.fromisoformat(request.form.get("slot", ""))
        if slot.tzinfo is None:
            raise ValueError
        slot = slot.astimezone(dt.timezone.utc)
    except ValueError:
        flash(t["choose"], "error")
        return redirect(back)
    if not intake.reserve_attempt("book", 10):
        flash(t["limit"], "error")
        return redirect(back)
    s = settings(practitioner)
    now = iso(now_utc())
    connection = db()
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")  # check the time is free and hold it, as one step
    try:
        # A new checkout replaces this client's earlier unpaid one (its hold is released).
        connection.execute("UPDATE bookings SET status = 'cancelled', updated_at = ? WHERE lead_id = ?"
                           " AND status = 'pending'", (now, lead["id"]))
        if slot not in free_slots(connection, practitioner):
            connection.rollback()
            flash(t["taken"], "error")
            return redirect(back)
        booking_id = connection.execute(
            "INSERT INTO bookings (lead_id, practitioner_id, token, starts_at, tz, duration_min, kind, mode,"
            " amount_cents, currency, status, paypal_account, held_until, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
            (lead["id"], practitioner["id"], secrets.token_urlsafe(24), slot.isoformat(), s["tz"], s["duration_min"],
             s["kind"], s["mode"], s["price_cents"], CURRENCY, account.key,
             iso(now_utc() + dt.timedelta(minutes=HOLD_MINUTES)), now, now)).lastrowid
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    session = t["kinds"].get(s["kind"], t["kinds"]["other"])
    try:  # network call: no database lock held
        paypal_id, approve_url = paypal.create_order(
            reference=f"booking-{booking_id}", cents=s["price_cents"], currency=CURRENCY,
            description=f"{SITE_NAME}: {session}, {slot.astimezone(zone(s)).strftime('%Y-%m-%d %H:%M')}",
            return_url=f"{BASE_URL}/paypal/booking/return", cancel_url=f"{BASE_URL}/paypal/booking/cancel",
            brand=SITE_NAME, account=account)
    except paypal.PayPalError as exc:
        app.logger.warning("PayPal create order failed for booking %s: %s (%s)", booking_id, exc, exc.issue)
        with open_db() as connection:
            connection.execute("UPDATE bookings SET status = 'cancelled', updated_at = ? WHERE id = ?",
                               (iso(now_utc()), booking_id))
        flash(t["failed"], "error")
        return redirect(back)
    with open_db() as connection:
        connection.execute("UPDATE bookings SET paypal_order_id = ?, updated_at = ? WHERE id = ?",
                           (paypal_id, iso(now_utc()), booking_id))
    return redirect(approve_url)


def _booking_by_paypal_id(paypal_id: str):
    if not paypal_id or len(paypal_id) > 64 or not paypal_id.isalnum():
        abort(404)
    booking = db().execute("SELECT * FROM bookings WHERE paypal_order_id = ?", (paypal_id,)).fetchone()
    if booking is None:
        abort(404)
    return booking


def _client_token(booking) -> str:
    import client_page
    with open_db() as connection:
        return client_page.ensure_page(connection, booking["lead_id"])["token"]


def _booking_lang(booking) -> str:
    lead = db().execute("SELECT * FROM leads WHERE id = ?", (booking["lead_id"],)).fetchone()
    return _lang(lead)


@app.get("/paypal/booking/return")
def booking_paypal_return():
    booking = _booking_by_paypal_id(request.args.get("token", ""))
    t = T[_booking_lang(booking)]
    if booking["status"] in ("paid", "refunded"):
        return redirect(url_for("booking_page", token=booking["token"]))
    if booking["status"] != "pending":
        flash(t["cancelled_checkout"], "info")
        return redirect(url_for("booking_choose", token=_client_token(booking)))
    outcome = finalize(booking["id"])
    if outcome == "paid":
        return redirect(url_for("booking_page", token=booking["token"]))
    flash(t["declined"] if outcome == "declined" else t["unconfirmed"], "error")
    return redirect(url_for("booking_choose", token=_client_token(booking)))


@app.get("/paypal/booking/cancel")
def booking_paypal_cancel():
    booking = _booking_by_paypal_id(request.args.get("token", ""))
    if booking["status"] == "pending":
        with open_db() as connection:
            connection.execute("UPDATE bookings SET status = 'cancelled', updated_at = ? WHERE id = ?"
                               " AND status = 'pending'", (iso(now_utc()), booking["id"]))
    current = db().execute("SELECT status, token FROM bookings WHERE id = ?", (booking["id"],)).fetchone()
    if current["status"] in ("paid", "refunded"):  # paid in another tab
        return redirect(url_for("booking_page", token=current["token"]))
    flash(T[_booking_lang(booking)]["cancelled_checkout"], "info")
    return redirect(url_for("booking_choose", token=_client_token(booking)))


def finalize(booking_id: int) -> str:
    """Capture a pending booking with PayPal; "paid", "declined" or "unconfirmed" (try later)."""
    booking = db().execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    if booking is None or not booking["paypal_order_id"]:
        return "unconfirmed"
    if booking["status"] in ("paid", "refunded"):
        return "paid"
    if booking["status"] not in ("pending", "cancelled"):
        return "declined"
    import practitioner_paypal
    account = practitioner_paypal.account_for_key(booking["paypal_account"])
    if account is None:
        app.logger.error("Booking %s: PayPal account %s is no longer connected; can't capture",
                         booking_id, booking["paypal_account"])
        return "unconfirmed"
    try:  # network call: no database lock held
        result = paypal.capture_order(booking["paypal_order_id"], account=account)
    except paypal.PayPalError as exc:
        app.logger.warning("PayPal capture failed for booking %s: %s (%s)", booking_id, exc, exc.issue)
        if exc.issue in ("INSTRUMENT_DECLINED", "PAYER_ACTION_REQUIRED", "ORDER_NOT_APPROVED"):
            return "declined"
        return "unconfirmed"
    capture = paypal.completed_capture(result, reference=f"booking-{booking_id}", cents=booking["amount_cents"],
                                       currency=booking["currency"])
    if capture is None:
        app.logger.warning("PayPal order for booking %s not completed or mismatched (status %s)",
                           booking_id, result.get("status"))
        return "declined" if result.get("status") == "VOIDED" else "unconfirmed"
    mark_paid(booking_id, capture["capture_id"], capture["payer_email"])
    return "paid"


def mark_paid(booking_id: int, capture_id: str, payer_email: str) -> bool:
    """Record a confirmed payment once: the reading goes on the client's record (or, if the time was
    taken meanwhile, the practitioner is asked to agree a new one), the fee is recorded, everyone is told.

    A completed capture is money received, so it's recorded even if the checkout was cancelled or its
    hold ran out while the client was paying."""
    connection = db()
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    try:
        booking = connection.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        if booking is None or booking["status"] not in ("pending", "cancelled"):
            connection.rollback()
            return False
        practitioner = connection.execute("SELECT * FROM users WHERE id = ?", (booking["practitioner_id"],)).fetchone()
        s = settings(practitioner)
        start = dt.datetime.fromisoformat(booking["starts_at"])
        end = start + dt.timedelta(minutes=booking["duration_min"])
        busy = _busy(connection, booking["practitioner_id"], s, start - dt.timedelta(days=1),
                     end + dt.timedelta(days=1), exclude_booking=booking_id)
        conflict = _clashes(start, end, busy, 0)
        now = iso(now_utc())
        reading_id = None
        tz = ZoneInfo(booking["tz"])
        if not conflict:
            reading_id = connection.execute(
                "INSERT INTO readings (lead_id, practitioner_id, kind, mode, scheduled_at, duration_min, status,"
                " notes, created_at) VALUES (?, ?, ?, ?, ?, ?, 'scheduled', ?, ?)",
                (booking["lead_id"], booking["practitioner_id"], booking["kind"], booking["mode"],
                 start.astimezone(tz).strftime("%Y-%m-%dT%H:%M"), booking["duration_min"],
                 f"Booked and paid online ({price_text(booking['amount_cents'])}).", now)).lastrowid
            connection.execute("UPDATE leads SET stage = 'contacted', updated_at = ? WHERE id = ? AND stage = 'new'",
                               (now, booking["lead_id"]))
        local_key = start.astimezone(tz).strftime("%Y-%m-%dT%H:%M")
        ahead = start - now_utc()
        day_done = ahead < DAY_REMINDER + dt.timedelta(hours=2)  # no "tomorrow" email for a booking this close
        hour_done = ahead < HOUR_REMINDER + dt.timedelta(minutes=10)  # nor "in an hour" right after booking
        connection.execute(
            "UPDATE bookings SET status = 'paid', paypal_capture_id = ?, payer_email = ?, paid_at = ?, updated_at = ?,"
            " reading_id = ?, conflict = ?, held_until = NULL, reminded_day_for = ?, reminded_hour_for = ?"
            " WHERE id = ?",
            (capture_id or None, (payer_email or "")[:200], now, now, reading_id, 1 if conflict else 0,
             local_key if day_done else None, local_key if hour_done else None, booking_id))
        if booking["status"] == "cancelled":
            app.logger.warning("Booking %s was cancelled while PayPal captured it; recorded as paid", booking_id)
        booking = connection.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        _credit(connection, booking)
        _queue_client_email(connection, booking, "conflict" if conflict else "confirmed")
        recipients = _queue_staff_alert(connection, booking, conflict)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    import emails
    import notify
    emails.kick()
    lead = db().execute("SELECT full_name FROM leads WHERE id = ?", (booking["lead_id"],)).fetchone()
    state = appointment(booking)
    when = when_text(state["start"], booking["tz"], "en")
    notify.queue([r["id"] for r in recipients], {
        "title": ("Paid booking: time taken, contact client" if conflict else "New paid booking"),
        "body": f"{lead['full_name']} · {when}",
        "url": (f"/leads/{booking['lead_id']}#readings" if conflict
                else f"/settings/appointments/{booking['reading_id']}"), "tag": f"booking-{booking_id}"})
    return True


def _credit(connection, booking) -> None:
    """25% fee. Paid into the practitioner's PayPal: recorded as a fee she owes (monthly invoice).
    Paid to TAPNET (fallback): 75% credited to the practitioner, 25% to TAPNET."""
    reference = f"booking-{booking['id']}"
    note = f"Booking {booking['id']}"
    platform_cents, practitioner_cents = billing.split(booking["amount_cents"])
    if (booking["paypal_account"] or "").startswith("user:"):
        billing.post(connection, booking["practitioner_id"], "platform_fee", -platform_cents, f"{reference}:fee",
                     note=note)
        return
    platform = billing.platform_account(connection)
    billing.post(connection, booking["practitioner_id"], "sale_share", practitioner_cents,
                 f"{reference}:practitioner", note=note)
    if platform is not None:
        billing.post(connection, platform["id"], "platform_share", platform_cents, f"{reference}:platform",
                     source_user_id=booking["practitioner_id"], note=note)
    else:
        app.logger.error("Booking %s: no TAPNET platform account, platform share not credited", booking["id"])


# --------------------------------------------------------------------------
# Emails and alerts
# --------------------------------------------------------------------------
def _queue_client_email(connection, booking, mode: str) -> None:
    """mode: confirmed | conflict | day | hour | moved | cancelled"""
    import emails
    lead = connection.execute("SELECT * FROM leads WHERE id = ?", (booking["lead_id"],)).fetchone()
    if lead is None or not lead["email"]:
        return
    practitioner = connection.execute("SELECT * FROM users WHERE id = ?", (booking["practitioner_id"],)).fetchone()
    lang = _lang(lead)
    t = T[lang]
    who = _who(practitioner, lang)
    state = appointment(booking, connection)
    session = mid_sentence(t["kinds"].get(state["kind"], t["kinds"]["other"]))
    when = when_text(state["start"], booking["tz"], lang)
    date = date_text(state["start"].astimezone(ZoneInfo(booking["tz"])).date(), lang)
    show_calendar = mode in ("confirmed", "day", "moved")
    context = {"t": t, "lang": lang, "email_lang": lang, "mode": mode, "who": who, "when": when,
               "lead_line": t[f"lead_{mode}"].format(first_name=_first_name(lead), who=who, session=session),
               "minutes": t["minutes"].format(n=state["minutes"]), "how": t["modes"].get(state["mode"], ""),
               "details": settings(practitioner)["details"].strip() if mode != "cancelled" else "",
               "page_url": page_url(booking),
               "links": calendar_links(booking, practitioner, lang, state) if show_calendar else None,
               "receipt": {"price": price_text(booking["amount_cents"]), "id": booking["id"],
                           "via": t["paid_via"].format(who=who)} if mode in ("confirmed", "conflict") else None,
               "card": emails._card(practitioner) if practitioner is not None and practitioner["is_active"] else None}
    text, html = emails._render_pair("booking_client", **context)
    subject = t[f"subject_{mode}"].format(session=session, who=who, date=date)
    emails.enqueue(connection, f"booking_{mode}", lead["email"], subject, text, html, lead_id=lead["id"])


def _queue_staff_alert(connection, booking, conflict: bool) -> list:
    import emails
    import orders
    lead = connection.execute("SELECT * FROM leads WHERE id = ?", (booking["lead_id"],)).fetchone()
    recipients = orders._staff_recipients(connection, booking["practitioner_id"])
    state = appointment(booking, connection)
    for recipient in recipients:
        if not recipient["email_alerts"]:
            continue
        context = {"recipient_name": (recipient["display_name"] or "").split(" ")[0], "full_name": lead["full_name"],
                   "when": when_text(state["start"], booking["tz"], "en"), "minutes": state["minutes"],
                   "session_kind": mid_sentence(STAFF_KINDS.get(state["kind"], "Session")), "price": price_text(booking["amount_cents"]),
                   "conflict": conflict, "lead_url": f"{BASE_URL}/leads/{lead['id']}#readings",
                   "appointment_url": (f"{BASE_URL}/settings/appointments/{booking['reading_id']}"
                                       if booking["reading_id"] else None)}
        text, html = emails._render_pair("booking_alert", **context)
        subject = (f"Paid booking needs a new time: {lead['full_name']}" if conflict
                   else f"New booking: {lead['full_name']}, {when_text(state['start'], booking['tz'], 'en')}")
        emails.enqueue(connection, "booking_alert", recipient["email"], subject, text, html,
                       lead_id=lead["id"], user_id=recipient["id"])
    return list(recipients)


def after_reading_saved(reading_id: int, old_when: str, old_status: str) -> None:
    """Called after the practitioner saves a reading: tell the client if a booked time moved or was cancelled."""
    with open_db() as connection:
        booking = connection.execute("SELECT * FROM bookings WHERE reading_id = ? AND status = 'paid'",
                                     (reading_id,)).fetchone()
        reading = connection.execute("SELECT * FROM readings WHERE id = ?", (reading_id,)).fetchone()
        if booking is None or reading is None:
            return
        mode = None
        if reading["status"] == "cancelled" and old_status == "scheduled":
            mode = "cancelled"
        elif reading["status"] == "scheduled" and reading["scheduled_at"] != old_when:
            mode = "moved"
        if mode is None:
            return
        connection.execute("UPDATE bookings SET sequence = sequence + 1, updated_at = ? WHERE id = ?",
                           (iso(now_utc()), booking["id"]))
        booking = connection.execute("SELECT * FROM bookings WHERE id = ?", (booking["id"],)).fetchone()
        _queue_client_email(connection, booking, mode)
    import emails
    emails.kick()


def send_reminders(now: dt.datetime | None = None) -> dict:
    """Client: email the day before and about an hour before. Staff: a push about an hour before.
    Each reminder is tied to the appointment's time, so a moved appointment is reminded again."""
    now = now or now_utc()
    result = {"day": 0, "hour": 0}
    pushes = []
    with open_db() as connection:
        rows = connection.execute(
            "SELECT bookings.* FROM bookings JOIN readings ON readings.id = bookings.reading_id"
            " WHERE bookings.status = 'paid' AND readings.status = 'scheduled'").fetchall()
        for booking in rows:
            state = appointment(booking, connection)
            left = state["start"] - now
            if left <= dt.timedelta(0):
                continue
            key = state["local_key"]
            if left <= HOUR_REMINDER and booking["reminded_hour_for"] != key:
                connection.execute("UPDATE bookings SET reminded_hour_for = ?, reminded_day_for = ? WHERE id = ?",
                                   (key, key, booking["id"]))
                _queue_client_email(connection, booking, "hour")
                lead = connection.execute("SELECT full_name FROM leads WHERE id = ?", (booking["lead_id"],)).fetchone()
                import orders
                staff = orders._staff_recipients(connection, booking["practitioner_id"])
                pushes.append(([r["id"] for r in staff], {
                    "title": "Session in about an hour",
                    "body": f"{lead['full_name']} · {when_text(state['start'], booking['tz'], 'en')}",
                    "url": f"/settings/appointments/{booking['reading_id']}",
                    "tag": f"booking-{booking['id']}-soon"}))
                result["hour"] += 1
            elif HOUR_REMINDER < left <= DAY_REMINDER and booking["reminded_day_for"] != key:
                connection.execute("UPDATE bookings SET reminded_day_for = ? WHERE id = ?", (key, booking["id"]))
                _queue_client_email(connection, booking, "day")
                result["day"] += 1
    import notify
    for user_ids, message in pushes:
        notify.queue(user_ids, message, start_sending=False)
    return result


def capture_open() -> dict:
    """Job: capture approved checkouts whose buyer didn't come back; give up on old ones."""
    cutoff_new = iso(now_utc() - dt.timedelta(minutes=2))
    cutoff_old = iso(now_utc() - dt.timedelta(days=OPEN_PAYMENT_DAYS))
    with open_db() as connection:
        connection.execute("UPDATE bookings SET status = 'abandoned', updated_at = ? WHERE status IN"
                           " ('pending', 'cancelled') AND paypal_capture_id IS NULL AND created_at < ?",
                           (iso(now_utc()), cutoff_old))
        rows = connection.execute("SELECT id, paypal_order_id, paypal_account FROM bookings WHERE status IN"
                                  " ('pending', 'cancelled') AND paypal_order_id IS NOT NULL AND created_at < ?",
                                  (cutoff_new,)).fetchall()
    result = {"checked": 0, "paid": 0}
    import practitioner_paypal
    for row in rows:
        account = practitioner_paypal.account_for_key(row["paypal_account"])
        if account is None:
            continue
        result["checked"] += 1
        try:
            status = paypal.get_order(row["paypal_order_id"], account=account).get("status")
        except paypal.PayPalError:
            continue
        if status in ("APPROVED", "COMPLETED") and finalize(row["id"]) == "paid":
            result["paid"] += 1
    return result


def run_job() -> dict:
    import calendar_sync
    with app.app_context():
        result = calendar_sync.refresh_due()
        result.update(capture_open())
        result.update(send_reminders())
    return result


@app.cli.command("bookings")
def bookings_command():
    """Capture paid bookings and send appointment reminders."""
    click.echo(run_job())


# --------------------------------------------------------------------------
# Client: the appointment page and calendar file
# --------------------------------------------------------------------------
def _public_booking(token: str):
    if not token or len(token) > 64:
        abort(404)
    booking = db().execute("SELECT * FROM bookings WHERE token = ? AND status IN ('paid', 'refunded')",
                           (token,)).fetchone()
    if booking is None:
        abort(404)
    lead = db().execute("SELECT * FROM leads WHERE id = ?", (booking["lead_id"],)).fetchone()
    if lead is None:
        abort(404)
    practitioner = db().execute("SELECT * FROM users WHERE id = ?", (booking["practitioner_id"],)).fetchone()
    return booking, lead, practitioner


@app.get("/b/<token>")
def booking_page(token):
    booking, lead, practitioner = _public_booking(token)
    lang = _lang(lead)
    t = T[lang]
    state = appointment(booking)
    who = _who(practitioner, lang)
    from practitioner_profile import contact_card
    import client_page
    page = client_page.page_for_lead(lead["id"])
    return render_template(
        "booking_page.html", booking=booking, lead=lead, t=t, lang=lang, who=who, state=state,
        when=when_text(state["start"], booking["tz"], lang),
        local_start=state["start"].astimezone(ZoneInfo(booking["tz"])).isoformat(), session=t["kinds"].get(state["kind"], t["kinds"]["other"]),
        how=t["modes"].get(state["mode"], ""), minutes=t["minutes"].format(n=state["minutes"]),
        details=settings(practitioner)["details"].strip() if state["status"] in ("confirmed", "conflict") else "",
        links=calendar_links(booking, practitioner, lang, state) if state["status"] == "confirmed" else None,
        price=price_text(booking["amount_cents"]), client_token=page["token"] if page else None,
        card=contact_card(practitioner) if practitioner is not None and practitioner["is_active"] else None)


@app.get("/b/<token>/appointment.ics")
def booking_ics(token):
    booking, lead, practitioner = _public_booking(token)
    return Response(ics(booking, practitioner, _lang(lead)), mimetype="text/calendar",
                    headers={"Content-Disposition": 'attachment; filename="appointment.ics"'})


@app.after_request
def _booking_headers(response):
    if request.endpoint in ("booking_choose", "booking_checkout", "booking_page", "booking_ics"):
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


# --------------------------------------------------------------------------
# Practitioner: settings; admin: refunds
# --------------------------------------------------------------------------
def _owner():
    user = current_user()
    if user["role"] != "practitioner" or user["team_of"]:
        abort(403)  # the practitioner sets her own hours and price
    return db().execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()


@app.route("/settings/bookings", methods=["GET", "POST"])
@login_required
def booking_settings_page():
    import orders
    import readings
    user = _owner()
    s = settings(user)
    errors = []
    if request.method == "POST":
        form = request.form
        new = dict(s)
        new["enabled"] = form.get("enabled") == "yes"
        try:
            price = round(float((form.get("price") or "").replace("$", "").replace(",", "").strip()) * 100)
        except ValueError:
            price = -1
        if not 500 <= price <= 500000:
            errors.append("Enter a price between CAD $5 and CAD $5,000.")
        else:
            new["price_cents"] = price
        for key, low, high, label in (("duration_min", 15, 480, "Session length"), ("buffer_min", 0, 120, "Break"),
                                      ("notice_hours", 0, 336, "Minimum notice"), ("window_days", 1, 180, "Book ahead")):
            value = form.get(key, type=int)
            if value is None or not low <= value <= high:
                errors.append(f"{label}: enter a number from {low} to {high}.")
            else:
                new[key] = value
        if form.get("kind") in readings.KINDS:
            new["kind"] = form["kind"]
        if form.get("mode") in readings.MODES:
            new["mode"] = form["mode"]
        new["details"] = (form.get("details") or "").replace("\r\n", "\n").strip()[:1000]
        tz = form.get("tz", "")
        try:
            ZoneInfo(tz)
            new["tz"] = tz
        except (ZoneInfoNotFoundError, ValueError):
            errors.append("Choose your time zone.")
        hours = {}
        for day in range(7):
            text = (form.get(f"hours_{day}") or "").strip()[:200]
            try:
                parse_windows(text)
            except ValueError as exc:
                errors.append(f"{WEEKDAYS[day]}: \"{exc}\" isn't a time range like 10:00-13:00.")
            hours[str(day)] = text
        new["hours"] = hours
        days_off = (form.get("days_off") or "").strip()[:2000]
        try:
            parse_days_off(days_off)
            new["days_off"] = days_off
        except ValueError:
            errors.append("Days off: write dates like 2026-12-24, separated by commas.")
        if new["enabled"] and not any(new["hours"].values()):
            errors.append("Add your hours for at least one day before turning bookings on.")
        if not errors:
            with open_db() as connection:
                connection.execute("UPDATE users SET booking = ? WHERE id = ?", (json.dumps(new), user["id"]))
            flash("Booking settings saved." + (" Clients can now book from their client page." if new["enabled"] else ""),
                  "info")
            return redirect(url_for("booking_settings_page"))
        s = new
    preview = []
    if s["enabled"] and not errors:
        preview = group_by_day(free_slots(db(), user), zone(s), "en")[:5]
    lead_stub = {"practitioner_id": user["id"]}
    import calendar_sync
    return render_template("booking_settings.html", feed=calendar_sync.status(user["id"]), s=s, errors=errors, weekdays=WEEKDAYS, timezones=TIMEZONES,
                           kinds=readings.KINDS, modes=readings.MODES, preview=preview,
                           price=f"{s['price_cents'] / 100:.2f}", zone=zone_name(s["tz"]),
                           can_sell=orders.seller_account(lead_stub) is not None)


@app.post("/admin/bookings/<int:booking_id>/refunded")
@role_required("admin")
def admin_booking_refunded(booking_id):
    """After refunding in PayPal: mark the booking refunded, reverse its credits, cancel the reading."""
    with open_db() as connection:
        booking = connection.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        if booking is None or booking["status"] != "paid":
            abort(404)
        connection.execute("UPDATE bookings SET status = 'refunded', updated_at = ? WHERE id = ?",
                           (iso(now_utc()), booking_id))
        if booking["reading_id"]:
            connection.execute("UPDATE readings SET status = 'cancelled', updated_at = ? WHERE id = ?"
                               " AND status = 'scheduled'", (iso(now_utc()), booking["reading_id"]))
        reference = f"booking-{booking_id}"
        for entry in connection.execute("SELECT * FROM ledger WHERE reference IN (?, ?, ?)",
                                         (f"{reference}:practitioner", f"{reference}:platform", f"{reference}:fee")).fetchall():
            billing.post(connection, entry["user_id"], "adjustment", -entry["amount_cents"],
                         f"{entry['reference']}:refund", source_user_id=entry["source_user_id"],
                         note=f"Refund of booking {booking_id}", created_by=current_user()["id"])
    flash(f"Booking {booking_id} marked refunded, its credits reversed and the reading cancelled.", "info")
    if booking["reading_id"]:
        return redirect(url_for("edit_reading", lead_id=booking["lead_id"], reading_id=booking["reading_id"]))
    return redirect(url_for("lead_detail", lead_id=booking["lead_id"]) + "#readings")
