"""Practitioner invitations: invite-only sign-up with referral tracking.

A signed-in practitioner (or admin) creates an invite link (/join/<token>). The invitee picks
their name, personal link and password; the new account records invited_by = the inviter.
Invites are single-use, expire after INVITE_DAYS and can be revoked.
"""
from __future__ import annotations

import datetime as dt
import secrets
import sqlite3

from flask import abort, flash, redirect, render_template, request, session, url_for

from app import BASE_URL, MIN_PASSWORD_LENGTH, SITE_NAME, app, create_user, current_user, db, iso, login_required, now_utc
from intake import EMAIL_RE, reserve_attempt, validate_slug

INVITE_DAYS = 14
MAX_OPEN_INVITES = 25
JOIN_LIMIT_PER_HOUR = 10


def invite_url(token: str) -> str:
    return f"{BASE_URL}/join/{token}"


def open_invite(token: str):
    if not token or len(token) > 64:
        return None
    row = db().execute(
        "SELECT invites.*, users.display_name AS inviter_name FROM invites"
        " JOIN users ON users.id = invites.inviter_id"
        " WHERE invites.token = ? AND invites.used_by IS NULL AND invites.revoked_at IS NULL"
        " AND users.is_active = 1",
        (token,),
    ).fetchone()
    if row is None or dt.datetime.fromisoformat(row["expires_at"]) < now_utc():
        return None
    return row


def referral_summary(user_id: int) -> dict:
    connection = db()
    referred = connection.execute(
        "SELECT display_name, email, slug, created_at, is_active FROM users"
        " WHERE invited_by = ? ORDER BY created_at DESC", (user_id,)
    ).fetchall()
    pending = connection.execute(
        "SELECT id, token, name, email, created_at, expires_at FROM invites"
        " WHERE inviter_id = ? AND used_by IS NULL AND revoked_at IS NULL AND expires_at > ?"
        " ORDER BY created_at DESC", (user_id, iso(now_utc()))
    ).fetchall()
    return {"referred": referred, "pending": pending}


# --------------------------------------------------------------------------
# Inviting (signed in)
# --------------------------------------------------------------------------
@app.route("/invite", methods=["GET", "POST"])
@login_required
def invite_practitioner():
    user = current_user()
    new_link = None
    errors = []
    form = {"name": "", "email": ""}
    if request.method == "POST":
        form = {"name": " ".join((request.form.get("name") or "").split())[:80],
                "email": (request.form.get("email") or "").strip()[:254]}
        if form["email"] and not EMAIL_RE.match(form["email"]):
            errors.append("Enter a valid email address, or leave it blank.")
        open_count = db().execute(
            "SELECT COUNT(*) FROM invites WHERE inviter_id = ? AND used_by IS NULL AND revoked_at IS NULL"
            " AND expires_at > ?", (user["id"], iso(now_utc()))
        ).fetchone()[0]
        if open_count >= MAX_OPEN_INVITES:
            errors.append(f"You have {open_count} open invites. Revoke some before creating more.")
        if not errors:
            token = secrets.token_urlsafe(18)
            connection = db()
            connection.execute(
                "INSERT INTO invites (token, inviter_id, name, email, created_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (token, user["id"], form["name"], form["email"], iso(now_utc()),
                 iso(now_utc() + dt.timedelta(days=INVITE_DAYS))),
            )
            connection.commit()
            new_link = invite_url(token)
            form = {"name": "", "email": ""}
    summary = referral_summary(user["id"])
    return render_template("invite.html", new_link=new_link, errors=errors, form=form,
                           invite_url=invite_url, invite_days=INVITE_DAYS, **summary), (400 if errors else 200)


@app.post("/invite/<int:invite_id>/revoke")
@login_required
def revoke_invite(invite_id):
    user = current_user()
    connection = db()
    changed = connection.execute(
        "UPDATE invites SET revoked_at = ? WHERE id = ? AND inviter_id = ? AND used_by IS NULL AND revoked_at IS NULL",
        (iso(now_utc()), invite_id, user["id"]),
    ).rowcount
    connection.commit()
    if not changed:
        abort(404)
    flash("Invite revoked. That link no longer works.", "info")
    return redirect(url_for("invite_practitioner"))


# --------------------------------------------------------------------------
# Joining (public, via invite link)
# --------------------------------------------------------------------------
@app.route("/join/<token>", methods=["GET", "POST"])
def join(token):
    invite = open_invite(token)
    if invite is None:
        return render_template("join_invalid.html"), 404
    if current_user() is not None:
        flash("You're already signed in. Sign out first to accept this invite for someone else.", "error")
        return redirect(url_for("dashboard"))
    form = {"display_name": invite["name"], "email": invite["email"], "slug": ""}
    errors: list[str] = []
    if request.method == "POST":
        form = {
            "display_name": " ".join((request.form.get("display_name") or "").split())[:80],
            "email": (request.form.get("email") or "").strip()[:254],
            "slug": (request.form.get("slug") or "").strip().lower(),
        }
        password = request.form.get("password") or ""
        if not form["display_name"]:
            errors.append("Enter the name your clients will see.")
        if not EMAIL_RE.match(form["email"]):
            errors.append("Enter a valid email address.")
        try:
            slug = validate_slug(form["slug"])
        except ValueError as exc:
            errors.append(f"Personal link: {exc}")
            slug = None
        if len(password) < MIN_PASSWORD_LENGTH:
            errors.append(f"Choose a password of at least {MIN_PASSWORD_LENGTH} characters.")
        elif password != (request.form.get("confirm_password") or ""):
            errors.append("The passwords don't match.")
        if request.form.get("accept_terms") != "yes":
            errors.append("Please accept the terms to create your account.")
        if not errors and not reserve_attempt("join", JOIN_LIMIT_PER_HOUR):
            errors.append("Too many sign-up attempts from your connection. Please try again in an hour.")
        if not errors:
            try:
                user_id = create_user(form["email"], password, "practitioner", form["display_name"],
                                      invited_by=invite["inviter_id"], slug=slug)
            except sqlite3.IntegrityError:
                errors.append("That email or personal link is already in use.")
            else:
                connection = db()
                claimed = connection.execute(
                    "UPDATE invites SET used_by = ?, used_at = ? WHERE id = ? AND used_by IS NULL AND revoked_at IS NULL",
                    (user_id, iso(now_utc()), invite["id"]),
                ).rowcount
                if not claimed:  # someone used the same link a moment earlier
                    connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
                    connection.commit()
                    return render_template("join_invalid.html"), 404
                connection.commit()
                session.clear()
                session.permanent = True
                session["user_id"] = user_id
                flash(f"Welcome to {SITE_NAME}! Your personal link is ready to share.", "info")
                return redirect(url_for("dashboard"))
    return render_template("join.html", invite=invite, form=form, errors=errors,
                           base_host=BASE_URL.split("://")[-1]), (400 if errors else 200)
