"""Day journal on the client's calendar.

The client taps any day on their calendar (/c/<token>/<YYYY-MM>) and writes a note about it.
A day can hold several notes; each keeps the date it is about and the time it was written
(in the client's own time zone, sent by their browser). Notes are private to the client,
their practitioner and the practitioner's team.

Notes live in the same table as the feedback journal (feedback_entries, source = 'calendar'),
so everything a client has written, on either page, shows up in one place: on every calendar
day it is about, and on the practitioner's lead page with the usual push + email alert.

No client account yet: the calendar's private link is the sign-in, as for the rest of the
client pages.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import abort, flash, redirect, request, url_for

import calendar_engine as engine
from app import app, db, iso, now_utc, open_db

MAX_BODY = 4000
SUBMIT_LIMIT_PER_HOUR = 30
RANGE_DAYS = 400  # notes can be about any day within about a year of today, before or after

T = {
    "en": {"edit": "Edit", "save_changes": "Save changes", "cancel": "Cancel", "updated": "Note updated.",
           "journal": "Your journal", "add": "Add a note", "placeholder": "What happened? How do you feel?",
           "feeling": "How do you feel? (optional)", "save": "Save note",
           "private": "Only {who} and their team see your notes.", "saved": "Note saved.", "deleted": "Note deleted.",
           "delete": "Delete", "written": "written", "error_empty": "Please write a few words or choose how you feel.",
           "error_day": "Please choose a day within a year of today.",
           "error_limit": "Too many notes from your connection. Please try again in an hour.",
           "write_today": "Write in your journal", "your_numerologist": "your numerologist",
           "feelings": {1: "Very low", 2: "Low", 3: "Okay", 4: "Good", 5: "Very good"}},
    "es": {"edit": "Editar", "save_changes": "Guardar cambios", "cancel": "Cancelar", "updated": "Nota actualizada.",
           "journal": "Tu diario", "add": "Añadir una nota", "placeholder": "¿Qué pasó? ¿Cómo te sientes?",
           "feeling": "¿Cómo te sientes? (opcional)", "save": "Guardar nota",
           "private": "Solo {who} y su equipo ven tus notas.", "saved": "Nota guardada.", "deleted": "Nota eliminada.",
           "delete": "Eliminar", "written": "escrita", "error_empty": "Escribe unas palabras o elige cómo te sientes.",
           "error_day": "Elige un día dentro de un año a partir de hoy.",
           "error_limit": "Demasiadas notas desde tu conexión. Inténtalo de nuevo en una hora.",
           "write_today": "Escribir en tu diario", "your_numerologist": "tu numeróloga",
           "feelings": {1: "Muy mal", 2: "Mal", 3: "Regular", 4: "Bien", 5: "Muy bien"}},
    "fr": {"edit": "Modifier", "save_changes": "Enregistrer", "cancel": "Annuler", "updated": "Note modifiée.",
           "journal": "Votre journal", "add": "Ajouter une note", "placeholder": "Que s'est-il passé ? Comment vous sentez-vous ?",
           "feeling": "Comment vous sentez-vous ? (facultatif)", "save": "Enregistrer la note",
           "private": "Seuls {who} et son équipe voient vos notes.", "saved": "Note enregistrée.",
           "deleted": "Note supprimée.", "delete": "Supprimer", "written": "écrite",
           "error_empty": "Écrivez quelques mots ou choisissez comment vous vous sentez.",
           "error_day": "Choisissez un jour à moins d'un an d'aujourd'hui.",
           "error_limit": "Trop de notes depuis votre connexion. Réessayez dans une heure.",
           "write_today": "Écrire dans votre journal", "your_numerologist": "votre numérologue",
           "feelings": {1: "Très mal", 2: "Mal", 3: "Correct", 4: "Bien", 5: "Très bien"}},
    "pt": {"edit": "Editar", "save_changes": "Salvar alterações", "cancel": "Cancelar", "updated": "Nota atualizada.",
           "journal": "Seu diário", "add": "Adicionar uma nota", "placeholder": "O que aconteceu? Como você se sente?",
           "feeling": "Como você se sente? (opcional)", "save": "Salvar nota",
           "private": "Só {who} e a equipe veem suas notas.", "saved": "Nota salva.", "deleted": "Nota apagada.",
           "delete": "Apagar", "written": "escrita", "error_empty": "Escreva algumas palavras ou escolha como se sente.",
           "error_day": "Escolha um dia dentro de um ano a partir de hoje.",
           "error_limit": "Muitas notas da sua conexão. Tente de novo em uma hora.",
           "write_today": "Escrever no seu diário", "your_numerologist": "sua numeróloga",
           "feelings": {1: "Muito mal", 2: "Mal", 3: "Mais ou menos", 4: "Bem", 5: "Muito bem"}},
    "it": {"edit": "Modifica", "save_changes": "Salva modifiche", "cancel": "Annulla", "updated": "Nota aggiornata.",
           "journal": "Il tuo diario", "add": "Aggiungi una nota", "placeholder": "Cosa è successo? Come ti senti?",
           "feeling": "Come ti senti? (facoltativo)", "save": "Salva nota",
           "private": "Solo {who} e il suo team vedono le tue note.", "saved": "Nota salvata.",
           "deleted": "Nota eliminata.", "delete": "Elimina", "written": "scritta",
           "error_empty": "Scrivi qualche parola o scegli come ti senti.",
           "error_day": "Scegli un giorno entro un anno da oggi.",
           "error_limit": "Troppe note dalla tua connessione. Riprova tra un'ora.",
           "write_today": "Scrivi nel tuo diario", "your_numerologist": "la tua numerologa",
           "feelings": {1: "Molto male", 2: "Male", 3: "Così così", 4: "Bene", 5: "Molto bene"}},
    "de": {"edit": "Bearbeiten", "save_changes": "Änderungen speichern", "cancel": "Abbrechen", "updated": "Notiz aktualisiert.",
           "journal": "Dein Tagebuch", "add": "Notiz hinzufügen", "placeholder": "Was ist passiert? Wie fühlst du dich?",
           "feeling": "Wie fühlst du dich? (optional)", "save": "Notiz speichern",
           "private": "Nur {who} und das Team sehen deine Notizen.", "saved": "Notiz gespeichert.",
           "deleted": "Notiz gelöscht.", "delete": "Löschen", "written": "geschrieben",
           "error_empty": "Schreib ein paar Worte oder wähle, wie du dich fühlst.",
           "error_day": "Wähle einen Tag innerhalb eines Jahres ab heute.",
           "error_limit": "Zu viele Notizen von deiner Verbindung. Bitte versuch es in einer Stunde noch einmal.",
           "write_today": "Ins Tagebuch schreiben", "your_numerologist": "deine Numerologin",
           "feelings": {1: "Sehr schlecht", 2: "Schlecht", 3: "Okay", 4: "Gut", 5: "Sehr gut"}},
}


def labels(lang: str) -> dict:
    return T.get(lang) or T["en"]


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name) if name else ZoneInfo(engine.DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(engine.DEFAULT_TZ)


def clean_tz(name: str | None) -> str | None:
    """A browser time zone name we can use, or None."""
    name = (name or "").strip()[:64]
    if not name:
        return None
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return name


def written_at(entry) -> dt.datetime:
    """When the note was written, in the writer's own time zone."""
    moment = dt.datetime.fromisoformat(entry["created_at"])
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    tz = entry["tz"] if "tz" in entry.keys() else None
    return moment.astimezone(_zone(tz))


