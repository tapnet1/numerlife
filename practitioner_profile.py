"""Practitioner profile (shown on their clients' reports), password change and share kit (QR code).

The profile lives in users.profile as JSON. Every value is validated here; links must be
https:// (or a plain social handle, which is turned into the network's https URL).
"""
from __future__ import annotations

import io
import json
import re
import sqlite3
from urllib.parse import urlsplit

import segno
from flask import Response, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from app import BASE_URL, MIN_PASSWORD_LENGTH, app, current_user, db, login_required

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^\+?[0-9 ().-]{7,25}$")
HANDLE_RE = re.compile(r"^@?([A-Za-z0-9._-]{1,60})$")

SOCIAL_NETWORKS = {
    # key: (label, URL template for a bare handle, accepted hosts)
    "instagram": ("Instagram", "https://www.instagram.com/{}", ("instagram.com",)),
    "facebook": ("Facebook", "https://www.facebook.com/{}", ("facebook.com", "fb.com")),
    "tiktok": ("TikTok", "https://www.tiktok.com/@{}", ("tiktok.com",)),
    "youtube": ("YouTube", "https://www.youtube.com/@{}", ("youtube.com", "youtu.be")),
    "x": ("X", "https://x.com/{}", ("x.com", "twitter.com")),
    "linkedin": ("LinkedIn", "https://www.linkedin.com/in/{}", ("linkedin.com",)),
}
TEXT_FIELDS = {"headline": 140, "bio": 600}
PROFILE_KEYS = ("headline", "bio", "contact_email", "phone", "website", "booking_url", *SOCIAL_NETWORKS)


def load_profile(row) -> dict:
    try:
        data = json.loads(row["profile"] or "{}")
    except (TypeError, ValueError, IndexError, KeyError):
        data = {}
    return {key: data.get(key, "") for key in PROFILE_KEYS}


def _https_url(value: str, field: str, errors: list, hosts: tuple[str, ...] | None = None) -> str:
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    parts = urlsplit(value)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or "." not in host or len(value) > 300:
        errors.append(f"{field}: enter a full web address starting with https://")
        return ""
    if hosts and not any(host == h or host.endswith("." + h) for h in hosts):
        errors.append(f"{field}: that doesn't look like a {field} link")
        return ""
    return value


def clean_profile(form) -> tuple[dict, list[str]]:
    errors: list[str] = []
    data: dict[str, str] = {}
    for key, limit in TEXT_FIELDS.items():
        data[key] = " ".join((form.get(key) or "").split())[:limit] if key == "headline" \
            else (form.get(key) or "").replace("\r\n", "\n").strip()[:limit]
    email = (form.get("contact_email") or "").strip()
    if email and not EMAIL_RE.match(email):
        errors.append("Contact email: enter a valid email address")
        email = ""
    data["contact_email"] = email
    phone = " ".join((form.get("phone") or "").split())
    if phone and not PHONE_RE.match(phone):
        errors.append("Phone: enter a valid phone number")
        phone = ""
    data["phone"] = phone
    data["website"] = _https_url((form.get("website") or "").strip(), "Website", errors)
    data["booking_url"] = _https_url((form.get("booking_url") or "").strip(), "Booking link", errors)
    for key, (label, template, hosts) in SOCIAL_NETWORKS.items():
        value = (form.get(key) or "").strip()
        looks_like_link = "/" in value or any(h in value.lower() for h in hosts)
        handle = HANDLE_RE.match(value)
        if value and not looks_like_link:
            if handle:
                data[key] = template.format(handle.group(1))
            else:
                errors.append(f"{label}: enter your handle (like @yourname) or a link to your page")
                data[key] = ""
        else:
            data[key] = _https_url(value, label, errors, hosts)
    return data, errors


def contact_card(user_row) -> dict:
    """What a practitioner shows on their clients' reports (only filled-in fields)."""
    profile = load_profile(user_row)
    socials = [{"label": SOCIAL_NETWORKS[k][0], "url": profile[k]} for k in SOCIAL_NETWORKS if profile[k]]
    keys = user_row.keys() if hasattr(user_row, "keys") else user_row
    return {
        "id": user_row["id"] if "id" in keys else None,
        "slug": user_row["slug"] if "slug" in keys else None,
        "name": user_row["display_name"],
        "headline": profile["headline"],
        "bio": profile["bio"],
        "email": profile["contact_email"] or user_row["email"],
        "phone": profile["phone"],
        "website": profile["website"],
        "booking_url": profile["booking_url"],
        "socials": socials,
    }


app.jinja_env.globals["contact_card"] = contact_card


