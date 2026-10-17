"""Client record: readings.

Each lead (client) can have readings: scheduled, then completed (or cancelled / no-show), with the
practitioner's private notes and, for a Magialogía Numérica reading, the reading's parameters.
Everything about a client lives on their lead page, and other features read from it:
- the Magialogía calendar can only be started after a completed Magialogía reading, and shows
  that reading's parameters;
- the dashboard lists upcoming readings.
"""
from __future__ import annotations

import datetime as dt
import json

from flask import abort, flash, redirect, render_template, request, url_for

import calendar_engine as engine
from app import app, current_user, db, iso, login_required, now_utc, open_db, workspace_owner_id

KINDS = {"numerology": "Numerology reading", "magialogia": "Magialogía Numérica reading", "other": "Other session"}
MODES = {"online": "Online (video)", "phone": "Phone", "in_person": "In person"}
STATUSES = {"scheduled": "Scheduled", "completed": "Completed", "cancelled": "Cancelled", "no_show": "No-show"}
PARAM_FIELDS = ("an", "ac", "ad", "n", "strong", "weak", "key_dates")
MAX_NOTES = 8000


def _scoped_lead(lead_id: int):
    import workspace
    return workspace.scoped_lead(lead_id)


def _parse_when(date_value: str, time_value: str) -> str | None:
    try:
        when = dt.datetime.fromisoformat(f"{date_value}T{time_value or '00:00'}")
    except ValueError:
        return None
    return when.strftime("%Y-%m-%dT%H:%M")


def readings_for_lead(lead_id: int):
    return db().execute("SELECT * FROM readings WHERE lead_id = ? ORDER BY scheduled_at DESC, id DESC",
                        (lead_id,)).fetchall()


def completed_magialogia(lead_id: int):
    """The latest completed Magialogía reading for this client, if any."""
    return db().execute("SELECT * FROM readings WHERE lead_id = ? AND kind = 'magialogia' AND status = 'completed'"
                        " ORDER BY completed_at DESC, id DESC LIMIT 1", (lead_id,)).fetchone()


def reading_params(reading) -> dict:
    return json.loads(reading["params"] or "{}") if reading else {}


def upcoming_readings(user, days: int = 14, limit: int = 8):
    today = engine.local_today()
    start = today.strftime("%Y-%m-%dT00:00")
    end = (today + dt.timedelta(days=days)).strftime("%Y-%m-%dT23:59")
    query = ("SELECT readings.*, leads.full_name FROM readings JOIN leads ON leads.id = readings.lead_id"
             " WHERE readings.status = 'scheduled' AND readings.scheduled_at BETWEEN ? AND ?")
    params: list = [start, end]
    if user["role"] != "admin":
        query += " AND leads.practitioner_id = ?"
        params.append(workspace_owner_id(user))
    return db().execute(query + " ORDER BY readings.scheduled_at LIMIT ?", [*params, limit]).fetchall()


@app.template_filter("reading_when")
def reading_when(value: str | None) -> str:
    if not value:
        return ""
    moment = dt.datetime.fromisoformat(value)
    return moment.strftime("%a %b %-d, %Y · %-I:%M %p").replace("AM", "am").replace("PM", "pm")


app.jinja_env.globals.update(readings_for_lead=readings_for_lead, reading_kinds=KINDS, reading_modes=MODES,
                             reading_statuses=STATUSES, upcoming_readings=upcoming_readings)


@app.post("/leads/<int:lead_id>/readings")
@login_required
def schedule_reading(lead_id):
    lead = _scoped_lead(lead_id)
    kind = request.form.get("kind", "")
    mode = request.form.get("mode", "online")
    when = _parse_when(request.form.get("date", ""), request.form.get("time", ""))
    duration = request.form.get("duration", type=int) or 60
    if kind not in KINDS or mode not in MODES or when is None or not 15 <= duration <= 480:
        flash("Choose a reading type, a date and a time.", "error")
        return redirect(url_for("lead_detail", lead_id=lead_id) + "#readings")
    status = "completed" if request.form.get("already_done") == "yes" else "scheduled"
    with open_db() as connection:
        cursor = connection.execute(
            "INSERT INTO readings (lead_id, practitioner_id, kind, mode, scheduled_at, duration_min, status, created_by,"
            " created_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (lead_id, lead["practitioner_id"], kind, mode, when, duration, status, current_user()["id"],
             iso(now_utc()), iso(now_utc()) if status == "completed" else None))
        reading_id = cursor.lastrowid
        if lead["stage"] == "new":  # a booked reading means they've been contacted
            connection.execute("UPDATE leads SET stage = 'contacted', updated_at = ? WHERE id = ?",
                               (iso(now_utc()), lead_id))
    flash("Reading logged." if status == "completed" else "Reading scheduled.", "info")
    return redirect(url_for("edit_reading", lead_id=lead_id, reading_id=reading_id))


def _reading(lead_id: int, reading_id: int):
    reading = db().execute("SELECT * FROM readings WHERE id = ? AND lead_id = ?", (reading_id, lead_id)).fetchone()
    if reading is None:
        abort(404)
    return reading


@app.get("/leads/<int:lead_id>/readings/<int:reading_id>")
@login_required
def edit_reading(lead_id, reading_id):
    lead = _scoped_lead(lead_id)
    reading = _reading(lead_id, reading_id)
    import calendars
    return render_template("reading.html", lead=lead, reading=reading, params=reading_params(reading),
                           calendar=calendars.calendar_for_lead(lead_id))


@app.post("/leads/<int:lead_id>/readings/<int:reading_id>")
@login_required
def save_reading(lead_id, reading_id):
    _scoped_lead(lead_id)
    reading = _reading(lead_id, reading_id)
    status = request.form.get("status", reading["status"])
    mode = request.form.get("mode", reading["mode"])
    kind = request.form.get("kind", reading["kind"])
    when = _parse_when(request.form.get("date", ""), request.form.get("time", "")) or reading["scheduled_at"]
    duration = request.form.get("duration", type=int) or reading["duration_min"]
    if status not in STATUSES or mode not in MODES or kind not in KINDS or not 15 <= duration <= 480:
        abort(400, description="Invalid reading details.")
    notes = (request.form.get("notes") or "").replace("\r\n", "\n")[:MAX_NOTES]
    params = {field: (request.form.get(field) or "").replace("\r\n", "\n").strip()[:500] for field in PARAM_FIELDS}
    completed_at = reading["completed_at"]
    if status == "completed" and not completed_at:
        completed_at = iso(now_utc())
    elif status != "completed":
        completed_at = None
    with open_db() as connection:
        connection.execute(
            "UPDATE readings SET kind = ?, mode = ?, scheduled_at = ?, duration_min = ?, status = ?, notes = ?,"
            " params = ?, completed_at = ?, updated_at = ? WHERE id = ?",
            (kind, mode, when, duration, status, notes, json.dumps(params), completed_at, iso(now_utc()), reading_id))
    flash("Reading saved.", "info")
    return redirect(url_for("edit_reading", lead_id=lead_id, reading_id=reading_id))