def time_label(entry) -> str:
    """'14:05', or '14:05 · written 2026-11-19' when it was written on another day."""
    moment = written_at(entry)
    text = moment.strftime("%H:%M")
    if moment.date().isoformat() != entry["entry_date"]:
        text += f" · {moment.date().isoformat()}"
    return text


def month_entries(lead_id: int, month_key: str) -> dict[str, list]:
    """Everything the client wrote about days in this month, by date, oldest first."""
    by_date: dict[str, list] = {}
    for row in db().execute("SELECT * FROM feedback_entries WHERE lead_id = ? AND entry_date LIKE ?"
                            " ORDER BY entry_date, created_at, id", (lead_id, f"{month_key}-%")):
        by_date.setdefault(row["entry_date"], []).append(row)
    return by_date


app.jinja_env.globals.update(journal_labels=labels, journal_month_entries=month_entries,
                             journal_time=time_label)


# --------------------------------------------------------------------------
# Client: add and delete notes
# --------------------------------------------------------------------------
def _calendar(token: str):
    import calendars
    return calendars._public_calendar(token)


def _back(token: str, day: dt.date | None, month_key: str | None = None) -> str:
    key = day.strftime("%Y-%m") if day else month_key
    anchor = f"#d{day.day}" if day else ""
    return url_for("calendar_public", token=token, month_key=key) + anchor