# --------------------------------------------------------------------------
# Profile page
# --------------------------------------------------------------------------
@app.route("/settings/profile", methods=["GET", "POST"])
@login_required
def edit_profile():
    from intake import validate_slug  # imported here: intake imports this module's helpers

    user = current_user()
    row = db().execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
    profile = load_profile(row)
    form = {"display_name": row["display_name"], "slug": row["slug"] or "", "round_robin": bool(row["round_robin"]),
            "email_alerts": bool(row["email_alerts"]), "email_nudges": bool(row["email_nudges"]), **profile}
    errors: list[str] = []
    if request.method == "POST":
        profile, errors = clean_profile(request.form)
        display_name = " ".join((request.form.get("display_name") or "").split())[:80]
        if not display_name:
            errors.append("Name: enter the name clients will see")
        slug = row["slug"]
        if user["role"] == "practitioner":
            submitted = (request.form.get("slug") or "").strip().lower()
            try:
                # An unchanged link is kept as is (the platform's own reserved link included).
                slug = row["slug"] if row["slug"] and submitted == row["slug"] else validate_slug(submitted)
            except ValueError as exc:
                errors.append(f"Link name: {exc}")
        form = {"display_name": display_name, "slug": request.form.get("slug", ""),
                "round_robin": request.form.get("round_robin") == "yes",
                "email_alerts": request.form.get("email_alerts") == "yes",
                "email_nudges": request.form.get("email_nudges") == "yes", **profile}
        if not errors:
            connection = db()
            try:
                connection.execute(
                    "UPDATE users SET display_name = ?, slug = ?, profile = ?, round_robin = ?,"
                    " email_alerts = ?, email_nudges = ? WHERE id = ?",
                    (display_name, slug, json.dumps(profile),
                     1 if request.form.get("round_robin") == "yes" else 0,
                     1 if request.form.get("email_alerts") == "yes" else 0,
                     1 if request.form.get("email_nudges") == "yes" else 0, user["id"]),
                )
                if request.form.get("email_nudges") != "yes":
                    import emails  # loaded after this module
                    emails.cancel_nudges(connection, "practitioner_nudge", user_id=user["id"])
                connection.commit()
            except sqlite3.IntegrityError:
                connection.rollback()
                errors.append("Link name: that link is already taken; please pick another")
            else:
                if slug != row["slug"] and row["slug"]:
                    flash(f"Saved. Your link is now {BASE_URL.split('://')[-1]}/{slug}; "
                          f"the old link no longer works.", "info")
                else:
                    flash("Profile saved.", "info")
                return redirect(url_for("edit_profile"))
    preview = contact_card({**dict(row), "display_name": form["display_name"], "profile": json.dumps(
        {k: form.get(k, "") for k in PROFILE_KEYS})})
    return render_template("profile.html", form=form, errors=errors, preview=preview,
                           socials=SOCIAL_NETWORKS, base_host=BASE_URL.split("://")[-1]), (400 if errors else 200)


# --------------------------------------------------------------------------
# Password change
# --------------------------------------------------------------------------
@app.route("/settings/password", methods=["GET", "POST"])
@login_required
def change_password():
    user = current_user()
    errors = []
    if request.method == "POST":
        row = db().execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
        current = request.form.get("current_password") or ""
        new = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""
        if not check_password_hash(row["password_hash"], current):
            errors.append("Your current password is incorrect.")
        if len(new) < MIN_PASSWORD_LENGTH:
            errors.append(f"Your new password must be at least {MIN_PASSWORD_LENGTH} characters.")
        if new != confirm:
            errors.append("The new passwords don't match.")
        if not errors:
            connection = db()
            connection.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                               (generate_password_hash(new), user["id"]))
            connection.commit()
            user_id = session["user_id"]
            session.clear()  # fresh session after a credential change
            session["user_id"] = user_id
            session.permanent = True
            flash("Password changed.", "info")
            return redirect(url_for("dashboard"))
    return render_template("password.html", errors=errors), (400 if errors else 200)


# --------------------------------------------------------------------------
# QR code for the personal link
# --------------------------------------------------------------------------
@app.get("/share/qr.<fmt>")
@login_required
def share_qr(fmt):
    if fmt not in ("svg", "png"):
        abort(404)
    row = db().execute("SELECT slug FROM users WHERE id = ?", (current_user()["id"],)).fetchone()
    target = f"{BASE_URL}/{row['slug']}" if row and row["slug"] else f"{BASE_URL}/"
    qr = segno.make(target, error="m")
    buffer = io.BytesIO()
    if fmt == "svg":
        qr.save(buffer, kind="svg", scale=8, border=2, dark="#24164a", light="#ffffff", xmldecl=True)
        mimetype = "image/svg+xml"
    else:
        qr.save(buffer, kind="png", scale=16, border=2, dark="#24164a", light="#ffffff")
        mimetype = "image/png"
    download = request.args.get("download") == "1"
    name = f"numerlife-{row['slug'] if row and row['slug'] else 'link'}-qr.{fmt}"
    headers = {"Cache-Control": "private, max-age=300"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return Response(buffer.getvalue(), mimetype=mimetype, headers=headers)

