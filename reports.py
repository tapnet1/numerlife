"""Report texts in the practitioner's own words, and the full written reading.

- practitioner_overrides(): her wording for one language, laid over numer.life's starter texts.
- lead_items(): a lead's numbers with texts (used by the report page, emails and the lead page).
- full_reading(): every section of the written reading (meaning, strengths, challenges, lesson,
  affirmation per number, plus the numbers together). This is the draft a practitioner reviews
  and completes before a paid report is sent.
- /settings/report: the editor, one position at a time, per language.
"""
from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, url_for

import numerology
import report_texts as texts
from app import app, current_user, db, iso, login_required, now_utc, open_db, workspace_owner_id


def practitioner_overrides(practitioner_id, lang: str) -> dict:
    """{(position, number): {field: text}} for the fields she has written herself."""
    if not practitioner_id:
        return {}
    rows = db().execute("SELECT * FROM report_content WHERE practitioner_id = ? AND lang = ?",
                        (practitioner_id, texts.clean_lang(lang))).fetchall()
    return {(row["position"], row["number"]): {field: row[field] for field in texts.FIELDS if row[field]}
            for row in rows}


def _report_lang(lead) -> str:
    return texts.clean_lang(numerology.lead_report(lead).get("lang"))


def lead_items(lead) -> list[dict]:
    keys = lead.keys()
    practitioner_id = lead["practitioner_id"] if "practitioner_id" in keys else None
    return numerology.report_items(lead, practitioner_overrides(practitioner_id, _report_lang(lead)))


def full_reading(lead) -> dict:
    """The written reading: each number's sections, the combination sentence and the affirmations."""
    items = lead_items(lead)
    lang = _report_lang(lead)
    for item in items:
        item["strength_list"] = texts.as_list(item["strengths"])
        item["challenge_list"] = texts.as_list(item["challenges"])
    active = [item for item in items if item["number"]]
    return {
        "lang": lang,
        "labels": texts.LABELS[lang],
        "items": items,
        "combination": texts.combination(lang, active),
        "lessons": [item["lesson"] for item in active if item["lesson"]],
        "affirmations": [item["affirmation"] for item in active if item["affirmation"]],
    }


app.jinja_env.globals["full_reading"] = full_reading


# --------------------------------------------------------------------------
# Editor: /settings/report
# --------------------------------------------------------------------------
def _owner(value) -> tuple[int, object]:
    """Your own texts (your practitioner's, for a team member); an admin can open any practitioner's."""
    user = current_user()
    owner_id = workspace_owner_id(user)
    if user["role"] == "admin" and value:
        try:
            owner_id = int(value)
        except (TypeError, ValueError):
            abort(404)
    owner = db().execute("SELECT * FROM users WHERE id = ?", (owner_id,)).fetchone()
    if owner is None or owner["team_of"]:
        abort(404)
    return owner_id, owner


def _positions_for(method: str) -> tuple[str, ...]:
    return texts.METHOD_POSITIONS.get(method, texts.METHOD_POSITIONS["classic"])


@app.get("/settings/report")
@login_required
def edit_report_texts():
    if current_user()["role"] == "admin" and not request.args.get("for"):
        practitioners = db().execute("SELECT id, display_name, email FROM users WHERE role = 'practitioner'"
                                     " AND team_of IS NULL AND is_active = 1 ORDER BY display_name").fetchall()
        return render_template("report_texts.html", practitioners=practitioners, owner=None, for_id=None)
    owner_id, owner = _owner(request.args.get("for"))
    style = numerology.style_of(owner)
    lang = request.args.get("lang") or style["lang"]
    if lang not in texts.LANGUAGES:
        lang = "en"
    positions = _positions_for(style["method"])
    position = request.args.get("position") or positions[0]
    if position not in texts.POSITIONS[lang]:
        abort(404)
    overrides = practitioner_overrides(owner_id, lang)
    fields = [f for f in texts.FIELDS if not (f == "strengths" and position in texts.NO_STRENGTHS)]
    entries = [{"number": number, "default": texts.default_entry(lang, position, number),
                "mine": {field: (overrides.get((position, number)) or {}).get(field, "") for field in fields}}
               for number in texts.NUMBERS]
    customised = sum(1 for entry in entries if any(entry["mine"].values()))
    editing_other = owner_id != workspace_owner_id(current_user())
    tabs = [(key, texts.POSITIONS[lang][key][0]) for key in positions]
    return render_template("report_texts.html", lang=lang, languages=texts.LANGUAGES, position=position,
                           position_label=texts.POSITIONS[lang][position][0], tabs=tabs, entries=entries,
                           fields=fields, limits=texts.FIELD_LIMITS, labels=texts.LABELS[lang],
                           customised=customised, owner=owner, for_id=owner_id if editing_other else None)


@app.post("/settings/report")
@login_required
def save_report_texts():
    if current_user()["role"] == "admin" and not request.form.get("for"):
        abort(400, description="Choose a practitioner.")
    owner_id, _owner_row = _owner(request.form.get("for"))
    lang = request.form.get("lang", "")
    position = request.form.get("position", "")
    if lang not in texts.LANGUAGES or position not in texts.POSITIONS["en"]:
        abort(400, description="Unknown language or position.")
    label = texts.POSITIONS[lang][position][0]
    back = url_for("edit_report_texts", lang=lang, position=position, **{"for": request.form.get("for") or None})
    with open_db() as connection:
        if request.form.get("action") == "reset":
            connection.execute("DELETE FROM report_content WHERE practitioner_id = ? AND lang = ? AND position = ?",
                               (owner_id, lang, position))
            flash(f"{label} ({texts.LANGUAGES[lang]}): back to the starter texts.", "info")
            return redirect(back)
        for number in texts.NUMBERS:
            default = texts.default_entry(lang, position, number)
            values = {}
            for field in texts.FIELDS:
                raw = request.form.get(f"{number}-{field}") or ""
                value = " ".join(raw.split())[:texts.FIELD_LIMITS[field]]
                values[field] = "" if value == " ".join(default[field].split()) else value  # unchanged keeps the default
            if position in texts.NO_STRENGTHS:
                values["strengths"] = ""
            if any(values.values()):
                connection.execute(
                    "INSERT INTO report_content (practitioner_id, lang, position, number, title, meaning, strengths,"
                    " challenges, lesson, affirmation, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT (practitioner_id, lang, position, number) DO UPDATE SET title = excluded.title,"
                    " meaning = excluded.meaning, strengths = excluded.strengths, challenges = excluded.challenges,"
                    " lesson = excluded.lesson, affirmation = excluded.affirmation, updated_at = excluded.updated_at",
                    (owner_id, lang, position, number, *(values[field] for field in texts.FIELDS), iso(now_utc())))
            else:
                connection.execute("DELETE FROM report_content WHERE practitioner_id = ? AND lang = ?"
                                   " AND position = ? AND number = ?", (owner_id, lang, position, number))
    flash(f"Your {label} texts ({texts.LANGUAGES[lang]}) are saved. Reports use them from now on.", "info")
    return redirect(back)
