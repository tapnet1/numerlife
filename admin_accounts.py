"""Admin account tools: add a practitioner with a chosen password, reset a password,
and turn an account into a team member of a practitioner (moving its leads across).

Team members sign in with their own email and password and work on their practitioner's
leads. They have no public link or page, never receive round-robin visitors, and can't
invite or edit the practitioner's profile.
"""
from __future__ import annotations

import sqlite3

from flask import abort, flash, redirect, request, url_for
from werkzeug.security import generate_password_hash

from app import MIN_PASSWORD_LENGTH, app, create_user, current_user, iso, now_utc, open_db, role_required


def _back():
    return redirect(url_for("admin") + "#accounts")


@app.post("/admin/accounts/create")
@role_required("admin")
def admin_create_practitioner():
    from intake import EMAIL_RE, validate_slug

    name = " ".join((request.form.get("display_name") or "").split())[:80]
    email = (request.form.get("email") or "").strip()[:254]
    password = request.form.get("password") or ""
    errors = []
    if not name:
        errors.append("Enter the name clients will see.")
    if not EMAIL_RE.match(email):
        errors.append("Enter a valid email address.")
    try:
        slug = validate_slug((request.form.get("slug") or "").strip().lower())
    except ValueError as exc:
        errors.append(f"Personal link: {exc}")
        slug = None
    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f"Choose a password of at least {MIN_PASSWORD_LENGTH} characters.")
    if errors:
        for error in errors:
            flash(error, "error")
        return _back()
    try:
        user_id = create_user(email, password, "practitioner", name, invited_by=current_user()["id"], slug=slug)
    except sqlite3.IntegrityError:
        flash("That email or personal link is already in use.", "error")
        return _back()
    with open_db() as connection:  # their open invites are no longer needed
        connection.execute("UPDATE invites SET used_by = ?, used_at = ? WHERE lower(email) = lower(?)"
                           " AND used_by IS NULL AND revoked_at IS NULL", (user_id, iso(now_utc()), email))
    flash(f"Added {name}. They can sign in now with {email} and the password you chose.", "info")
    return _back()


@app.post("/admin/accounts/password")
@role_required("admin")
def admin_set_password():
    user_id = request.form.get("user_id", type=int)
    password = request.form.get("password") or ""
    if len(password) < MIN_PASSWORD_LENGTH:
        flash(f"Choose a password of at least {MIN_PASSWORD_LENGTH} characters.", "error")
        return _back()
    with open_db() as connection:
        changed = connection.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                                     (generate_password_hash(password), user_id)).rowcount
    if not changed:
        abort(404)
    flash("Password set. Share it with them privately.", "info")
    return _back()


@app.post("/admin/accounts/team")
@role_required("admin")
def admin_set_team():
    user_id = request.form.get("user_id", type=int)
    owner_id = request.form.get("owner_id", type=int)
    with open_db() as connection:
        member = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if member is None:
            abort(404)
        if not owner_id:  # remove from team
            connection.execute("UPDATE users SET team_of = NULL WHERE id = ?", (user_id,))
            flash(f"{member['display_name']} is no longer a team member.", "info")
            return _back()
        owner = connection.execute("SELECT * FROM users WHERE id = ? AND role = 'practitioner' AND team_of IS NULL"
                                   " AND is_active = 1", (owner_id,)).fetchone()
        if owner is None or owner_id == user_id or member["role"] != "practitioner":
            abort(400, description="Choose a different practitioner.")
        if connection.execute("SELECT 1 FROM users WHERE team_of = ?", (user_id,)).fetchone():
            abort(400, description="This account has its own team members; move them first.")
        moved = connection.execute("UPDATE leads SET practitioner_id = ?, updated_at = ? WHERE practitioner_id = ?",
                                   (owner_id, iso(now_utc()), user_id)).rowcount
        connection.execute("UPDATE users SET team_of = ?, slug = NULL, round_robin = 0 WHERE id = ?",
                           (owner_id, user_id))
    flash(f"{member['display_name']} is now on {owner['display_name']}'s team"
          + (f"; {moved} lead{'s' if moved != 1 else ''} moved to {owner['display_name']}." if moved else "."), "info")
    return _back()
