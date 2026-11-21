"""Two-step sign-in for practitioners, team members and admins (authenticator app codes, RFC 6238).

- Settings → Two-step sign-in: scan a QR code with an authenticator app (Google Authenticator,
  Microsoft Authenticator, 1Password…), type the 6-digit code to turn it on. The secret is stored
  encrypted (secretbox, NUMER_CREDENTIAL_KEY) and is never shown again.
- Sign in: after the password, accounts with two-step ask for the current code. Each code works
  once; wrong codes count as failed sign-ins (same 15-minute lockout as wrong passwords).
- Lost phone: an admin turns it off for that account in Admin → Accounts, or on the server:
  flask --app app reset-two-step <email>
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

import click
import segno
from flask import abort, flash, make_response, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from app import (app, client_ip, current_user, db, iso, login_blocked, login_required, now_utc,
                 open_db, record_login_attempt, role_required, safe_next)

ISSUER = "numer.life"
STEP = 30
PENDING_SECONDS = 300


# ---------------------------------------------------------------------------- codes (RFC 6238)
def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def matching_step(secret: str, code: str, after: int = -1, now: float | None = None) -> int | None:
    """The time step the code belongs to (now ±1 step), if it is newer than `after`."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != 6:
        return None
    current = int((now if now is not None else time.time()) // STEP)
    for step in (current - 1, current, current + 1):
        if step > after and hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def otpauth_uri(secret: str, email: str) -> str:
    return (f"otpauth://totp/{quote(ISSUER)}:{quote(email)}?secret={secret}&issuer={quote(ISSUER)}"
            f"&algorithm=SHA1&digits=6&period={STEP}")


# ---------------------------------------------------------------------------- storage
def _row(user_id: int):
    return db().execute("SELECT id, email, password_hash, totp_secret, totp_enabled_at, totp_last_step"
                        " FROM users WHERE id = ? AND is_active = 1", (user_id,)).fetchone()


def enabled(user_id: int) -> bool:
    row = _row(user_id)
    return bool(row and row["totp_enabled_at"] and row["totp_secret"])


def _verify_and_use(user_id: int, code: str) -> bool:
    """Check a code for a user with two-step on; a used code (or an older one) never works again."""
    import secretbox

    row = _row(user_id)
    if row is None or not row["totp_secret"]:
        return False
    step = matching_step(secretbox.decrypt(row["totp_secret"]), code, after=row["totp_last_step"] or -1)
    if step is None:
        return False
    with open_db() as connection:
        used = connection.execute("UPDATE users SET totp_last_step = ? WHERE id = ?"
                                  " AND COALESCE(totp_last_step, -1) < ?", (step, user_id, step)).rowcount
    return used == 1


def two_step_users():
    return db().execute("SELECT id, email, display_name FROM users WHERE totp_enabled_at IS NOT NULL"
                        " ORDER BY lower(email)").fetchall()


app.jinja_env.globals["two_step_users"] = two_step_users
app.jinja_env.globals["two_step_enabled"] = enabled


# ---------------------------------------------------------------------------- sign in, step 2
def start_pending(user_id: int, next_url: str | None):
    """Called by login() after a correct password for an account with two-step on."""
    session.clear()
    session["two_step"] = {"uid": user_id, "next": next_url or "", "at": int(time.time())}
    return redirect(url_for("login_code"))


def _pending():
    pending = session.get("two_step")
    if not pending or time.time() - pending.get("at", 0) > PENDING_SECONDS:
        session.pop("two_step", None)
        return None
    return pending


@app.route("/login/code", methods=["GET", "POST"])
def login_code():
    pending = _pending()
    if pending is None:
        flash("Please sign in again.", "info")
        return redirect(url_for("login"))
    if request.method == "GET":
        return render_template("login_code.html")
    row = _row(pending["uid"])
    if row is None:
        session.clear()
        return redirect(url_for("login"))
    ip = client_ip()
    if login_blocked(ip, row["email"]):
        flash("Too many failed attempts. Please wait 15 minutes and try again.", "error")
        return render_template("login_code.html"), 429
    ok = _verify_and_use(row["id"], request.form.get("code", ""))
    record_login_attempt(ip, row["email"], ok)
    if not ok:
        flash("That code didn't work. Type the 6-digit code your authenticator app shows now.", "error")
        return render_template("login_code.html"), 401
    next_url = safe_next(pending.get("next"))
    session.clear()  # new session on login (prevents session fixation)
    session.permanent = True
    session["user_id"] = row["id"]
    with open_db() as connection:
        connection.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (iso(now_utc()), row["id"]))
    return redirect(next_url or url_for("dashboard"))


# ---------------------------------------------------------------------------- settings
def _qr_data_uri(uri: str) -> str:
    return segno.make(uri, error="m").svg_data_uri(scale=5, border=2)


@app.get("/settings/two-step")
@login_required
def two_step_settings():
    user = current_user()
    if enabled(user["id"]):
        return render_template("two_step.html", on=True)
    secret = session.get("two_step_setup") or new_secret()
    session["two_step_setup"] = secret
    uri = otpauth_uri(secret, user["email"])
    grouped = " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))
    response = make_response(render_template("two_step.html", on=False, qr=_qr_data_uri(uri), secret=grouped))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/settings/two-step/on")
@login_required
def two_step_on():
    import secretbox

    user = current_user()
    secret = session.get("two_step_setup")
    if not secret or enabled(user["id"]):
        return redirect(url_for("two_step_settings"))
    step = matching_step(secret, request.form.get("code", ""))
    if step is None:
        flash("That code didn't match. Check the time on your phone and type the code shown now.", "error")
        return redirect(url_for("two_step_settings"))
    with open_db() as connection:
        connection.execute("UPDATE users SET totp_secret = ?, totp_enabled_at = ?, totp_last_step = ? WHERE id = ?",
                           (secretbox.encrypt(secret), iso(now_utc()), step, user["id"]))
    session.pop("two_step_setup", None)
    flash("Two-step sign-in is on. Next time you sign in, have your phone ready.", "info")
    return redirect(url_for("two_step_settings"))


@app.post("/settings/two-step/off")
@login_required
def two_step_off():
    user = current_user()
    row = _row(user["id"])
    if not check_password_hash(row["password_hash"], request.form.get("password") or "") \
            or not _verify_and_use(user["id"], request.form.get("code", "")):
        flash("Password or code is incorrect.", "error")
        return redirect(url_for("two_step_settings"))
    _turn_off(user["id"])
    flash("Two-step sign-in is off.", "info")
    return redirect(url_for("two_step_settings"))


def _turn_off(user_id: int) -> int:
    with open_db() as connection:
        return connection.execute("UPDATE users SET totp_secret = NULL, totp_enabled_at = NULL,"
                                  " totp_last_step = NULL WHERE id = ?", (user_id,)).rowcount


@app.post("/admin/accounts/two-step-off")
@role_required("admin")
def admin_two_step_off():
    if not _turn_off(request.form.get("user_id", type=int) or 0):
        abort(404)
    flash("Two-step sign-in turned off for that account. They can turn it on again in Settings.", "info")
    return redirect(url_for("admin") + "#accounts")


@app.cli.command("reset-two-step")
@click.argument("email")
def reset_two_step(email):
    """Turn off two-step sign-in for an account (lost phone)."""
    from app import initialize

    initialize()
    with open_db() as connection:
        row = connection.execute("SELECT id FROM users WHERE email = ?", (email.strip(),)).fetchone()
    if row is None or not _turn_off(row["id"]):
        raise click.ClickException("No account with that email")
    click.echo("Two-step sign-in turned off")
