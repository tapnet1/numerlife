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
from app import app, db, iso, login_required, now_utc, open_db

MAX_BODY = 4000
SUBMIT_LIMIT_PER_HOUR = 30
RANGE_DAYS = 400  # notes can be about any day within about a year of today, before or after

T = {
    "en": {"reply": "Reply", "reply_placeholder": "Write a reply…", "send": "Send", "you": "You",
           "today_prompt": "Today is a {n} day: {title}. How is your day going?", "my_journal": "My journal",
           "notes_count": "{n} notes so far", "book_title": "{name}'s journal", "print": "Print or save as PDF",
           "book_empty": "No notes yet. Tap any day on your calendar to write your first note.",
           "day_note_from": "From {who}", "prepared_with": "With {who}", "replied": "Reply sent.",
           "reply_subject": "{who} replied to your journal note", "reply_lead": "{who} replied to your note from {date}:",
           "open_journal": "Open my calendar", "back_calendar": "Back to my calendar",
           "confirm_delete": "Delete this note?", "yes_delete": "Yes, delete",
           "edit": "Edit", "save_changes": "Save changes", "cancel": "Cancel", "updated": "Note updated.",
           "journal": "Your journal", "add": "Add a note", "placeholder": "What happened? How do you feel?",
           "feeling": "How do you feel? (optional)", "save": "Save note",
           "private": "Only {who} and their team see your notes.", "saved": "Note saved.", "deleted": "Note deleted.",
           "delete": "Delete", "written": "written", "error_empty": "Please write a few words or choose how you feel.",
           "error_day": "Please choose a day within a year of today.",
           "error_limit": "Too many notes from your connection. Please try again in an hour.",
           "write_today": "Write in your journal", "your_numerologist": "your numerologist",
           "feelings": {1: "Very low", 2: "Low", 3: "Okay", 4: "Good", 5: "Very good"}},
    "es": {"reply": "Responder", "reply_placeholder": "Escribe una respuesta…", "send": "Enviar", "you": "Tú",
           "today_prompt": "Hoy es un día {n}: {title}. ¿Cómo va tu día?", "my_journal": "Mi diario",
           "notes_count": "{n} notas hasta ahora", "book_title": "Diario de {name}", "print": "Imprimir o guardar como PDF",
           "book_empty": "Aún no hay notas. Toca cualquier día de tu calendario para escribir tu primera nota.",
           "day_note_from": "De {who}", "prepared_with": "Con {who}", "replied": "Respuesta enviada.",
           "reply_subject": "{who} respondió a tu nota del diario", "reply_lead": "{who} respondió a tu nota del {date}:",
           "open_journal": "Abrir mi calendario", "back_calendar": "Volver a mi calendario",
           "confirm_delete": "¿Eliminar esta nota?", "yes_delete": "Sí, eliminar",
           "edit": "Editar", "save_changes": "Guardar cambios", "cancel": "Cancelar", "updated": "Nota actualizada.",
           "journal": "Tu diario", "add": "Añadir una nota", "placeholder": "¿Qué pasó? ¿Cómo te sientes?",
           "feeling": "¿Cómo te sientes? (opcional)", "save": "Guardar nota",
           "private": "Solo {who} y su equipo ven tus notas.", "saved": "Nota guardada.", "deleted": "Nota eliminada.",
           "delete": "Eliminar", "written": "escrita", "error_empty": "Escribe unas palabras o elige cómo te sientes.",
           "error_day": "Elige un día dentro de un año a partir de hoy.",
           "error_limit": "Demasiadas notas desde tu conexión. Inténtalo de nuevo en una hora.",
           "write_today": "Escribir en tu diario", "your_numerologist": "tu numeróloga",
           "feelings": {1: "Muy mal", 2: "Mal", 3: "Regular", 4: "Bien", 5: "Muy bien"}},
    "fr": {"reply": "Répondre", "reply_placeholder": "Écrire une réponse…", "send": "Envoyer", "you": "Vous",
           "today_prompt": "Aujourd'hui est un jour {n} : {title}. Comment se passe votre journée ?", "my_journal": "Mon journal",
           "notes_count": "{n} notes jusqu'ici", "book_title": "Journal de {name}", "print": "Imprimer ou enregistrer en PDF",
           "book_empty": "Pas encore de notes. Touchez un jour de votre calendrier pour écrire votre première note.",
           "day_note_from": "De {who}", "prepared_with": "Avec {who}", "replied": "Réponse envoyée.",
           "reply_subject": "{who} a répondu à votre note de journal", "reply_lead": "{who} a répondu à votre note du {date} :",
           "open_journal": "Ouvrir mon calendrier", "back_calendar": "Retour à mon calendrier",
           "confirm_delete": "Supprimer cette note ?", "yes_delete": "Oui, supprimer",
           "edit": "Modifier", "save_changes": "Enregistrer", "cancel": "Annuler", "updated": "Note modifiée.",
           "journal": "Votre journal", "add": "Ajouter une note", "placeholder": "Que s'est-il passé ? Comment vous sentez-vous ?",
           "feeling": "Comment vous sentez-vous ? (facultatif)", "save": "Enregistrer la note",
           "private": "Seuls {who} et son équipe voient vos notes.", "saved": "Note enregistrée.",
           "deleted": "Note supprimée.", "delete": "Supprimer", "written": "écrite",
           "error_empty": "Écrivez quelques mots ou choisissez comment vous vous sentez.",
           "error_day": "Choisissez un jour à moins d'un an d'aujourd'hui.",
           "error_limit": "Trop de notes depuis votre connexion. Réessayez dans une heure.",
           "write_today": "Écrire dans votre journal", "your_numerologist": "votre numérologue",
           "feelings": {1: "Très mal", 2: "Mal", 3: "Correct", 4: "Bien", 5: "Très bien"}},
    "pt": {"reply": "Responder", "reply_placeholder": "Escreva uma resposta…", "send": "Enviar", "you": "Você",
           "today_prompt": "Hoje é um dia {n}: {title}. Como está o seu dia?", "my_journal": "Meu diário",
           "notes_count": "{n} notas até agora", "book_title": "Diário de {name}", "print": "Imprimir ou salvar como PDF",
           "book_empty": "Ainda não há notas. Toque em qualquer dia do calendário para escrever sua primeira nota.",
           "day_note_from": "De {who}", "prepared_with": "Com {who}", "replied": "Resposta enviada.",
           "reply_subject": "{who} respondeu à sua nota do diário", "reply_lead": "{who} respondeu à sua nota de {date}:",
           "open_journal": "Abrir meu calendário", "back_calendar": "Voltar ao meu calendário",
           "confirm_delete": "Apagar esta nota?", "yes_delete": "Sim, apagar",
           "edit": "Editar", "save_changes": "Salvar alterações", "cancel": "Cancelar", "updated": "Nota atualizada.",
           "journal": "Seu diário", "add": "Adicionar uma nota", "placeholder": "O que aconteceu? Como você se sente?",
           "feeling": "Como você se sente? (opcional)", "save": "Salvar nota",
           "private": "Só {who} e a equipe veem suas notas.", "saved": "Nota salva.", "deleted": "Nota apagada.",
           "delete": "Apagar", "written": "escrita", "error_empty": "Escreva algumas palavras ou escolha como se sente.",
           "error_day": "Escolha um dia dentro de um ano a partir de hoje.",
           "error_limit": "Muitas notas da sua conexão. Tente de novo em uma hora.",
           "write_today": "Escrever no seu diário", "your_numerologist": "sua numeróloga",
           "feelings": {1: "Muito mal", 2: "Mal", 3: "Mais ou menos", 4: "Bem", 5: "Muito bem"}},
    "it": {"reply": "Rispondi", "reply_placeholder": "Scrivi una risposta…", "send": "Invia", "you": "Tu",
           "today_prompt": "Oggi è un giorno {n}: {title}. Come sta andando la tua giornata?", "my_journal": "Il mio diario",
           "notes_count": "{n} note finora", "book_title": "Diario di {name}", "print": "Stampa o salva come PDF",
           "book_empty": "Ancora nessuna nota. Tocca un giorno del calendario per scrivere la tua prima nota.",
           "day_note_from": "Da {who}", "prepared_with": "Con {who}", "replied": "Risposta inviata.",
           "reply_subject": "{who} ha risposto alla tua nota del diario", "reply_lead": "{who} ha risposto alla tua nota del {date}:",
           "open_journal": "Apri il mio calendario", "back_calendar": "Torna al mio calendario",
           "confirm_delete": "Eliminare questa nota?", "yes_delete": "Sì, elimina",
           "edit": "Modifica", "save_changes": "Salva modifiche", "cancel": "Annulla", "updated": "Nota aggiornata.",
           "journal": "Il tuo diario", "add": "Aggiungi una nota", "placeholder": "Cosa è successo? Come ti senti?",
           "feeling": "Come ti senti? (facoltativo)", "save": "Salva nota",
           "private": "Solo {who} e il suo team vedono le tue note.", "saved": "Nota salvata.",
           "deleted": "Nota eliminata.", "delete": "Elimina", "written": "scritta",
           "error_empty": "Scrivi qualche parola o scegli come ti senti.",
           "error_day": "Scegli un giorno entro un anno da oggi.",
           "error_limit": "Troppe note dalla tua connessione. Riprova tra un'ora.",
           "write_today": "Scrivi nel tuo diario", "your_numerologist": "la tua numerologa",
           "feelings": {1: "Molto male", 2: "Male", 3: "Così così", 4: "Bene", 5: "Molto bene"}},
    "de": {"reply": "Antworten", "reply_placeholder": "Antwort schreiben…", "send": "Senden", "you": "Du",
           "today_prompt": "Heute ist ein {n}er-Tag: {title}. Wie läuft dein Tag?", "my_journal": "Mein Tagebuch",
           "notes_count": "Bisher {n} Notizen", "book_title": "Tagebuch von {name}", "print": "Drucken oder als PDF speichern",
           "book_empty": "Noch keine Notizen. Tippe auf einen Tag im Kalender, um deine erste Notiz zu schreiben.",
           "day_note_from": "Von {who}", "prepared_with": "Mit {who}", "replied": "Antwort gesendet.",
           "reply_subject": "{who} hat auf deine Tagebuchnotiz geantwortet", "reply_lead": "{who} hat auf deine Notiz vom {date} geantwortet:",
           "open_journal": "Meinen Kalender öffnen", "back_calendar": "Zurück zu meinem Kalender",
           "confirm_delete": "Diese Notiz löschen?", "yes_delete": "Ja, löschen",
           "edit": "Bearbeiten", "save_changes": "Änderungen speichern", "cancel": "Abbrechen", "updated": "Notiz aktualisiert.",
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
    if "entry_date" not in entry.keys():  # a reply: date and time
        return moment.strftime("%Y-%m-%d %H:%M")
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


def replies_for(entry_ids) -> dict[int, list]:
    ids = [int(i) for i in entry_ids]
    by_entry: dict[int, list] = {}
    if not ids:
        return by_entry
    marks = ",".join("?" * len(ids))
    for row in db().execute(
            "SELECT journal_replies.*, users.display_name AS staff_name FROM journal_replies"
            " LEFT JOIN users ON users.id = journal_replies.user_id"
            f" WHERE entry_id IN ({marks}) ORDER BY journal_replies.created_at, journal_replies.id", ids):
        by_entry.setdefault(row["entry_id"], []).append(row)
    return by_entry


def month_replies(by_date: dict) -> dict[int, list]:
    return replies_for([e["id"] for notes in by_date.values() for e in notes])


def lead_replies(lead_id: int) -> dict[int, list]:
    return replies_for([r["id"] for r in db().execute("SELECT id FROM feedback_entries WHERE lead_id = ?",
                                                      (lead_id,))])


def note_count(lead_id: int) -> int:
    return db().execute("SELECT COUNT(*) FROM feedback_entries WHERE lead_id = ?", (lead_id,)).fetchone()[0]


app.jinja_env.globals.update(journal_labels=labels, journal_month_entries=month_entries,
                             journal_time=time_label, journal_month_replies=month_replies,
                             journal_lead_replies=lead_replies, journal_note_count=note_count)


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
        connection.execute("DELETE FROM journal_replies WHERE entry_id = ?", (entry_id,))
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


# --------------------------------------------------------------------------
# Conversation on a note: client and practitioner (and team) reply to each other
# --------------------------------------------------------------------------
def _clean_body() -> str:
    return (request.form.get("body") or "").replace("\r\n", "\n").strip()[:MAX_BODY]


@app.post("/c/<token>/notes/<int:entry_id>/reply")
def journal_client_reply(token, entry_id):
    import feedback
    import intake
    cal, lead = _calendar(token)
    t = labels(cal["lang"])
    body = _clean_body()
    entry = db().execute("SELECT * FROM feedback_entries WHERE id = ? AND lead_id = ?",
                         (entry_id, lead["id"])).fetchone()
    if entry is None:
        abort(404)
    day = dt.date.fromisoformat(entry["entry_date"])
    if not body:
        return redirect(_back(token, day))
    if not intake.reserve_attempt("feedback", SUBMIT_LIMIT_PER_HOUR):
        flash(t["error_limit"], "error")
        return redirect(_back(token, day))
    with open_db() as connection:
        connection.execute("INSERT INTO journal_replies (entry_id, lead_id, author, body, tz, created_at)"
                           " VALUES (?, ?, 'client', ?, ?, ?)",
                           (entry_id, lead["id"], body, clean_tz(request.form.get("tz")), iso(now_utc())))
        connection.execute("UPDATE feedback_entries SET seen_at = NULL WHERE id = ?", (entry_id,))
        alert = {**dict(entry), "body": body, "feeling": None}
        recipients = feedback._queue_alerts(connection, lead, alert, subject="Journal reply from {name}")
    import emails
    import notify
    emails.kick()
    notify.queue([r["id"] for r in recipients], {
        "title": f"Journal reply from {feedback._first_name(lead)}", "body": body[:120],
        "url": f"/leads/{lead['id']}#note-{entry_id}", "tag": f"feedback-{lead['id']}"})
    flash(t["replied"], "info")
    return redirect(_back(token, day))


@app.post("/leads/<int:lead_id>/journal/<int:entry_id>/reply")
@login_required
def journal_staff_reply(lead_id, entry_id):
    import emails
    import workspace
    from app import current_user
    lead = workspace.scoped_lead(lead_id)
    body = _clean_body()
    back = url_for("lead_detail", lead_id=lead_id) + f"#note-{entry_id}"
    entry = db().execute("SELECT * FROM feedback_entries WHERE id = ? AND lead_id = ?", (entry_id, lead_id)).fetchone()
    if entry is None:
        abort(404)
    if not body:
        flash("Write a reply first.", "error")
        return redirect(back)
    user = current_user()
    cal = db().execute("SELECT * FROM calendars WHERE lead_id = ?", (lead_id,)).fetchone()
    with open_db() as connection:
        connection.execute("INSERT INTO journal_replies (entry_id, lead_id, author, user_id, body, created_at)"
                           " VALUES (?, ?, 'staff', ?, ?, ?)", (entry_id, lead_id, user["id"], body, iso(now_utc())))
        connection.execute("UPDATE feedback_entries SET seen_at = COALESCE(seen_at, ?) WHERE id = ?",
                           (iso(now_utc()), entry_id))
        if cal is not None and lead["email"] and lead["status"] == "captured":
            _queue_reply_email(connection, lead, cal, entry, body)
    emails.kick()
    flash("Reply sent. The client gets an email with a link to the note.", "info")
    return redirect(back)


def _queue_reply_email(connection, lead, cal, entry, body: str) -> None:
    import emails
    practitioner = connection.execute("SELECT * FROM users WHERE id = ?", (lead["practitioner_id"],)).fetchone() \
        if lead["practitioner_id"] else None
    lang = cal["lang"] if cal["lang"] in T else "en"
    t = T[lang]
    who = practitioner["display_name"] if practitioner is not None and practitioner["display_name"] \
        else t["your_numerologist"]
    day = dt.date.fromisoformat(entry["entry_date"])
    from app import BASE_URL
    context = {"t": t, "lang": lang, "email_lang": lang, "who": who, "date": day.isoformat(), "body": body,
               "note": entry["body"], "page_url": f"{BASE_URL}/c/{cal['token']}/{day.strftime('%Y-%m')}#d{day.day}",
               "card": emails._card(practitioner) if practitioner is not None and practitioner["is_active"] else None}
    text, html = emails._render_pair("journal_reply", **context)
    emails.enqueue(connection, "journal_reply", lead["email"], t["reply_subject"].format(who=who), text, html,
                   lead_id=lead["id"])


# --------------------------------------------------------------------------
# The client's journal: every note with the replies and the practitioner's day notes; printable
# --------------------------------------------------------------------------
@app.get("/c/<token>/journal")
def journal_book(token):
    import calendar_engine as engine
    import calendar_content as content
    import calendars
    from flask import render_template
    cal, lead = _calendar(token)
    lang = cal["lang"] if cal["lang"] in T else "en"
    entries = db().execute("SELECT * FROM feedback_entries WHERE lead_id = ? ORDER BY entry_date, created_at, id",
                           (lead["id"],)).fetchall()
    replies = replies_for([e["id"] for e in entries])
    day_notes = {r["day"]: r["text"] for r in db().execute(
        "SELECT day, text FROM calendar_notes WHERE calendar_id = ?", (cal["id"],))}
    birth = dt.date.fromisoformat(lead["birth_date"])
    overrides = calendars.practitioner_overrides(lead["practitioner_id"], lang)
    days: list[dict] = []
    for entry in entries:
        if not days or days[-1]["date"] != entry["entry_date"]:
            date = dt.date.fromisoformat(entry["entry_date"])
            number = engine.personal_day(birth, date)
            days.append({"date": entry["entry_date"], "day": date, "number": number,
                         "title": content.guidance(number, lang, overrides)["title"],
                         "month_name": content.MONTH_NAMES[lang][date.month - 1],
                         "day_note": day_notes.get(entry["entry_date"], ""), "notes": []})
        days[-1]["notes"].append(entry)
    practitioner = db().execute("SELECT * FROM users WHERE id = ?", (lead["practitioner_id"],)).fetchone() \
        if lead["practitioner_id"] else None
    t = labels(lang)
    who = practitioner["display_name"] if practitioner is not None and practitioner["display_name"] \
        else t["your_numerologist"]
    return render_template("journal_book.html", cal=cal, lead=lead, lang=lang, jt=t, days=days, replies=replies,
                           who=who, first_name=(lead["full_name"].split() or [""])[0])


@app.after_request
def _journal_headers(response):
    if request.endpoint in ("journal_add", "journal_edit", "journal_delete", "calendar_public",
                            "journal_client_reply", "journal_book"):
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response
