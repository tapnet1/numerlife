"""Email: the visitor's report, new-lead alerts for practitioners, reminder nudges, unsubscribe,
and admin test sends.

Every email goes through the email_outbox table: it is written in the same request (after the
lead is committed), then sent by a background thread and retried by the `send-email` cron job.
No database transaction is open while talking to the mail server.

Automatic nudges are off until an admin turns them on (Admin > Email), so they can be tested first.
"""
from __future__ import annotations

import datetime as dt
import json
import threading

from flask import abort, flash, has_request_context, redirect, render_template, request, url_for
from itsdangerous import BadSignature, URLSafeSerializer

import mailer
import numerology
from app import BASE_URL, SITE_NAME, app, connect, current_user, db, iso, now_utc, open_db, role_required
from practitioner_profile import contact_card

RETRY_MAX_DELAY = dt.timedelta(hours=1)    # failed sends back off 2, 4, 8 ... minutes, up to this
EXPIRE_AFTER = dt.timedelta(days=3)          # don't deliver very stale queued email
PRACTITIONER_NUDGE_EVERY = dt.timedelta(days=3)
LEAD_WAITING = dt.timedelta(hours=48)        # an unlocked lead still "new" after this is waiting
CLIENT_NUDGE_AFTER = dt.timedelta(days=2)
CLIENT_NUDGE_WINDOW = dt.timedelta(days=14)
KINDS = {
    "report": "Visitor's report",
    "lead_alert": "New-lead alert to practitioner",
    "practitioner_nudge": "Nudge: leads waiting (practitioner)",
    "client_nudge": "Nudge: book a reading (client)",
}


