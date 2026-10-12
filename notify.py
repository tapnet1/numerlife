"""PWA (manifest, service worker) and Web Push notifications for new leads.

Flow: when a visitor unlocks their report, queue_new_lead() writes one outbox row per
recipient (the practitioner, or every admin for leads without a practitioner) and starts a
background send. Sending happens after the request's database work is committed, so no
lock is held during network calls. jobs.py send-push (cron, every minute) retries anything
that didn't go out. Subscriptions that the push service reports gone (404/410) are deleted.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading

from flask import Response, jsonify, request, send_from_directory, url_for

import webpush
from app import BASE_DIR, SITE_NAME, app, connect, current_user, db, iso, login_required, now_utc, open_db

log = logging.getLogger("numerlife.push")
MAX_ATTEMPTS = 5
MAX_SUBSCRIPTIONS_PER_USER = 10


@app.context_processor
def _push_globals():
    return {"vapid_public_key": webpush.public_key() if current_user() else None}


# --------------------------------------------------------------------------
# PWA: manifest and service worker (served from the root so its scope is the whole site)
# --------------------------------------------------------------------------
@app.get("/manifest.webmanifest")
def manifest():
    data = {
        "name": f"{SITE_NAME}: practitioner",
        "short_name": SITE_NAME,
        "description": "Your numerology leads, wherever you are.",
        "id": "/dashboard",
        "start_url": "/dashboard?source=pwa",
        "scope": "/",
        "display": "standalone",
        "background_color": "#160d31",
        "theme_color": "#24164a",
        "icons": [
            {"src": url_for("static", filename="img/icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": url_for("static", filename="img/icon-512.png"), "sizes": "512x512", "type": "image/png"},
            {"src": url_for("static", filename="img/icon-512.png"), "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
        ],
        "shortcuts": [{"name": "Leads", "url": "/leads"}],
    }
    return Response(json.dumps(data), mimetype="application/manifest+json",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/sw.js")
def service_worker():
    response = send_from_directory(BASE_DIR / "static" / "js", "sw.js", mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


# --------------------------------------------------------------------------
# Subscriptions (signed in, JSON, CSRF via X-CSRF-Token header)
# --------------------------------------------------------------------------
@app.post("/push/subscribe")
@login_required
def push_subscribe():
    data = request.get_json(silent=True) or {}
    endpoint = str(data.get("endpoint") or "")
    keys = data.get("keys") or {}
    p256dh, auth = str(keys.get("p256dh") or ""), str(keys.get("auth") or "")
    if not webpush.endpoint_allowed(endpoint) or not (40 <= len(p256dh) <= 200) or not (8 <= len(auth) <= 64):
        return jsonify(ok=False, error="This browser's push service isn't supported."), 400
    if webpush.public_key() is None:
        return jsonify(ok=False, error="Notifications aren't set up on the server yet."), 503
    user = current_user()
    device = " ".join(str(data.get("device") or request.user_agent.string or "")[:120].split())
    connection = db()
    count = connection.execute("SELECT COUNT(*) FROM push_subscriptions WHERE user_id = ? AND endpoint != ?",
                               (user["id"], endpoint)).fetchone()[0]
    if count >= MAX_SUBSCRIPTIONS_PER_USER:  # keep the newest devices
        connection.execute(
            "DELETE FROM push_subscriptions WHERE id IN (SELECT id FROM push_subscriptions WHERE user_id = ?"
            " ORDER BY created_at LIMIT ?)", (user["id"], count - MAX_SUBSCRIPTIONS_PER_USER + 1))
    connection.execute(
        "INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, device, created_at) VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(endpoint) DO UPDATE SET user_id = excluded.user_id, p256dh = excluded.p256dh,"
        " auth = excluded.auth, device = excluded.device, failures = 0",
        (user["id"], endpoint, p256dh, auth, device, iso(now_utc())),
    )
    connection.commit()
    return jsonify(ok=True)


@app.post("/push/unsubscribe")
@login_required
def push_unsubscribe():
    endpoint = str((request.get_json(silent=True) or {}).get("endpoint") or "")
    connection = db()
    connection.execute("DELETE FROM push_subscriptions WHERE endpoint = ? AND user_id = ?",
                       (endpoint, current_user()["id"]))
    connection.commit()
    return jsonify(ok=True)


@app.post("/push/test")
@login_required
def push_test():
    user = current_user()
    has_device = db().execute("SELECT 1 FROM push_subscriptions WHERE user_id = ?", (user["id"],)).fetchone()
    if not has_device:
        return jsonify(ok=False, error="Turn on notifications on this device first."), 400
    queue([user["id"]], {"title": f"{SITE_NAME} notifications are on",
                         "body": "You'll get an alert like this for every new lead.", "url": "/dashboard"})
    return jsonify(ok=True)


# --------------------------------------------------------------------------
# Queue and send
# --------------------------------------------------------------------------
def queue(user_ids: list[int], message: dict, start_sending: bool = True) -> None:
    if not user_ids:
        return
    with open_db() as connection:
        for user_id in dict.fromkeys(user_ids):
            connection.execute("INSERT INTO push_outbox (user_id, payload, created_at) VALUES (?, ?, ?)",
                               (user_id, json.dumps(message), iso(now_utc())))
    if start_sending and not app.config.get("TESTING"):
        threading.Thread(target=send_pending, name="push-send", daemon=True).start()


def queue_new_lead(lead_id: int) -> None:
    """Called after a visitor unlocks their report (the lead row is already committed)."""
    with open_db() as connection:
        lead = connection.execute("SELECT id, practitioner_id, full_name, numbers FROM leads WHERE id = ?",
                                  (lead_id,)).fetchone()
        if lead is None:
            return
        if lead["practitioner_id"]:
            recipients = [lead["practitioner_id"]]
        else:
            recipients = [r[0] for r in connection.execute(
                "SELECT id FROM users WHERE role = 'admin' AND is_active = 1")]
    first_name = (lead["full_name"].split() or ["Someone"])[0]
    life_path = json.loads(lead["numbers"]).get("life_path")
    queue(recipients, {"title": f"New lead: {first_name}",
                       "body": f"Life Path {life_path} · unlocked their full report. Tap to see their numbers.",
                       "url": f"/leads/{lead['id']}", "tag": f"lead-{lead['id']}"})


def send_pending(limit: int = 50, post=None) -> dict:
    """Send queued notifications. Safe to run concurrently: each row is claimed atomically."""
    stats = {"sent": 0, "failed": 0, "removed": 0, "no_device": 0}
    if webpush.public_key() is None:
        return stats
    stale = iso(now_utc() - dt.timedelta(minutes=5))
    connection = connect()
    try:
        rows = connection.execute(
            "SELECT id, user_id, payload FROM push_outbox WHERE sent_at IS NULL AND attempts < ?"
            " AND (claimed_at IS NULL OR claimed_at < ?) ORDER BY created_at LIMIT ?",
            (MAX_ATTEMPTS, stale, limit)).fetchall()
        for row in rows:
            claimed = connection.execute(
                "UPDATE push_outbox SET claimed_at = ?, attempts = attempts + 1 WHERE id = ?"
                " AND sent_at IS NULL AND (claimed_at IS NULL OR claimed_at < ?)",
                (iso(now_utc()), row["id"], stale)).rowcount
            connection.commit()
            if not claimed:
                continue
            subscriptions = connection.execute(
                "SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = ?",
                (row["user_id"],)).fetchall()
            connection.commit()  # no transaction open during the network calls below
            if not subscriptions:
                connection.execute("UPDATE push_outbox SET sent_at = ?, last_error = 'no device' WHERE id = ?",
                                   (iso(now_utc()), row["id"]))
                connection.commit()
                stats["no_device"] += 1
                continue
            message = json.loads(row["payload"])
            delivered, errors, gone = False, [], []
            for sub in subscriptions:
                try:
                    status, reason = webpush.send(dict(sub), message, post=post)
                except Exception as exc:  # network failure: retry later
                    errors.append(type(exc).__name__)
                    continue
                if 200 <= status < 300:
                    delivered = True
                elif status in (404, 410):
                    gone.append(sub["id"])
                else:
                    errors.append(f"HTTP {status}")
            for sub_id in gone:
                connection.execute("DELETE FROM push_subscriptions WHERE id = ?", (sub_id,))
            stats["removed"] += len(gone)
            if delivered or (not errors):
                connection.execute("UPDATE push_outbox SET sent_at = ?, last_error = ? WHERE id = ?",
                                   (iso(now_utc()), ", ".join(errors)[:200], row["id"]))
                stats["sent" if delivered else "no_device"] += 1
            else:
                connection.execute("UPDATE push_outbox SET claimed_at = NULL, last_error = ? WHERE id = ?",
                                   (", ".join(errors)[:200], row["id"]))
                stats["failed"] += 1
            if delivered:
                connection.execute(
                    "UPDATE push_subscriptions SET last_success_at = ? WHERE user_id = ?",
                    (iso(now_utc()), row["user_id"]))
            connection.commit()
        connection.execute("DELETE FROM push_outbox WHERE created_at < ?",
                           (iso(now_utc() - dt.timedelta(days=7)),))
        connection.commit()
    finally:
        connection.close()
    return stats


def device_count(user_id: int) -> int:
    return db().execute("SELECT COUNT(*) FROM push_subscriptions WHERE user_id = ?", (user_id,)).fetchone()[0]


app.jinja_env.globals["push_device_count"] = device_count
