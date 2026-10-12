"""Public report form, practitioner links (numer.life/<slug>), teaser and email capture.

Flow: a visitor fills in the form (name, birth date, birth place) -> the core numbers are
computed and the lead is saved as 'started' -> the report page shows the Life Path with its
meaning and the other numbers locked -> the visitor gives an email (phone optional) and
consent -> the lead becomes 'captured' and the full report is shown.

Leads that arrive through numer.life/<slug> belong to that practitioner.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import secrets
import sqlite3

import click
from flask import abort, redirect, render_template, request, session, url_for

import numerology
from app import (
    app,
    client_ip,
    create_user,
    current_user,
    db,
    initialize,
    iso,
    now_utc,
    open_db,
)

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])$")
RESERVED_SLUGS = frozenset("""
    about account accounts admin administrator api app apps assets auth billing blog
    contact dashboard docs favicon.ico health help home invite invites join leads legal
    login logout manifest.webmanifest me numer numerlife pricing privacy profile r register
    report reports robots.txt settings signin signout signup sitemap.xml static support
    sw.js terms unlock www
""".split())

REPORT_LINK_DAYS = 30
SUBMIT_LIMIT_PER_HOUR = 10
UNLOCK_LIMIT_PER_HOUR = 20
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^\+?[0-9 ().-]{7,25}$")
MAX_NAME = 100
MAX_PLACE = 80


# --------------------------------------------------------------------------
# Practitioner slugs
# --------------------------------------------------------------------------
def validate_slug(slug: str) -> str:
    slug = (slug or "").strip().lower()
    if not SLUG_RE.match(slug):
        raise ValueError("Links use 2-40 lowercase letters, numbers or dashes, "
                         "starting and ending with a letter or number")
    if slug in RESERVED_SLUGS:
        raise ValueError("That link name is reserved; please pick another")
    return slug


def practitioner_by_slug(slug: str):
    return db().execute(
        "SELECT id, display_name, slug FROM users"
        " WHERE slug = ? AND role = 'practitioner' AND is_active = 1",
        (slug,),
    ).fetchone()


def referring_practitioner():
    """The practitioner whose link this visitor arrived through, if still active."""
    practitioner_id = session.get("ref_id")
    if practitioner_id is None:
        return None
    row = db().execute(
        "SELECT id, display_name, slug FROM users WHERE id = ? AND role = 'practitioner' AND is_active = 1",
        (practitioner_id,),
    ).fetchone()
    if row is None:
        session.pop("ref_id", None)
    return row


# --------------------------------------------------------------------------
# Abuse protection: per-IP limits (IPs stored only as keyed hashes) and a honeypot.
# --------------------------------------------------------------------------
def ip_hash() -> str:
    return client_ip()


def reserve_attempt(kind: str, limit: int) -> bool:
    """Count and record one attempt atomically; False if this connection is over the limit.

    BEGIN IMMEDIATE takes SQLite's write lock first, so parallel requests from the same
    address are counted one after another and can't all slip under the limit.
    """
    connection = db()
    since = iso(now_utc() - dt.timedelta(hours=1))
    connection.commit()  # finish any implicit transaction before taking the lock
    connection.execute("BEGIN IMMEDIATE")
    try:
        count = connection.execute(
            "SELECT COUNT(*) FROM form_attempts WHERE ip_hash = ? AND kind = ? AND created_at >= ?",
            (ip_hash(), kind, since),
        ).fetchone()[0]
        if count >= limit:
            connection.rollback()
            return False
        connection.execute("INSERT INTO form_attempts (ip_hash, kind, created_at) VALUES (?, ?, ?)",
                           (ip_hash(), kind, iso(now_utc())))
        connection.execute("DELETE FROM form_attempts WHERE created_at < ?",
                           (iso(now_utc() - dt.timedelta(days=1)),))
        connection.commit()
        return True
    except BaseException:
        connection.rollback()
        raise


# --------------------------------------------------------------------------
# Form handling
# --------------------------------------------------------------------------
def clean(value: str | None, limit: int) -> str:
    return " ".join((value or "").split())[:limit]


def parse_birth_form(form) -> tuple[dict, list[str]]:
    data = {
        "full_name": clean(form.get("full_name"), MAX_NAME),
        "birth_date": (form.get("birth_date") or "").strip(),
        "birth_city": clean(form.get("birth_city"), MAX_PLACE),
        "birth_region": clean(form.get("birth_region"), MAX_PLACE),
        "birth_country": clean(form.get("birth_country"), MAX_PLACE),
    }
    errors = []
    if numerology.unsupported_letters(data["full_name"]):
        errors.append("Please write your name in Latin letters (A-Z, accents are fine).")
    elif not numerology.letters_of(data["full_name"]):
        errors.append("Enter your full birth name.")
    try:
        born = dt.date.fromisoformat(data["birth_date"])
        if not dt.date(1900, 1, 1) <= born <= dt.date.today():
            raise ValueError
    except ValueError:
        errors.append("Enter a valid date of birth.")
    if not data["birth_city"]:
        errors.append("Enter the city you were born in.")
    if not data["birth_country"]:
        errors.append("Enter the country you were born in.")
    return data, errors


def render_form(practitioner, data=None, errors=None, status=200):
    return render_template("home.html", practitioner=practitioner, form=data or {},
                           errors=errors or []), status


@app.get("/")
def home():
    return render_form(referring_practitioner())


@app.get("/<slug>")
def practitioner_link(slug):
    if not SLUG_RE.match(slug) or slug in RESERVED_SLUGS:
        abort(404)
    practitioner = practitioner_by_slug(slug)
    if practitioner is None:
        # Unknown or inactive link: still offer the report, without attribution.
        session.pop("ref_id", None)
        return redirect(url_for("home"))
    session["ref_id"] = practitioner["id"]
    return render_form(practitioner)


@app.post("/report")
def start_report():
    practitioner = referring_practitioner()
    if request.form.get("website"):  # honeypot: humans never see this field
        return redirect(url_for("home"))
    data, errors = parse_birth_form(request.form)
    if errors:
        return render_form(practitioner, data, errors, 400)
    if not reserve_attempt("submit", SUBMIT_LIMIT_PER_HOUR):
        return render_form(practitioner, data, ["Too many reports from your connection. "
                                                "Please try again in an hour."], 429)
    numbers = numerology.core_numbers(data["full_name"], dt.date.fromisoformat(data["birth_date"]))
    token = secrets.token_urlsafe(24)
    connection = db()
    connection.execute(
        "INSERT INTO leads (practitioner_id, ref_slug, token, full_name, birth_date, birth_city,"
        " birth_region, birth_country, numbers, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (practitioner["id"] if practitioner else None, practitioner["slug"] if practitioner else None,
         token, data["full_name"], data["birth_date"], data["birth_city"], data["birth_region"],
         data["birth_country"], json.dumps(numbers.as_dict()), iso(now_utc())),
    )
    connection.commit()
    return redirect(url_for("report", token=token))


def lead_for_token(token: str):
    if not token or len(token) > 64:
        abort(404)
    lead = db().execute("SELECT * FROM leads WHERE token = ?", (token,)).fetchone()
    if lead is None:
        abort(404)
    created = dt.datetime.fromisoformat(lead["created_at"])
    if now_utc() - created > dt.timedelta(days=REPORT_LINK_DAYS):
        abort(404)
    return lead


def report_context(lead):
    numbers = json.loads(lead["numbers"])
    items = []
    for key in numerology.NUMBER_ORDER:
        title, text = numerology.meaning(numbers[key])
        items.append({"key": key, "label": numerology.NUMBER_LABELS[key],
                      "purpose": numerology.NUMBER_PURPOSE[key], "number": numbers[key],
                      "title": title, "text": text})
    practitioner = None
    if lead["practitioner_id"]:
        practitioner = db().execute("SELECT display_name, email FROM users WHERE id = ?",
                                    (lead["practitioner_id"],)).fetchone()
    first_name = lead["full_name"].split(" ")[0]
    return {"lead": lead, "items": items, "practitioner": practitioner, "first_name": first_name}


@app.get("/r/<token>")
def report(token):
    lead = lead_for_token(token)
    return render_template("report.html", errors=[], form={}, **report_context(lead))


@app.post("/r/<token>/unlock")
def unlock_report(token):
    lead = lead_for_token(token)
    if lead["status"] == "captured":
        return redirect(url_for("report", token=token))
    form = {
        "email": (request.form.get("email") or "").strip()[:254],
        "phone": clean(request.form.get("phone"), 25),
        "consent_report": request.form.get("consent_report") == "yes",
        "marketing_opt_in": request.form.get("marketing_opt_in") == "yes",
    }
    errors = []
    if request.form.get("website"):
        return redirect(url_for("report", token=token))
    if not EMAIL_RE.match(form["email"]):
        errors.append("Enter a valid email address.")
    if form["phone"] and not PHONE_RE.match(form["phone"]):
        errors.append("Enter a valid phone number, or leave it blank.")
    if not form["consent_report"]:
        errors.append("Please tick the box so we can send your report.")
    if errors:
        return render_template("report.html", errors=errors, form=form, **report_context(lead)), 400
    if not reserve_attempt("unlock", UNLOCK_LIMIT_PER_HOUR):
        errors = ["Too many attempts from your connection. Please try again in an hour."]
        return render_template("report.html", errors=errors, form=form, **report_context(lead)), 429
    connection = db()
    connection.execute(
        "UPDATE leads SET email = ?, phone = ?, consent_report = 1, marketing_opt_in = ?,"
        " status = 'captured', captured_at = ? WHERE id = ? AND status = 'started'",
        (form["email"], form["phone"], 1 if form["marketing_opt_in"] else 0, iso(now_utc()), lead["id"]),
    )
    connection.commit()
    return redirect(url_for("report", token=token))


@app.get("/privacy")
def privacy():
    return render_template("privacy.html")


# --------------------------------------------------------------------------
# Signed-in practitioner: their share link (leads workspace arrives next).
# --------------------------------------------------------------------------
@app.context_processor
def _share_link():
    user = current_user()
    if user is None:
        return {}
    row = db().execute("SELECT slug FROM users WHERE id = ?", (user["id"],)).fetchone()
    slug = row["slug"] if row else None
    return {"my_slug": slug}


# --------------------------------------------------------------------------
# Commands (until invitations and the profile page arrive)
# --------------------------------------------------------------------------
@app.cli.command("add-practitioner")
@click.option("--email", prompt=True)
@click.option("--name", "display_name", prompt="Display name")
@click.option("--slug", prompt="Link name (numer.life/<link name>)")
@click.password_option()
def add_practitioner_command(email, display_name, slug, password):
    """Create a practitioner account with a personal link."""
    initialize()
    try:
        slug = validate_slug(slug)
        create_user(email, password, "practitioner", display_name, slug=slug)
    except ValueError as exc:
        raise click.ClickException(str(exc))
    except sqlite3.IntegrityError:
        raise click.ClickException("That email or link name is already in use.")
    click.echo(f"Practitioner created. Their link: /{slug}")


@app.cli.command("set-slug")
@click.option("--email", prompt=True)
@click.option("--slug", prompt="Link name")
def set_slug_command(email, slug):
    """Set or change a practitioner's personal link."""
    initialize()
    try:
        slug = validate_slug(slug)
        with open_db() as connection:
            changed = connection.execute(
                "UPDATE users SET slug = ? WHERE email = ? AND role = 'practitioner'", (slug, email.strip())
            ).rowcount
    except ValueError as exc:
        raise click.ClickException(str(exc))
    except sqlite3.IntegrityError:
        raise click.ClickException("That link name is already in use.")
    if not changed:
        raise click.ClickException("No practitioner with that email.")
    click.echo(f"Link set: /{slug}")

