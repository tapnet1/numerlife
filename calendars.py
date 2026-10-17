"""Personal calendars for clients.

Two kinds:
- numerology: the client's personal day numbers with guidance; every month is available.
- magialogia: only for clients who have had the practitioner's Magialogía Numérica reading.
  The practitioner adds the client's reading parameters and day notes and publishes each
  month; the client sees only published months.

The client opens their calendar through a private link (/c/<token>), and can download any
visible month as a PDF. Numbers are always computed (calendar_engine); words come from the
practitioner's guidance (calendar_content) or the defaults.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import secrets
from pathlib import Path

from flask import Response, abort, flash, redirect, render_template, request, url_for

import calendar_content as content
import calendar_engine as engine
from app import BASE_URL, app, current_user, db, iso, login_required, now_utc, open_db, workspace_owner_id
from practitioner_profile import contact_card

KINDS = {"numerology": "Numerology calendar", "magialogia": "Magialogía Numérica calendar"}
PARAM_FIELDS = ("an", "ac", "ad", "n", "strong", "weak", "notes")
PARAM_LIMITS = {"notes": 2000}
MAX_NOTE = 600
MAX_INTRO = 2000


# --------------------------------------------------------------------------
# Data helpers
# --------------------------------------------------------------------------
def calendar_for_lead(lead_id: int):
    return db().execute("SELECT * FROM calendars WHERE lead_id = ?", (lead_id,)).fetchone()


def practitioner_overrides(practitioner_id: int | None, lang: str) -> dict:
    if not practitioner_id:
        return {}
    rows = db().execute("SELECT * FROM calendar_content WHERE practitioner_id = ? AND lang = ?",
                        (practitioner_id, lang)).fetchall()
    return {row["number"]: {field: row[field] for field in content.FIELDS} for row in rows}


def month_view(cal, lead, year: int, month: int) -> dict:
    """Everything needed to show one month: numbers, guidance, notes, moon, holidays."""
    lang = cal["lang"] if cal["lang"] in content.LANGUAGES else "en"
    birth = dt.date.fromisoformat(lead["birth_date"])
    built = engine.build_month(birth, year, month, lang=lang)
    owner_id = lead["practitioner_id"]  # the lead's current practitioner, even after reassignment
    overrides = practitioner_overrides(owner_id, lang)
    connection = db()
    notes = {row["day"]: row["text"] for row in connection.execute(
        "SELECT day, text FROM calendar_notes WHERE calendar_id = ? AND day LIKE ?", (cal["id"], f"{built.key}-%"))}
    month_row = connection.execute("SELECT * FROM calendar_months WHERE calendar_id = ? AND month = ?",
                                   (cal["id"], built.key)).fetchone()
    days = []
    for day in built.days:
        days.append({"date": day.date, "number": day.personal_day, "universal": day.universal_day,
                     "moon": day.moon, "holiday": day.holiday, "note": notes.get(day.date.isoformat(), ""),
                     "guide": content.guidance(day.personal_day, lang, overrides)})
    by_date = {d["date"]: d for d in days}
    weeks = [[by_date[cell.date] if cell else None for cell in week] for week in built.weeks()]
    practitioner = connection.execute("SELECT * FROM users WHERE id = ?", (owner_id,)).fetchone() \
        if owner_id else None
    numbers = json.loads(lead["numbers"])
    import readings  # loaded after this module
    reading = readings.completed_magialogia(lead["id"]) if cal["kind"] == "magialogia" else None
    params = readings.reading_params(reading) if reading else json.loads(cal["params"] or "{}")
    prev_y, prev_m = engine.shift_month(year, month, -1)
    next_y, next_m = engine.shift_month(year, month, 1)
    return {
        "cal": cal, "lead": lead, "lang": lang, "labels": content.LABELS[lang], "key": built.key,
        "year": year, "month": month, "month_name": content.MONTH_NAMES[lang][month - 1],
        "personal_year": built.personal_year, "personal_month": built.personal_month,
        "year_guide": content.guidance(built.personal_year, lang, overrides),
        "month_guide": content.guidance(built.personal_month, lang, overrides),
        "days": days, "weeks": weeks, "params": params, "reading": reading,
        "intro": month_row["intro"] if month_row else "",
        "published": bool(month_row and month_row["published_at"]),
        "visible": cal["kind"] == "numerology" or bool(month_row and month_row["published_at"]),
        "life_path": numbers.get("life_path"), "client_first": (lead["full_name"].split() or [""])[0],
        "card": contact_card(practitioner) if practitioner else None,
        "prev_key": f"{prev_y:04d}-{prev_m:02d}", "next_key": f"{next_y:04d}-{next_m:02d}",
        "client_url": f"{BASE_URL}/c/{cal['token']}/{built.key}",
    }


app.jinja_env.globals["lead_calendar"] = calendar_for_lead
app.jinja_env.globals["calendar_kinds"] = KINDS
app.jinja_env.globals["calendar_languages"] = content.LANGUAGES


def preferred_calendar_lang(practitioner_id) -> str:
    """The language this practitioner used for her latest calendar (Spanish if none yet)."""
    row = db().execute("SELECT lang FROM calendars WHERE practitioner_id = ? ORDER BY id DESC LIMIT 1",
                       (practitioner_id,)).fetchone() if practitioner_id else None
    return row["lang"] if row and row["lang"] in content.LANGUAGES else "es"


app.jinja_env.globals["preferred_calendar_lang"] = preferred_calendar_lang


def _scoped_lead(lead_id: int):
    import workspace  # loaded before this module
    return workspace.scoped_lead(lead_id)


# --------------------------------------------------------------------------
# Practitioner: start, edit and publish
# --------------------------------------------------------------------------
@app.post("/leads/<int:lead_id>/calendar")
@login_required
def start_calendar(lead_id):
    lead = _scoped_lead(lead_id)
    kind = request.form.get("kind", "")
    lang = request.form.get("lang", "en")
    if kind not in KINDS or lang not in content.LANGUAGES:
        abort(400, description="Choose a calendar type and language.")
    if lead["status"] != "captured":
        flash("This visitor hasn't unlocked their report yet, so there's no email to send a calendar to.", "error")
        return redirect(url_for("lead_detail", lead_id=lead_id))
    if kind == "magialogia":
        import readings  # loaded after this module
        if readings.completed_magialogia(lead_id) is None:
            flash("Log a completed Magialogía Numérica reading for this client first: the calendar uses its details.",
                  "error")
            return redirect(url_for("lead_detail", lead_id=lead_id) + "#readings")
    with open_db() as connection:
        existing = connection.execute("SELECT id FROM calendars WHERE lead_id = ?", (lead_id,)).fetchone()
        if existing:
            connection.execute("UPDATE calendars SET kind = ?, lang = ?, active = 1, updated_at = ? WHERE id = ?",
                               (kind, lang, iso(now_utc()), existing["id"]))
        else:
            connection.execute(
                "INSERT INTO calendars (lead_id, practitioner_id, kind, lang, token, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (lead_id, lead["practitioner_id"], kind, lang, secrets.token_urlsafe(18), iso(now_utc())))
    return redirect(url_for("edit_calendar", lead_id=lead_id))


@app.get("/leads/<int:lead_id>/calendar")
@login_required
def edit_calendar(lead_id):
    lead = _scoped_lead(lead_id)
    cal = calendar_for_lead(lead_id)
    if cal is None:
        return redirect(url_for("lead_detail", lead_id=lead_id))
    year, month = engine.parse_month(request.args.get("month"), engine.local_today())
    view = month_view(cal, lead, year, month)
    return render_template("calendar_edit.html", kinds=KINDS, languages=content.LANGUAGES, **view)


@app.post("/leads/<int:lead_id>/calendar/settings")
@login_required
def save_calendar_settings(lead_id):
    _scoped_lead(lead_id)
    cal = calendar_for_lead(lead_id)
    if cal is None:
        abort(404)
    lang = request.form.get("lang", cal["lang"])
    if lang not in content.LANGUAGES:
        abort(400)
    params = {}
    for field in PARAM_FIELDS:
        value = (request.form.get(field) or "").replace("\r\n", "\n").strip()
        params[field] = value[:PARAM_LIMITS.get(field, 200)]
    with open_db() as connection:
        connection.execute("UPDATE calendars SET lang = ?, params = ?, active = ?, updated_at = ? WHERE id = ?",
                           (lang, json.dumps(params), 0 if request.form.get("paused") == "yes" else 1,
                            iso(now_utc()), cal["id"]))
    flash("Calendar settings saved.", "info")
    return redirect(url_for("edit_calendar", lead_id=lead_id, month=request.form.get("month")))


@app.post("/leads/<int:lead_id>/calendar/<month_key>")
@login_required
def save_calendar_month(lead_id, month_key):
    _scoped_lead(lead_id)
    cal = calendar_for_lead(lead_id)
    if cal is None:
        abort(404)
    year, month = engine.parse_month(month_key)
    key = f"{year:04d}-{month:02d}"
    if key != month_key:
        abort(404)
    intro = (request.form.get("intro") or "").replace("\r\n", "\n").strip()[:MAX_INTRO]
    action = request.form.get("action", "save")
    with open_db() as connection:
        for name, value in request.form.items():
            if not name.startswith("note-"):
                continue
            day = name[5:]
            if not day.startswith(key + "-"):
                continue
            try:
                dt.date.fromisoformat(day)
            except ValueError:
                continue
            text = value.replace("\r\n", "\n").strip()[:MAX_NOTE]
            if text:
                connection.execute("INSERT INTO calendar_notes (calendar_id, day, text) VALUES (?, ?, ?)"
                                   " ON CONFLICT(calendar_id, day) DO UPDATE SET text = excluded.text",
                                   (cal["id"], day, text))
            else:
                connection.execute("DELETE FROM calendar_notes WHERE calendar_id = ? AND day = ?", (cal["id"], day))
        published = {"publish": iso(now_utc()), "unpublish": None}.get(action, "keep")
        connection.execute("INSERT INTO calendar_months (calendar_id, month, intro, published_at) VALUES (?, ?, ?, ?)"
                           " ON CONFLICT(calendar_id, month) DO UPDATE SET intro = excluded.intro"
                           + ("" if published == "keep" else ", published_at = excluded.published_at"),
                           (cal["id"], key, intro, None if published == "keep" else published))
    flash({"publish": "Published: your client can now see this month.",
           "unpublish": "Unpublished: your client can no longer see this month."}.get(action, "Saved."), "info")
    return redirect(url_for("edit_calendar", lead_id=lead_id, month=key))


# --------------------------------------------------------------------------
# Practitioner: her own guidance for each number
# --------------------------------------------------------------------------
FIELD_LIMITS = {"title": 80, "focus": 300, "avoid": 300, "lucky": 60, "color": 40}


def _guidance_owner(value) -> tuple[int, object]:
    """Whose guidance is being edited: your own (your practitioner's, for a team member);
    an admin can open any practitioner's from that practitioner's calendar."""
    user = current_user()
    owner_id = workspace_owner_id(user)
    if user["role"] == "admin" and value:
        try:
            owner_id = int(value)
        except (TypeError, ValueError):
            abort(404)
    owner = db().execute("SELECT id, display_name, team_of FROM users WHERE id = ?", (owner_id,)).fetchone()
    if owner is None or owner["team_of"]:
        abort(404)
    return owner_id, owner


@app.get("/settings/calendar")
@login_required
def edit_guidance():
    if current_user()["role"] == "admin" and not request.args.get("for"):
        # an admin has no calendars of her own: choose whose guidance to edit
        practitioners = db().execute("SELECT id, display_name, email FROM users WHERE role = 'practitioner'"
                                     " AND team_of IS NULL AND is_active = 1 ORDER BY display_name").fetchall()
        return render_template("guidance.html", practitioners=practitioners, owner=None, for_id=None)
    owner_id, owner = _guidance_owner(request.args.get("for"))
    lang = request.args.get("lang") or preferred_calendar_lang(owner_id)
    if lang not in content.LANGUAGES:
        lang = "es"
    overrides = practitioner_overrides(owner_id, lang)
    entries = [{"number": number, "default": content.DEFAULTS[lang][number],
                "mine": {field: (overrides.get(number) or {}).get(field, "") for field in content.FIELDS}}
               for number in content.NUMBERS]
    customised = sum(1 for entry in entries if any(entry["mine"].values()))
    editing_other = owner_id != workspace_owner_id(current_user())
    return render_template("guidance.html", lang=lang, languages=content.LANGUAGES, entries=entries,
                           fields=content.FIELDS, limits=FIELD_LIMITS, labels=content.LABELS[lang],
                           customised=customised, owner=owner, for_id=owner_id if editing_other else None)


@app.post("/settings/calendar")
@login_required
def save_guidance():
    if current_user()["role"] == "admin" and not request.form.get("for"):
        abort(400, description="Choose a practitioner.")
    owner_id, _owner = _guidance_owner(request.form.get("for"))
    lang = request.form.get("lang", "")
    if lang not in content.LANGUAGES:
        abort(400, description="Unknown language.")
    with open_db() as connection:
        if request.form.get("action") == "reset":
            connection.execute("DELETE FROM calendar_content WHERE practitioner_id = ? AND lang = ?", (owner_id, lang))
            flash(f"{content.LANGUAGES[lang]}: back to the starter texts.", "info")
            return redirect(url_for("edit_guidance", lang=lang, **{"for": request.form.get("for") or None}))
        for number in content.NUMBERS:
            default = content.DEFAULTS[lang][number]
            values = {}
            for field in content.FIELDS:
                value = " ".join((request.form.get(f"{number}-{field}") or "").split())[:FIELD_LIMITS[field]]
                values[field] = "" if value == default[field] else value  # unchanged text keeps following the default
            if any(values.values()):
                connection.execute(
                    "INSERT INTO calendar_content (practitioner_id, lang, number, title, focus, avoid, lucky, color,"
                    " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (practitioner_id, lang, number)"
                    " DO UPDATE SET title = excluded.title, focus = excluded.focus, avoid = excluded.avoid,"
                    " lucky = excluded.lucky, color = excluded.color, updated_at = excluded.updated_at",
                    (owner_id, lang, number, *(values[field] for field in content.FIELDS), iso(now_utc())))
            else:
                connection.execute("DELETE FROM calendar_content WHERE practitioner_id = ? AND lang = ? AND number = ?",
                                   (owner_id, lang, number))
    flash(f"Your {content.LANGUAGES[lang]} guidance is saved. Every calendar in {content.LANGUAGES[lang]} uses it now.",
          "info")
    return redirect(url_for("edit_guidance", lang=lang, **{"for": request.form.get("for") or None}))


# --------------------------------------------------------------------------
# Client: private link and PDF
# --------------------------------------------------------------------------
def _public_calendar(token: str):
    if not token or len(token) > 64:
        abort(404)
    cal = db().execute("SELECT * FROM calendars WHERE token = ? AND active = 1", (token,)).fetchone()
    if cal is None:
        abort(404)
    lead = db().execute("SELECT * FROM leads WHERE id = ?", (cal["lead_id"],)).fetchone()
    if lead is None:
        abort(404)
    return cal, lead


def _can_preview(lead) -> bool:
    user = current_user()
    if user is None:
        return False
    if user["role"] == "admin":
        return True
    from app import workspace_owner_id
    return lead["practitioner_id"] is not None and lead["practitioner_id"] == workspace_owner_id(user)


@app.get("/c/<token>")
@app.get("/c/<token>/<month_key>")
def calendar_public(token, month_key=None):
    cal, lead = _public_calendar(token)
    year, month = engine.parse_month(month_key, engine.local_today())
    view = month_view(cal, lead, year, month)
    preview = not view["visible"] and _can_preview(lead)
    return render_template("calendar_public.html", preview=preview, **view)


@app.get("/c/<token>/<month_key>.pdf")
def calendar_pdf(token, month_key):
    cal, lead = _public_calendar(token)
    year, month = engine.parse_month(month_key)
    if f"{year:04d}-{month:02d}" != month_key:
        abort(404)
    view = month_view(cal, lead, year, month)
    if not view["visible"] and not _can_preview(lead):
        abort(404)
    pdf = render_pdf(view)
    filename = f"calendar-{view['client_first'].lower() or 'client'}-{view['key']}.pdf"
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{filename}"'})


# --------------------------------------------------------------------------
# PDF (ReportLab, brand fonts)
# --------------------------------------------------------------------------
FONT_DIR = Path(__file__).resolve().parent / "fonts_pdf"
_fonts_ready = False


def _register_fonts():
    global _fonts_ready
    if _fonts_ready:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for name in ("Cormorant-SemiBold", "Cormorant-SemiBoldItalic", "Cormorant-Bold", "Inter-Regular", "Inter-SemiBold"):
        pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / f"{name}.ttf")))
    _fonts_ready = True


NIGHT = (0x16 / 255, 0x0d / 255, 0x31 / 255)
PURPLE = (0x3a / 255, 0x2a / 255, 0x6b / 255)
GOLD = (0xd9 / 255, 0xb4 / 255, 0x5a / 255)
INK = (0x2a / 255, 0x23 / 255, 0x40 / 255)
MUTED = (0x6f / 255, 0x67 / 255, 0x86 / 255)
LINE = (0xe4 / 255, 0xdb / 255, 0xf1 / 255)
CREAM = (0xf6 / 255, 0xf0 / 255, 0xe1 / 255)


def _wrap(text: str, font: str, size: float, width: float) -> list[str]:
    from reportlab.pdfbase.pdfmetrics import stringWidth
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if stringWidth(trial, font, size) <= width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _moon(c, x, y, r, phase):
    c.setLineWidth(0.8)
    c.setStrokeColorRGB(*PURPLE)
    if phase == "new":
        c.setFillColorRGB(*PURPLE)
        c.circle(x, y, r, stroke=1, fill=1)
    elif phase == "full":
        c.setFillColorRGB(1, 1, 1)
        c.circle(x, y, r, stroke=1, fill=1)
    else:
        c.setFillColorRGB(1, 1, 1)
        c.circle(x, y, r, stroke=1, fill=1)
        c.setFillColorRGB(*PURPLE)
        path = c.beginPath()
        if phase == "first_quarter":   # right half lit: shade the left half
            path.arc(x - r, y - r, x + r, y + r, 90, 180)
        else:                          # last quarter: shade the right half
            path.arc(x - r, y - r, x + r, y + r, -90, 180)
        path.close()
        c.drawPath(path, stroke=0, fill=1)


def render_pdf(view: dict) -> bytes:
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.pdfgen import canvas

    _register_fonts()
    labels = view["labels"]
    buffer = io.BytesIO()
    width, height = landscape(letter)
    c = canvas.Canvas(buffer, pagesize=(width, height))
    c.setTitle(f"{labels['calendar']} {view['month_name']} {view['year']} · {view['lead']['full_name']}")
    c.setAuthor((view["card"] or {}).get("name") or "numer.life")
    margin = 36

    def header(subtitle: str):
        c.setFillColorRGB(*NIGHT)
        c.rect(0, height - 78, width, 78, stroke=0, fill=1)
        c.setFillColorRGB(*CREAM)
        c.setFont("Cormorant-Bold", 26)
        c.drawString(margin, height - 46, f"{view['month_name']} {view['year']}")
        c.setFillColorRGB(*GOLD)
        c.setFont("Cormorant-SemiBoldItalic", 15)
        c.drawString(margin, height - 66, subtitle)
        c.setFont("Inter-Regular", 9)
        c.setFillColorRGB(*CREAM)
        right = [f"{labels['personal_year']}: {view['personal_year']}   ·   {labels['personal_month']}: {view['personal_month']}"]
        if view.get("life_path"):
            right.append(f"{labels['life_path']}: {view['life_path']}")
        for index, line in enumerate(right):
            c.drawRightString(width - margin, height - 40 - index * 14, line)

    def footer():
        c.setFont("Inter-Regular", 7.5)
        c.setFillColorRGB(*MUTED)
        card = view["card"]
        by = f"{labels['prepared_by']} {card['name']}" if card else "numer.life"
        if card and card.get("email"):
            by += f" · {card['email']}"
        c.drawString(margin, 22, by)
        c.drawRightString(width - margin, 22, f"numer.life · {labels['footer']}")

    # Page 1: month grid
    header(f"{labels['calendar']} {labels['for']} {view['lead']['full_name']}")
    top = height - 96
    grid_w = width - 2 * margin
    col_w = grid_w / 7
    c.setFont("Inter-SemiBold", 8.5)
    c.setFillColorRGB(*MUTED)
    for index, name in enumerate(labels["weekdays"]):
        c.drawCentredString(margin + col_w * index + col_w / 2, top - 4, name.upper())
    rows = len(view["weeks"])
    grid_top = top - 14
    row_h = (grid_top - 40) / rows
    for r, week in enumerate(view["weeks"]):
        for col, day in enumerate(week):
            x = margin + col * col_w
            y = grid_top - (r + 1) * row_h
            c.setStrokeColorRGB(*LINE)
            c.setLineWidth(0.6)
            c.setFillColorRGB(1, 1, 1) if day else c.setFillColorRGB(0.985, 0.98, 0.99)
            c.rect(x, y, col_w, row_h, stroke=1, fill=1)
            if not day:
                continue
            c.setFillColorRGB(*INK)
            c.setFont("Inter-SemiBold", 9)
            c.drawString(x + 5, y + row_h - 12, str(day["date"].day))
            # personal day number badge
            c.setFillColorRGB(*PURPLE)
            c.circle(x + col_w - 13, y + row_h - 12, 8.5, stroke=0, fill=1)
            c.setFillColorRGB(*GOLD)
            c.setFont("Inter-SemiBold", 8 if day["number"] > 9 else 9)
            c.drawCentredString(x + col_w - 13, y + row_h - 15, str(day["number"]))
            line_y = y + row_h - 27
            c.setFillColorRGB(*INK)
            c.setFont("Cormorant-SemiBold", 10.5)
            for line in _wrap(day["guide"]["title"], "Cormorant-SemiBold", 10.5, col_w - 10)[:2]:
                c.drawString(x + 5, line_y, line)
                line_y -= 11
            c.setFont("Inter-Regular", 6.6)
            c.setFillColorRGB(*MUTED)
            extra = day["note"] or day["guide"]["focus"]
            for line in _wrap(extra, "Inter-Regular", 6.6, col_w - 10)[:max(0, int((line_y - y - 14) / 8))]:
                c.drawString(x + 5, line_y, line)
                line_y -= 8
            if day["moon"]:
                _moon(c, x + 9, y + 8, 3.6, day["moon"])
                c.setFont("Inter-Regular", 6)
                c.setFillColorRGB(*PURPLE)
                c.drawString(x + 16, y + 6, labels["moon"][day["moon"]])
            if day["holiday"]:
                c.setFont("Inter-SemiBold", 6.2)
                c.setFillColorRGB(*GOLD)
                c.drawRightString(x + col_w - 5, y + 6, day["holiday"])
    footer()
    c.showPage()

    # Following pages: overview + day-by-day guidance
    header(f"{labels['personal_month']} {view['personal_month']}: {view['month_guide']['title']}")
    y = height - 104
    text_w = width - 2 * margin

    def ensure(space: float):
        nonlocal y
        if y - space < 40:
            footer()
            c.showPage()
            header(f"{labels['calendar']} {labels['for']} {view['lead']['full_name']}")
            y = height - 104

    def paragraph(text: str, font="Inter-Regular", size=9, color=INK, indent=0, leading=12):
        nonlocal y
        for line in _wrap(text, font, size, text_w - indent):
            ensure(leading)
            c.setFont(font, size)
            c.setFillColorRGB(*color)
            c.drawString(margin + indent, y, line)
            y -= leading

    if view["intro"]:
        for para in view["intro"].split("\n"):
            if para.strip():
                paragraph(para.strip(), size=10, leading=13)
        y -= 6
    params = view["params"]
    if view["cal"]["kind"] == "magialogia" and any(params.get(k) for k in ("an", "ac", "ad", "n", "strong", "weak")):
        conn = "   ".join(f"{k.upper()}: {params[k]}" for k in ("an", "ac", "ad", "n") if params.get(k))
        if conn:
            paragraph(f"{labels['connections']}: {conn}", font="Inter-SemiBold", size=9)
        if params.get("strong"):
            paragraph(f"{labels['strong']}: {params['strong']}", size=9)
        if params.get("weak"):
            paragraph(f"{labels['weak']}: {params['weak']}", size=9)
        y -= 6
    for day in view["days"]:
        guide = day["guide"]
        ensure(48)
        c.setStrokeColorRGB(*LINE)
        c.line(margin, y + 9, width - margin, y + 9)
        c.setFillColorRGB(*PURPLE)
        c.circle(margin + 9, y - 2, 9, stroke=0, fill=1)
        c.setFillColorRGB(*GOLD)
        c.setFont("Inter-SemiBold", 8.5 if day["number"] > 9 else 9.5)
        c.drawCentredString(margin + 9, y - 5.5, str(day["number"]))
        from reportlab.pdfbase.pdfmetrics import stringWidth
        when = f"{labels['weekdays'][day['date'].weekday()]} {day['date'].day}"
        c.setFillColorRGB(*INK)
        c.setFont("Inter-SemiBold", 10)
        c.drawString(margin + 26, y - 5, when)
        tail = guide["title"]
        if day["holiday"]:
            tail += f" · {day['holiday']}"
        if day["moon"]:
            tail += f" · {labels['moon'][day['moon']]}"
        c.setFont("Cormorant-SemiBold", 13)
        c.drawString(margin + 32 + stringWidth(when, "Inter-SemiBold", 10), y - 5, tail)
        y -= 20
        paragraph(f"{labels['focus']}: {guide['focus']}", indent=26, size=8.6, leading=11)
        paragraph(f"{labels['avoid']}: {guide['avoid']}", indent=26, size=8.6, leading=11, color=MUTED)
        paragraph(f"{labels['lucky']}: {guide['lucky']}   ·   {labels['color']}: {guide['color']}", indent=26,
                  size=8, leading=11, color=MUTED)
        if day["note"]:
            paragraph(f"{labels['note']}: {day['note']}", font="Inter-SemiBold", indent=26, size=8.6, leading=11,
                      color=PURPLE)
        y -= 6
    footer()
    c.showPage()
    c.save()
    return buffer.getvalue()
