"""The practitioner's appointments: what's coming up, and "Add to my calendar" for each one.

- /settings/appointments lists upcoming scheduled readings (paid online bookings and readings
  scheduled by hand) for the practitioner and her team, by day, in her time zone.
- /settings/appointments/<reading id> is one appointment: client, time, how to join, and buttons to
  add it to her own calendar: Apple/phone (.ics), Google, Outlook. The push notifications for a
  new booking and "in about an hour" open this page, so one tap adds it to her phone's calendar.
- numer.life doesn't write to her calendar by itself (read-only link, see calendar_sync), so this
  is how bookings reach it. Re-adding after a change updates the same event (same UID).
"""
from __future__ import annotations

import datetime as dt
from urllib.parse import urlencode

from flask import Response, abort, render_template

import bookings
import readings
from app import BASE_URL, app, current_user, db, login_required, now_utc, workspace_owner_id

DAYS_AHEAD = 120


def _practitioner(practitioner_id):
    if not practitioner_id:
        return None
    return db().execute("SELECT * FROM users WHERE id = ?", (practitioner_id,)).fetchone()


def _item(row) -> dict:
    """One appointment with its times (UTC and the practitioner's local time)."""
    practitioner = _practitioner(row["practitioner_id"])
    s = bookings.settings(practitioner)
    tz = bookings.zone(s)
    start = dt.datetime.fromisoformat(row["scheduled_at"]).replace(tzinfo=tz)
    end = start + dt.timedelta(minutes=row["duration_min"])
    booking = bookings.booking_for_reading(row["id"])
    return {"reading": row, "start": start.astimezone(dt.timezone.utc), "end": end.astimezone(dt.timezone.utc),
            "local": start, "tz": s["tz"], "zone": bookings.zone_name(s["tz"]), "booking": booking,
            "paid": booking is not None and booking["status"] == "paid",
            "details": s["details"].strip(), "practitioner": practitioner,
            "day": bookings.date_text(start.date(), "en"), "time": bookings.time_text(start, "en"),
            "end_time": bookings.time_text(end, "en")}


def upcoming(user, days: int = DAYS_AHEAD) -> list[dict]:
    yesterday = (now_utc() - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    until = (now_utc() + dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M")
    query = ("SELECT readings.*, leads.full_name, leads.email, leads.phone FROM readings"
             " JOIN leads ON leads.id = readings.lead_id WHERE readings.status = 'scheduled'"
             " AND readings.scheduled_at BETWEEN ? AND ?")
    params: list = [yesterday, until]
    if user["role"] != "admin":
        query += " AND leads.practitioner_id = ?"
        params.append(workspace_owner_id(user))
    now = now_utc()
    items = [_item(r) for r in db().execute(query + " ORDER BY readings.scheduled_at", params).fetchall()]
    return [i for i in items if i["end"] > now]


def _scoped(reading_id: int):
    user = current_user()
    query = ("SELECT readings.*, leads.full_name, leads.email, leads.phone FROM readings"
             " JOIN leads ON leads.id = readings.lead_id WHERE readings.id = ?")
    params: list = [reading_id]
    if user["role"] != "admin":
        query += " AND leads.practitioner_id = ?"
        params.append(workspace_owner_id(user))
    row = db().execute(query, params).fetchone()
    if row is None:
        abort(404)   # not found and not yours look the same
    return row


def event_text(item: dict) -> dict:
    reading = item["reading"]
    kind = readings.KINDS.get(reading["kind"], "Session")
    lines = [f"Client: {reading['full_name']}"]
    if reading["email"]:
        lines.append(f"Email: {reading['email']}")
    if reading["phone"]:
        lines.append(f"Phone: {reading['phone']}")
    lines.append(f"How: {readings.MODES.get(reading['mode'], '')}")
    if item["details"]:
        lines += ["", item["details"]]
    lines += ["", f"numer.life: {BASE_URL}/settings/appointments/{reading['id']}"]
    location = item["details"].splitlines()[0][:200] if item["details"] else readings.MODES.get(reading["mode"], "")
    return {"title": f"{kind} · {reading['full_name']}", "description": "\n".join(lines), "location": location}


def calendar_links(item: dict) -> dict:
    text = event_text(item)
    stamp = bookings._stamp
    google = "https://calendar.google.com/calendar/render?" + urlencode({
        "action": "TEMPLATE", "text": text["title"], "dates": f"{stamp(item['start'])}/{stamp(item['end'])}",
        "details": text["description"], "location": text["location"]})
    outlook = "https://outlook.live.com/calendar/0/deeplink/compose?" + urlencode({
        "path": "/calendar/action/compose", "rru": "addevent", "subject": text["title"],
        "startdt": item["start"].strftime("%Y-%m-%dT%H:%M:%SZ"), "enddt": item["end"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "body": text["description"], "location": text["location"]})
    return {"google": google, "outlook": outlook}


def ics(item: dict) -> str:
    reading = item["reading"]
    text = event_text(item)
    changed = dt.datetime.fromisoformat(reading["updated_at"] or reading["created_at"])
    sequence = int(changed.timestamp()) // 60   # grows each time the reading is saved: re-adding updates it
    esc, fold, stamp = bookings._ics_text, bookings._fold, bookings._stamp
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//numer.life//Appointments//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "BEGIN:VEVENT", f"UID:reading-{reading['id']}-practitioner@numer.life",
             f"SEQUENCE:{sequence}", f"DTSTAMP:{stamp(now_utc())}", f"DTSTART:{stamp(item['start'])}",
             f"DTEND:{stamp(item['end'])}", f"SUMMARY:{esc(text['title'])}", f"DESCRIPTION:{esc(text['description'])}",
             f"LOCATION:{esc(text['location'])}", f"URL:{BASE_URL}/settings/appointments/{reading['id']}",
             "STATUS:CONFIRMED", "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{esc(text['title'])}",
             "TRIGGER:-PT30M", "END:VALARM", "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(fold(line) for line in lines) + "\r\n"


def _feed_owner(user):
    """Whose calendar link and booking settings apply on these pages."""
    owner_id = workspace_owner_id(user) if user["role"] != "admin" else user["id"]
    return _practitioner(owner_id)


@app.get("/settings/appointments")
@login_required
def appointments_list():
    user = current_user()
    items = upcoming(user)
    days: dict = {}
    for item in items:
        days.setdefault(item["day"], []).append(item)
    owner = _feed_owner(user)
    import calendar_sync
    return render_template("appointments.html", days=days, owner=owner,
                           feed=calendar_sync.status(owner["id"]) if owner is not None else None,
                           s=bookings.settings(owner), can_edit=user["role"] == "practitioner" and not user["team_of"])


@app.get("/settings/appointments/<int:reading_id>")
@login_required
def appointment_detail(reading_id):
    item = _item(_scoped(reading_id))
    return render_template("appointment.html", item=item, links=calendar_links(item),
                           kind=readings.KINDS.get(item["reading"]["kind"], "Session"),
                           mode=readings.MODES.get(item["reading"]["mode"], ""),
                           past=item["end"] < now_utc())


@app.get("/settings/appointments/<int:reading_id>.ics")
@login_required
def appointment_ics(reading_id):
    item = _item(_scoped(reading_id))
    return Response(ics(item), mimetype="text/calendar",
                    headers={"Content-Disposition": f'attachment; filename="appointment-{reading_id}.ics"',
                             "Cache-Control": "no-store"})

