"""Forgot password: a practitioner (or team member, or admin) asks for a reset link by email.

The link carries a signed token: the user id plus a fingerprint of their current password
hash, valid for RESET_MINUTES. Setting a new password changes the hash, so a link works
once, and any older links stop working too. Nothing about the token is stored.

The request page always shows the same answer, whether or not the email has an account,
so it can't be used to find out who is registered.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac

from flask import abort, flash, redirect, render_template, request, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import generate_password_hash

from app import BASE_URL, MIN_PASSWORD_LENGTH, SITE_NAME, app, current_user, db, iso, now_utc, open_db
from intake import EMAIL_RE, reserve_attempt

RESET_MINUTES = 60
# Queued reset emails still unsent after this are dropped, so nobody receives a dead link.
DELIVER_WITHIN = dt.timedelta(minutes=RESET_MINUTES - 10)
REQUESTS_PER_IP_PER_HOUR = 5
EMAILS_PER_ACCOUNT_PER_HOUR = 3
RESETS_PER_IP_PER_HOUR = 10
SENT_MESSAGE = ("If that email belongs to an account, a link to choose a new password is on its way. "
                "It works for {minutes} minutes. Check your spam folder if it doesn't arrive.")


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(app.secret_key, salt="numer-password-reset")


def _fingerprint(password_hash: str) -> str:
    key = app.secret_key.encode() if isinstance(app.secret_key, str) else app.secret_key
    return hmac.new(key, password_hash.encode(), hashlib.sha256).hexdigest()[:24]


def make_token(user) -> str:
    return _serializer().dumps([user["id"], _fingerprint(user["password_hash"])])


def reset_url(user) -> str:
    return f"{BASE_URL}/login/reset/{make_token(user)}"


def user_for_token(token: str):
    """The active account a reset link belongs to, or None if it's invalid, expired or used."""
    if not token or len(token) > 300:
        return None
    try:
        user_id, fingerprint = _serializer().loads(token, max_age=RESET_MINUTES * 60)
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        return None
    row = db().execute("SELECT id, email, display_name, password_hash FROM users WHERE id = ? AND is_active = 1",
                       (user_id,)).fetchone()
    if row is None or not hmac.compare_digest(_fingerprint(row["password_hash"]), str(fingerprint)):
        return None
    return row


def reset_email(user, link: str) -> tuple[str, str, str]:
    import emails  # loaded after this module
    first = (user["display_name"] or "").split()
    context = {"first_name": first[0] if first else "", "reset_link": link, "minutes": RESET_MINUTES,
               "email": user["email"]}
    return (f"Reset your {SITE_NAME} password", *emails._render_pair("password_reset", **context))


def queue_reset_email(email: str) -> bool:
    """Queue a reset link for this address if it has an active account. False if nothing was sent."""
    import emails
    import mailer
    if not mailer.configured():
        app.logger.warning("Password reset requested but email (SMTP) is not configured")
        return False
    since = iso(now_utc() - dt.timedelta(hours=1))
    with open_db() as connection:
        user = connection.execute("SELECT id, email, display_name, password_hash FROM users"
                                  " WHERE email = ? AND is_active = 1", (email,)).fetchone()
        if user is None:
            return False
        recent = connection.execute("SELECT COUNT(*) FROM email_outbox WHERE kind = 'password_reset'"
                                    " AND user_id = ? AND created_at >= ?", (user["id"], since)).fetchone()[0]
        if recent >= EMAILS_PER_ACCOUNT_PER_HOUR:
            return False
        subject, text, html = reset_email(user, reset_url(user))
        emails.enqueue(connection, "password_reset", user["email"], subject, text, html, user_id=user["id"])
    emails.kick()
    return True


@app.route("/login/forgot", methods=["GET", "POST"])
def forgot_password():
    if current_user() is not None:
        return redirect(url_for("change_password"))
    if request.method == "GET":
        return render_template("forgot_password.html", email=request.args.get("email", "")[:254])
    email = (request.form.get("email") or "").strip()[:254]
    if not EMAIL_RE.match(email):
        flash("Enter the email address you sign in with.", "error")
        return render_template("forgot_password.html", email=email), 400
    if request.form.get("website"):  # honeypot: humans never see this field
        return render_template("forgot_password.html", sent=True, minutes=RESET_MINUTES)
    if not reserve_attempt("password-reset", REQUESTS_PER_IP_PER_HOUR):
        flash("Too many requests. Please wait an hour and try again.", "error")
        return render_template("forgot_password.html", email=email), 429
    try:
        queue_reset_email(email)
    except Exception:  # never reveal a failure that depends on whether the account exists
        app.logger.exception("Could not queue a password reset email")
    return render_template("forgot_password.html", sent=True, minutes=RESET_MINUTES)


@app.route("/login/reset/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = user_for_token(token)
    if user is None:
        return render_template("reset_password.html", invalid=True, minutes=RESET_MINUTES), 404
    errors = []
    if request.method == "POST":
        if not reserve_attempt("password-reset-set", RESETS_PER_IP_PER_HOUR):
            abort(429, description="Too many attempts. Please wait an hour and try again.")
        new = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""
        if len(new) < MIN_PASSWORD_LENGTH:
            errors.append(f"Your new password must be at least {MIN_PASSWORD_LENGTH} characters.")
        if new != confirm:
            errors.append("The new passwords don't match.")
        if not errors:
            with open_db() as connection:
                changed = connection.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ? AND password_hash = ? AND is_active = 1",
                    (generate_password_hash(new), user["id"], user["password_hash"])).rowcount
                if changed:  # failed sign-ins before the reset no longer lock the account
                    connection.execute("DELETE FROM login_attempts WHERE email = ? AND succeeded = 0",
                                       (user["email"],))
            if not changed:  # the link was used a moment ago in another tab
                return render_template("reset_password.html", invalid=True, minutes=RESET_MINUTES), 404
            flash("Your password has been changed. Sign in with your new password.", "info")
            return redirect(url_for("login", email=user["email"]))
    return render_template("reset_password.html", errors=errors, email=user["email"],
                           token=token), (400 if errors else 200)


@app.after_request
def _reset_headers(response):
    if request.endpoint in ("forgot_password", "reset_password"):
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"  # the link's token never leaks onward
    return response