@app.post("/c/<token>/notes")
def journal_add(token):
    import feedback
    import intake
    cal, lead = _calendar(token)
    t = labels(cal["lang"])
    try:
        day = dt.date.fromisoformat(request.form.get("day", ""))
    except ValueError:
        day = None
    if request.form.get("website"):  # honeypot
        return redirect(_back(token, day))
    today = engine.local_today()
    if day is None or abs((day - today).days) > RANGE_DAYS:
        flash(t["error_day"], "error")
        return redirect(_back(token, None, request.form.get("month")))
    body = (request.form.get("body") or "").replace("\r\n", "\n").strip()[:MAX_BODY]
    feeling = request.form.get("feeling", type=int)
    if feeling not in t["feelings"]:
        feeling = None
    if not body and feeling is None:
        flash(t["error_empty"], "error")
        return redirect(_back(token, day))
    if not intake.reserve_attempt("feedback", SUBMIT_LIMIT_PER_HOUR):
        flash(t["error_limit"], "error")
        return redirect(_back(token, day))
    with open_db() as connection:
        entry_id = connection.execute(
            "INSERT INTO feedback_entries (lead_id, source, entry_date, feeling, body, tz, created_at)"
            " VALUES (?, 'calendar', ?, ?, ?, ?, ?)",
            (lead["id"], day.isoformat(), feeling, body, clean_tz(request.form.get("tz")),
             iso(now_utc()))).lastrowid
        entry = connection.execute("SELECT * FROM feedback_entries WHERE id = ?", (entry_id,)).fetchone()
        recipients = feedback._queue_alerts(connection, lead, entry, subject="Journal note from {name}")
    import emails
    import notify
    emails.kick()
    first = feedback._first_name(lead)
    notify.queue([r["id"] for r in recipients], {
        "title": f"Journal note from {first}",
        "body": f"{day.isoformat()}: {(body or feedback.STAFF_FEELINGS[feeling])[:120]}",
        "url": f"/leads/{lead['id']}#feedback", "tag": f"feedback-{lead['id']}"})
    flash(t["saved"], "info")
    return redirect(_back(token, day))


@app.post("/c/<token>/notes/<int:entry_id>/delete")
def journal_delete(token, entry_id):
    cal, lead = _calendar(token)
    with open_db() as connection:
        entry = connection.execute("SELECT * FROM feedback_entries WHERE id = ? AND lead_id = ?",
                                   (entry_id, lead["id"])).fetchone()
        if entry is None:
            abort(404)
        connection.execute("DELETE FROM feedback_entries WHERE id = ?", (entry_id,))
    flash(labels(cal["lang"])["deleted"], "info")
    return redirect(_back(token, dt.date.fromisoformat(entry["entry_date"])))


@app.post("/c/<token>/notes/<int:entry_id>/edit")
def journal_edit(token, entry_id):
    cal, lead = _calendar(token)
    t = labels(cal["lang"])
    body = (request.form.get("body") or "").replace("\r\n", "\n").strip()[:MAX_BODY]
    feeling = request.form.get("feeling", type=int)
    if feeling not in t["feelings"]:
        feeling = None
    with open_db() as connection:
        entry = connection.execute("SELECT * FROM feedback_entries WHERE id = ? AND lead_id = ?",
                                   (entry_id, lead["id"])).fetchone()
        if entry is None:
            abort(404)
        day = dt.date.fromisoformat(entry["entry_date"])
        if not body and feeling is None:
            flash(t["error_empty"], "error")
            return redirect(_back(token, day))
        if body != entry["body"] or feeling != entry["feeling"]:
            # an edited note shows as new again for the practitioner (no second alert)
            connection.execute("UPDATE feedback_entries SET body = ?, feeling = ?, seen_at = NULL WHERE id = ?",
                               (body, feeling, entry_id))
    flash(t["updated"], "info")
    return redirect(_back(token, day))


@app.after_request
def _journal_headers(response):
    if request.endpoint in ("journal_add", "journal_edit", "journal_delete", "calendar_public"):
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response