# --------------------------------------------------------------------------
# Settings (admin switches)
# --------------------------------------------------------------------------
def get_setting(key: str, default: str = "") -> str:
    with open_db() as connection:
        row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with open_db() as connection:
        connection.execute("INSERT INTO settings (key, value) VALUES (?, ?)"
                           " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def nudges_enabled() -> bool:
    return get_setting("auto_nudges") == "on"


# --------------------------------------------------------------------------
# Unsubscribe links (signed; no account needed)
# --------------------------------------------------------------------------
def _signer() -> URLSafeSerializer:
    return URLSafeSerializer(app.secret_key, salt="numer-unsubscribe")


def unsubscribe_url(kind: str, ident) -> str:
    return f"{BASE_URL}/unsubscribe/{_signer().dumps([kind, ident])}"


def cancel_nudges(connection, kind: str, user_id: int | None = None, email: str | None = None) -> None:
    """Drop reminder emails still waiting in the outbox once someone opts out."""
    if user_id is not None:
        where, value = "user_id = ?", user_id
    else:
        where, value = "lower(to_addr) = lower(?)", email
    connection.execute(f"UPDATE email_outbox SET sent_at = ?, last_error = 'cancelled: unsubscribed'"
                       f" WHERE sent_at IS NULL AND kind = ? AND {where}", (iso(now_utc()), kind, value))


@app.route("/unsubscribe/<token>", methods=["GET", "POST"])
def unsubscribe(token):
    try:
        kind, ident = _signer().loads(token)
    except (BadSignature, ValueError, TypeError):
        abort(404)
    if kind not in ("practitioner", "client"):
        abort(404)
    done = False
    if request.method == "POST":
        with open_db() as connection:
            if kind == "practitioner":
                connection.execute("UPDATE users SET email_nudges = 0 WHERE id = ?", (int(ident),))
                cancel_nudges(connection, "practitioner_nudge", user_id=int(ident))
            else:
                connection.execute("UPDATE leads SET marketing_opt_in = 0 WHERE lower(email) = lower(?)",
                                   (str(ident),))
                cancel_nudges(connection, "client_nudge", email=str(ident))
        done = True
    return render_template("unsubscribe.html", kind=kind, done=done)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def _render(template: str, **context) -> str:
    context.setdefault("site_name", SITE_NAME)
    context.setdefault("base_url", BASE_URL)
    if has_request_context():
        return render_template(template, **context)
    with app.test_request_context("/", base_url=BASE_URL):
        return render_template(template, **context)


def _render_pair(kind: str, **context) -> tuple[str, str]:
    return _render(f"email/{kind}.txt", **context), _render(f"email/{kind}.html", **context)


def _first_name(full_name: str) -> str:
    return (full_name.split() or ["there"])[0]


def _number_items(numbers: dict) -> list[dict]:
    items = []
    for key in numerology.NUMBER_ORDER:
        title, text = numerology.meaning(numbers[key])
        items.append({"label": numerology.NUMBER_LABELS[key], "number": numbers[key], "title": title,
                      "text": text, "purpose": numerology.NUMBER_PURPOSE[key]})
    return items


def _card(practitioner) -> dict | None:
    if practitioner is None:
        return None
    card = contact_card(practitioner)
    import public_profiles  # loaded after this module
    if has_request_context():
        photo = public_profiles.avatar_url(practitioner["id"])
    else:
        with app.test_request_context("/", base_url=BASE_URL):
            photo = public_profiles.avatar_url(practitioner["id"])
    card["photo"] = BASE_URL + photo if photo else None
    card["about_url"] = f"{BASE_URL}/{practitioner['slug']}/about" if practitioner["slug"] else None
    return card


def report_email(lead, practitioner, report_url: str | None = None) -> tuple[str, str, str]:
    numbers = json.loads(lead["numbers"])
    context = {"first_name": _first_name(lead["full_name"]), "full_name": lead["full_name"],
               "items": _number_items(numbers), "life_path": numbers["life_path"],
               "report_url": report_url or f"{BASE_URL}/r/{lead['token']}", "card": _card(practitioner)}
    subject = f"Your numerology report, {context['first_name']}: Life Path {numbers['life_path']}"
    return (subject, *_render_pair("report", **context))


def lead_alert_email(lead, recipient, lead_url: str | None = None) -> tuple[str, str, str]:
    numbers = json.loads(lead["numbers"])
    context = {"first_name": _first_name(lead["full_name"]), "full_name": lead["full_name"],
               "life_path": numbers["life_path"], "recipient_name": _first_name(recipient["display_name"] or ""),
               "lead_url": lead_url or f"{BASE_URL}/leads/{lead['id']}",
               "birth_date": lead["birth_date"], "settings_url": f"{BASE_URL}/settings/profile"}
    subject = f"New lead: {context['first_name']} (Life Path {numbers['life_path']})"
    return (subject, *_render_pair("lead_alert", **context))


def practitioner_nudge_email(recipient, waiting: list) -> tuple[str, str, str, dict]:
    unsubscribe = unsubscribe_url("practitioner", recipient["id"])
    context = {"recipient_name": _first_name(recipient["display_name"] or ""), "waiting": waiting,
               "count": len(waiting), "leads_url": f"{BASE_URL}/leads", "unsubscribe_url": unsubscribe}
    noun = "lead is" if len(waiting) == 1 else "leads are"
    subject = f"{len(waiting)} {noun} waiting to hear from you"
    return (subject, *_render_pair("practitioner_nudge", **context), {"List-Unsubscribe": f"<{unsubscribe}>"})


def client_nudge_email(lead, practitioner) -> tuple[str, str, str, dict]:
    unsubscribe = unsubscribe_url("client", lead["email"])
    numbers = json.loads(lead["numbers"])
    context = {"first_name": _first_name(lead["full_name"]), "life_path": numbers["life_path"],
               "card": _card(practitioner), "report_url": f"{BASE_URL}/r/{lead['token']}",
               "unsubscribe_url": unsubscribe}
    subject = f"{context['first_name']}, take your Life Path {numbers['life_path']} reading deeper"
    return (subject, *_render_pair("client_nudge", **context), {"List-Unsubscribe": f"<{unsubscribe}>"})


# --------------------------------------------------------------------------
# Outbox
# --------------------------------------------------------------------------
def enqueue(connection, kind: str, to: str, subject: str, text: str, html: str, headers: dict | None = None,
            lead_id: int | None = None, user_id: int | None = None) -> int:
    cursor = connection.execute(
        "INSERT INTO email_outbox (kind, to_addr, subject, body_text, body_html, headers, lead_id, user_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (kind, to, subject, text, html, json.dumps(headers or {}), lead_id, user_id, iso(now_utc())))
    return cursor.lastrowid


def kick() -> None:
    if mailer.configured() and not app.config.get("TESTING"):
        threading.Thread(target=send_pending, name="email-send", daemon=True).start()


def queue_unlock_emails(lead_id: int) -> None:
    """After a visitor unlocks: their report, plus an alert to the practitioner (or admins)."""
    with open_db() as connection:
        lead = connection.execute("SELECT * FROM leads WHERE id = ? AND status = 'captured'", (lead_id,)).fetchone()
        if lead is None or not lead["email"]:
            return
        practitioner = None
        if lead["practitioner_id"]:
            practitioner = connection.execute("SELECT * FROM users WHERE id = ?", (lead["practitioner_id"],)).fetchone()
            recipients = [practitioner] if practitioner and practitioner["is_active"] else []
        else:
            recipients = connection.execute("SELECT * FROM users WHERE role = 'admin' AND is_active = 1").fetchall()
        subject, text, html = report_email(lead, practitioner if practitioner and practitioner["is_active"] else None)
        enqueue(connection, "report", lead["email"], subject, text, html, lead_id=lead["id"])
        for recipient in recipients:
            if recipient["email_alerts"]:
                subject, text, html = lead_alert_email(lead, recipient)
                enqueue(connection, "lead_alert", recipient["email"], subject, text, html,
                        lead_id=lead["id"], user_id=recipient["id"])
    kick()


def _still_wanted(connection, row) -> bool:
    """Re-check consent right before delivery, so an opt-out during a send run still wins."""
    current = connection.execute("SELECT sent_at FROM email_outbox WHERE id = ?", (row["id"],)).fetchone()
    if current is None or current["sent_at"] is not None:
        return False
    if row["kind"] == "practitioner_nudge":
        user = connection.execute("SELECT email_nudges, is_active FROM users WHERE id = ?", (row["user_id"],)).fetchone()
        return bool(user and user["email_nudges"] and user["is_active"])
    if row["kind"] == "client_nudge":
        lead = connection.execute("SELECT marketing_opt_in FROM leads WHERE id = ?", (row["lead_id"],)).fetchone()
        return bool(lead and lead["marketing_opt_in"])
    return True


def send_pending(limit: int = 50, ids: list[int] | None = None, smtp_factory=None) -> dict:
    """Send queued email. Rows are claimed atomically, so overlapping runs never double-send."""
    stats = {"sent": 0, "failed": 0, "expired": 0}
    if not mailer.configured():
        stats["skipped"] = "SMTP not configured"
        return stats
    smtp_factory = smtp_factory or mailer.connect
    stale = iso(now_utc() - dt.timedelta(minutes=5))
    connection = connect()
    server = None
    try:
        connection.execute("UPDATE email_outbox SET sent_at = ?, last_error = 'expired' WHERE sent_at IS NULL"
                           " AND created_at < ?", (iso(now_utc()), iso(now_utc() - EXPIRE_AFTER)))
        stats["expired"] = connection.execute("SELECT changes()").fetchone()[0]
        connection.commit()
        query = "SELECT * FROM email_outbox WHERE sent_at IS NULL AND (claimed_at IS NULL OR claimed_at < ?)"
        params: list = [stale]
        if ids is not None:
            query += f" AND id IN ({','.join('?' * len(ids))})"
            params += ids
        rows = connection.execute(query + " ORDER BY created_at LIMIT ?", [*params, limit]).fetchall()
        connection.commit()
        for row in rows:
            claimed = connection.execute(
                "UPDATE email_outbox SET claimed_at = ?, attempts = attempts + 1 WHERE id = ? AND sent_at IS NULL"
                " AND (claimed_at IS NULL OR claimed_at < ?)", (iso(now_utc()), row["id"], stale)).rowcount
            connection.commit()
            if not claimed:
                continue
            if not _still_wanted(connection, row):
                connection.execute("UPDATE email_outbox SET sent_at = ?, last_error = 'cancelled: unsubscribed'"
                                   " WHERE id = ? AND sent_at IS NULL", (iso(now_utc()), row["id"]))
                connection.commit()
                continue
            try:
                if server is None:
                    server = smtp_factory()
                message = mailer.build(row["to_addr"], row["subject"], row["body_text"], row["body_html"],
                                       json.loads(row["headers"] or "{}"))
                server.send_message(message)
            except Exception as exc:  # recorded and retried by the next run
                error = mailer.describe_error(exc)
                # Hold the claim so the row waits before its next try (until EXPIRE_AFTER ends retries).
                delay = min(dt.timedelta(minutes=2 ** min(row["attempts"] + 1, 10)), RETRY_MAX_DELAY)
                retry_from = now_utc() + delay - dt.timedelta(minutes=5)
                connection.execute("UPDATE email_outbox SET claimed_at = ?, last_error = ? WHERE id = ?",
                                   (iso(retry_from), error[:200], row["id"]))
                connection.commit()
                stats["failed"] += 1
                if server is not None:
                    try:
                        server.quit()
                    except Exception:
                        pass
                server = None
                continue
            connection.execute("UPDATE email_outbox SET sent_at = ?, last_error = '' WHERE id = ? AND sent_at IS NULL",
                               (iso(now_utc()), row["id"]))
            connection.commit()
            stats["sent"] += 1
        connection.execute("DELETE FROM email_outbox WHERE created_at < ?", (iso(now_utc() - dt.timedelta(days=90)),))
        connection.commit()
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                pass
        connection.close()
    return stats


# --------------------------------------------------------------------------
# Nudges (cron: send-nudges)
# --------------------------------------------------------------------------
def run_nudges(force: bool = False) -> dict:
    stats = {"practitioners": 0, "clients": 0}
    if not force and not nudges_enabled():
        stats["skipped"] = "automatic nudges are off"
        return stats
    now = now_utc()
    with open_db() as connection:
        practitioners = connection.execute(
            "SELECT * FROM users WHERE role = 'practitioner' AND is_active = 1 AND email_nudges = 1"
            " AND (nudged_at IS NULL OR nudged_at < ?)", (iso(now - PRACTITIONER_NUDGE_EVERY),)).fetchall()
        for practitioner in practitioners:
            waiting = connection.execute(
                "SELECT id, full_name, numbers, captured_at FROM leads WHERE practitioner_id = ?"
                " AND status = 'captured' AND stage = 'new' AND captured_at < ? AND captured_at > ?"
                " ORDER BY captured_at DESC LIMIT 20",
                (practitioner["id"], iso(now - LEAD_WAITING), iso(now - dt.timedelta(days=30)))).fetchall()
            if not waiting:
                continue
            rows = [{"first_name": _first_name(w["full_name"]), "life_path": json.loads(w["numbers"])["life_path"],
                     "url": f"{BASE_URL}/leads/{w['id']}"} for w in waiting]
            subject, text, html, headers = practitioner_nudge_email(practitioner, rows)
            enqueue(connection, "practitioner_nudge", practitioner["email"], subject, text, html, headers,
                    user_id=practitioner["id"])
            connection.execute("UPDATE users SET nudged_at = ? WHERE id = ?", (iso(now), practitioner["id"]))
            stats["practitioners"] += 1
        leads = connection.execute(
            "SELECT leads.* FROM leads JOIN users ON users.id = leads.practitioner_id"
            " WHERE leads.status = 'captured' AND leads.marketing_opt_in = 1 AND leads.nudged_at IS NULL"
            " AND leads.stage = 'new' AND leads.captured_at < ? AND leads.captured_at > ? AND users.is_active = 1",
            (iso(now - CLIENT_NUDGE_AFTER), iso(now - CLIENT_NUDGE_WINDOW))).fetchall()
        for lead in leads:
            practitioner = connection.execute("SELECT * FROM users WHERE id = ?", (lead["practitioner_id"],)).fetchone()
            card = contact_card(practitioner)
            if not card["booking_url"]:
                continue  # only nudge when there's somewhere to book
            subject, text, html, headers = client_nudge_email(lead, practitioner)
            enqueue(connection, "client_nudge", lead["email"], subject, text, html, headers, lead_id=lead["id"])
            connection.execute("UPDATE leads SET nudged_at = ? WHERE id = ?", (iso(now), lead["id"]))
            stats["clients"] += 1
    if stats["practitioners"] or stats["clients"]:
        kick()
    return stats


# --------------------------------------------------------------------------
# Admin: status, switches and test sends
# --------------------------------------------------------------------------
SAMPLE_NAME = "Alex Jordan Rivera"
SAMPLE_BIRTH = dt.date(1990, 7, 15)


def sample_lead() -> dict:
    numbers = numerology.core_numbers(SAMPLE_NAME, SAMPLE_BIRTH).as_dict()
    return {"id": 0, "token": "sample", "full_name": SAMPLE_NAME, "birth_date": SAMPLE_BIRTH.isoformat(),
            "email": "", "numbers": json.dumps(numbers)}


def sample_practitioner():
    row = db().execute("SELECT * FROM users WHERE role = 'practitioner' AND is_active = 1 AND slug IS NOT NULL"
                       " ORDER BY created_at LIMIT 1").fetchone()
    return row or current_user()


@app.context_processor
def _email_admin_globals():
    return {"email_kinds": KINDS}


def email_status() -> dict:
    current = mailer.settings()
    recent = db().execute("SELECT id, kind, to_addr, subject, created_at, sent_at, attempts, last_error"
                          " FROM email_outbox ORDER BY id DESC LIMIT 15").fetchall()
    return {"configured": mailer.configured(), "sender": current["sender"], "host": current["host"],
            "port": current["port"], "recent": recent, "nudges_on": nudges_enabled()}


app.jinja_env.globals["email_status"] = email_status


@app.post("/admin/email/test")
@role_required("admin")
def admin_email_test():
    admin = current_user()
    to = (request.form.get("to") or admin["email"]).strip()[:254]
    from intake import EMAIL_RE
    if not EMAIL_RE.match(to):
        flash("Enter a valid address for the test emails.", "error")
        return redirect(url_for("admin") + "#email")
    kinds = [k for k in request.form.getlist("kinds") if k in KINDS] or list(KINDS)
    if not mailer.configured():
        flash("Email isn't set up yet: add the NUMER_SMTP_* settings to .env on the server.", "error")
        return redirect(url_for("admin") + "#email")
    lead, practitioner = sample_lead(), sample_practitioner()
    built = []
    for kind in kinds:
        if kind == "report":
            built.append((kind, *report_email(lead, practitioner, report_url=f"{BASE_URL}/"), {}))
        elif kind == "lead_alert":
            built.append((kind, *lead_alert_email(lead, practitioner, lead_url=f"{BASE_URL}/leads"), {}))
        elif kind == "practitioner_nudge":
            rows = [{"first_name": "Alex", "life_path": json.loads(lead["numbers"])["life_path"], "url": f"{BASE_URL}/leads"},
                    {"first_name": "Sam", "life_path": 7, "url": f"{BASE_URL}/leads"}]
            built.append((kind, *practitioner_nudge_email(practitioner, rows)))
        else:
            built.append((kind, *client_nudge_email({**lead, "email": to}, practitioner)))
    with open_db() as connection:
        ids = [enqueue(connection, f"test:{kind}", to, f"[Test] {subject}", text, html, headers, user_id=admin["id"])
               for kind, subject, text, html, headers in built]
    result = send_pending(ids=ids, limit=len(ids))
    if result["failed"]:
        error = db().execute("SELECT last_error FROM email_outbox WHERE id = ?", (ids[0],)).fetchone()[0]
        flash(f"Sent {result['sent']} of {len(ids)} test emails to {to}. Problem: {error}", "error")
    else:
        flash(f"Sent {result['sent']} test email{'s' if result['sent'] != 1 else ''} to {to}.", "info")
    return redirect(url_for("admin") + "#email")


@app.post("/admin/email/settings")
@role_required("admin")
def admin_email_settings():
    set_setting("auto_nudges", "on" if request.form.get("auto_nudges") == "on" else "off")
    flash("Automatic nudges are " + ("on." if nudges_enabled() else "off."), "info")
    return redirect(url_for("admin") + "#email")
