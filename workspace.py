"""Signed-in workspace: dashboard, leads list, lead detail with stage and notes, CSV export.

Scoping: a practitioner only ever sees leads where practitioner_id is their own id.
Admins see every lead, including ones that arrived without a practitioner link.
Every query that reads or changes a lead goes through lead_scope().
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json

from flask import Response, abort, flash, redirect, render_template, request, url_for

import numerology
from app import BASE_URL, app, current_user, db, iso, login_required, now_utc

STAGES = {
    "new": "New",
    "contacted": "Contacted",
    "client": "Client",
    "archived": "Archived",
}
PAGE_SIZE = 50
MAX_NOTES = 5000


def lead_scope(user) -> tuple[str, list]:
    """SQL condition and parameters limiting leads to what this user may see."""
    if user["role"] == "admin":
        return "1 = 1", []
    return "leads.practitioner_id = ?", [user["id"]]


def scoped_lead(lead_id: int):
    user = current_user()
    condition, params = lead_scope(user)
    lead = db().execute(
        f"SELECT leads.*, users.display_name AS practitioner_name FROM leads"
        f" LEFT JOIN users ON users.id = leads.practitioner_id"
        f" WHERE leads.id = ? AND {condition}",
        [lead_id, *params],
    ).fetchone()
    if lead is None:
        abort(404)  # not found and not yours look the same
    return lead


def lead_numbers(lead) -> list[dict]:
    numbers = json.loads(lead["numbers"])
    items = []
    for key in numerology.NUMBER_ORDER:
        title, text = numerology.meaning(numbers[key])
        items.append({"label": numerology.NUMBER_LABELS[key], "number": numbers[key],
                      "title": title, "text": text, "purpose": numerology.NUMBER_PURPOSE[key]})
    return items


def birth_place(lead) -> str:
    return ", ".join(part for part in (lead["birth_city"], lead["birth_region"], lead["birth_country"]) if part)


def life_path(lead) -> int:
    return json.loads(lead["numbers"])["life_path"]


app.jinja_env.filters["birth_place"] = birth_place
app.jinja_env.filters["life_path"] = life_path


@app.template_filter("friendly_time")
def friendly_time(value: str | None) -> str:
    if not value:
        return ""
    moment = dt.datetime.fromisoformat(value)
    delta = now_utc() - moment
    if delta < dt.timedelta(minutes=1):
        return "just now"
    if delta < dt.timedelta(hours=1):
        return f"{int(delta.total_seconds() // 60)} min ago"
    if delta < dt.timedelta(days=1):
        return f"{int(delta.total_seconds() // 3600)} h ago"
    if delta < dt.timedelta(days=7):
        return f"{delta.days} d ago"
    return moment.strftime("%b %-d, %Y")


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
@app.get("/dashboard")
@login_required
def dashboard():
    user = current_user()
    condition, params = lead_scope(user)
    connection = db()
    week_ago = iso(now_utc() - dt.timedelta(days=7))
    stats = connection.execute(
        f"""SELECT COUNT(*) AS total,
                   SUM(created_at >= ?) AS this_week,
                   SUM(status = 'captured') AS unlocked,
                   SUM(status = 'captured' AND viewed_at IS NULL) AS unread,
                   SUM(stage = 'client') AS clients
            FROM leads WHERE {condition}""",
        [week_ago, *params],
    ).fetchone()
    recent = connection.execute(
        f"SELECT leads.*, users.display_name AS practitioner_name FROM leads"
        f" LEFT JOIN users ON users.id = leads.practitioner_id"
        f" WHERE {condition} ORDER BY leads.created_at DESC LIMIT 6",
        params,
    ).fetchall()
    row = connection.execute("SELECT slug FROM users WHERE id = ?", (user["id"],)).fetchone()
    slug = row["slug"] if row else None
    total = stats["total"] or 0
    unlock_rate = round(100 * (stats["unlocked"] or 0) / total) if total else 0
    return render_template(
        "dashboard.html", stats=stats, total=total, unlock_rate=unlock_rate, recent=recent,
        share_url=f"{BASE_URL}/{slug}" if slug else None, stages=STAGES,
    )


# --------------------------------------------------------------------------
# Leads list, detail, update, export
# --------------------------------------------------------------------------
def filtered_leads(user, args, limit: int | None):
    condition, params = lead_scope(user)
    clauses, values = [condition], list(params)
    q = (args.get("q") or "").strip()[:100]
    if q:
        like = f"%{q}%"
        clauses.append("(leads.full_name LIKE ? OR leads.email LIKE ? OR leads.phone LIKE ?"
                       " OR leads.birth_city LIKE ? OR leads.birth_country LIKE ?)")
        values += [like] * 5
    stage = args.get("stage") or ""
    if stage in STAGES:
        clauses.append("leads.stage = ?")
        values.append(stage)
    elif stage != "all":
        clauses.append("leads.stage != 'archived'")
    unlocked = args.get("unlocked") or ""
    if unlocked == "yes":
        clauses.append("leads.status = 'captured'")
    elif unlocked == "no":
        clauses.append("leads.status = 'started'")
    sql = (f"SELECT leads.*, users.display_name AS practitioner_name FROM leads"
           f" LEFT JOIN users ON users.id = leads.practitioner_id"
           f" WHERE {' AND '.join(clauses)} ORDER BY leads.created_at DESC")
    if limit:
        sql += f" LIMIT {int(limit) + 1}"
    return db().execute(sql, values).fetchall()


@app.get("/leads")
@login_required
def leads_list():
    user = current_user()
    rows = filtered_leads(user, request.args, PAGE_SIZE)
    more = len(rows) > PAGE_SIZE
    return render_template("leads.html", leads=rows[:PAGE_SIZE], more=more, stages=STAGES,
                           q=request.args.get("q", ""), stage=request.args.get("stage", ""),
                           unlocked=request.args.get("unlocked", ""))


@app.get("/leads/<int:lead_id>")
@login_required
def lead_detail(lead_id):
    lead = scoped_lead(lead_id)
    user = current_user()
    if lead["viewed_at"] is None and (user["role"] != "admin" or lead["practitioner_id"] is None):
        connection = db()
        connection.execute("UPDATE leads SET viewed_at = ? WHERE id = ?", (iso(now_utc()), lead_id))
        connection.commit()
    return render_template("lead.html", lead=lead, numbers=lead_numbers(lead), stages=STAGES)


@app.post("/leads/<int:lead_id>")
@login_required
def update_lead(lead_id):
    lead = scoped_lead(lead_id)
    stage = request.form.get("stage", lead["stage"])
    if stage not in STAGES:
        abort(400, description="Unknown stage.")
    notes = (request.form.get("notes") if "notes" in request.form else lead["notes"]) or ""
    notes = notes.replace("\r\n", "\n")[:MAX_NOTES]
    connection = db()
    connection.execute("UPDATE leads SET stage = ?, notes = ?, updated_at = ? WHERE id = ?",
                       (stage, notes, iso(now_utc()), lead["id"]))
    connection.commit()
    flash("Saved.", "info")
    return redirect(url_for("lead_detail", lead_id=lead["id"]))


def _csv_safe(value) -> str:
    """Stop spreadsheet apps treating a visitor's text as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@app.get("/leads/export.csv")
@login_required
def export_leads():
    user = current_user()
    rows = filtered_leads(user, request.args, None)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    header = ["Date", "Name", "Email", "Phone", "Birth date", "Birth place", "Life Path", "Expression",
              "Soul Urge", "Personality", "Birthday", "Unlocked", "Stage", "Marketing opt-in", "Notes"]
    if user["role"] == "admin":
        header.append("Practitioner")
    writer.writerow(header)
    for lead in rows:
        numbers = json.loads(lead["numbers"])
        line = [lead["created_at"][:10], lead["full_name"], lead["email"] or "", lead["phone"],
                lead["birth_date"], birth_place(lead), *[numbers[k] for k in numerology.NUMBER_ORDER],
                "yes" if lead["status"] == "captured" else "no", STAGES.get(lead["stage"], lead["stage"]),
                "yes" if lead["marketing_opt_in"] else "no", lead["notes"]]
        if user["role"] == "admin":
            line.append(lead["practitioner_name"] or "(no practitioner)")
        writer.writerow([_csv_safe(value) for value in line])
    stamp = now_utc().strftime("%Y-%m-%d")
    return Response(buffer.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="numerlife-leads-{stamp}.csv"',
                             "Cache-Control": "no-store"})
