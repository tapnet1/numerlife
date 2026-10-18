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

import content
import numerology
from app import (
    BASE_URL,
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
    login logout manifest.webmanifest me numer numerlife pricing privacy profile qr r register
    report reports robots.txt settings share signin signout signup sitemap.xml static support
    sw.js tapnet terms unlock www
""".split())

PLATFORM_SLUGS = frozenset({"tapnet"})  # reserved for numer.life's own account, but a working link

REPORT_LINK_DAYS = 90  # covers the follow-up series (starts within 30 days, runs 21+)
SUBMIT_LIMIT_PER_HOUR = 10
UNLOCK_LIMIT_PER_HOUR = 20
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^\+?[0-9 ().-]{7,25}$")
MAX_NAME = 100
MAX_PLACE = 80

# Country names people sometimes type into "State / province" (e.g. "Warszawa, Poland" with Country
# left as Canada). Lower-case, checked case-insensitively. Names that are also states or regions
# (Georgia, England, Scotland, Wales, Puerto Rico, Hong Kong, Taiwan) are deliberately left out.
COUNTRY_NAMES = frozenset("""
    afghanistan albania algeria argentina armenia australia austria bangladesh belarus belgium bolivia
    bosnia brazil bulgaria cameroon canada chile china colombia congo croatia cuba cyprus czechia
    denmark ecuador egypt eritrea estonia ethiopia finland france germany ghana greece
    guatemala haiti honduras hungary iceland india indonesia iran iraq ireland israel italy jamaica japan
    jordan kazakhstan kenya korea latvia lebanon libya lithuania mexico moldova morocco nepal netherlands
    nicaragua nigeria norway pakistan palestine panama paraguay peru philippines poland polska portugal
    romania russia rwanda senegal serbia slovakia slovenia somalia spain sudan sweden
    switzerland syria tanzania thailand trinidad tunisia turkey uganda ukraine uruguay usa
    venezuela vietnam yemen zimbabwe españa méxico perú brasil deutschland italia
    """.split()) | {"el salvador", "costa rica", "dominican republic", "republica dominicana",
                     "united states", "united kingdom", "south africa", "south korea", "sri lanka",
                     "new zealand", "saudi arabia", "united arab emirates"}


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
        " WHERE slug = ? AND role = 'practitioner' AND is_active = 1 AND team_of IS NULL",
        (slug,),
    ).fetchone()


def referring_practitioner():
    """The practitioner whose link this visitor arrived through, if still active."""
    practitioner_id = session.get("ref_id")
    if practitioner_id is None:
        return None
    row = db().execute(
        "SELECT id, display_name, slug, team_of FROM users WHERE id = ? AND role = 'practitioner' AND is_active = 1",
        (practitioner_id,),
    ).fetchone()
    if row is not None and row["team_of"]:  # link opened before the account joined a team: credit the practitioner
        row = db().execute("SELECT id, display_name, slug, team_of FROM users WHERE id = ? AND role = 'practitioner'"
                           " AND is_active = 1 AND team_of IS NULL", (row["team_of"],)).fetchone()
        if row is not None:
            session["ref_id"] = row["id"]
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


def one_year_before(day: dt.date) -> dt.date:
    """Same calendar day a year earlier (29 February -> 28 February). A birth date after it is less
    than a year ago: usually a date picker left on today, so the form asks to confirm."""
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)


def looks_like_country(value: str) -> bool:
    return value.strip().strip(".").lower() in COUNTRY_NAMES


def parse_birth_form(form, today: dt.date | None = None, check_region: bool = True) -> tuple[dict, list[str]]:
    """Validate the public birth form. data["confirm_recent"] asks the page to show the
    "yes, this date is right" box when the birth date is within the last year."""
    today = today or dt.date.today()
    data = {
        "full_name": clean(form.get("full_name"), MAX_NAME),
        "birth_date": (form.get("birth_date") or "").strip(),
        "birth_city": clean(form.get("birth_city"), MAX_PLACE),
        "birth_region": clean(form.get("birth_region"), MAX_PLACE),
        "birth_country": clean(form.get("birth_country"), MAX_PLACE),
        "confirm_recent": False,
        "recent_ok": False,
    }
    errors = []
    if any(ch.isdigit() for ch in data["full_name"]):
        errors.append("Please enter your name only, without numbers. Your date of birth goes in its own box.")
    elif numerology.unsupported_letters(data["full_name"]):
        errors.append("Please write your name in Latin letters (A-Z, accents are fine).")
    elif not numerology.letters_of(data["full_name"]):
        errors.append("Enter your full birth name.")
    try:
        born = dt.date.fromisoformat(data["birth_date"])
    except ValueError:
        born = None
    if born is None or not dt.date(1900, 1, 1) <= born <= today:
        errors.append("Enter a valid date of birth (day, month and year).")
    elif born > one_year_before(today):
        data["confirm_recent"] = True  # keep the box on the page (ticked if it was) while other fields are fixed
        data["recent_ok"] = form.get("recent_ok") == "yes"
    if data["confirm_recent"] and not data.get("recent_ok"):
        errors.append(f"Please check your date of birth: {born.isoformat()} is less than a year ago. "
                      "If it is right (for example, a baby's report), tick the box below the date.")
    if not data["birth_city"]:
        errors.append("Enter the city you were born in.")
    if not data["birth_country"]:
        errors.append("Enter the country you were born in.")
    elif (check_region and data["birth_region"] and looks_like_country(data["birth_region"])
          and data["birth_region"].lower() != data["birth_country"].lower()):
        errors.append(f"\"{data['birth_region']}\" looks like a country. Please put the country you were born "
                      "in under Country of birth, and leave State / province empty if there isn't one.")
    return data, errors


def share_url_for(practitioner) -> str:
    """Public link to pass on: the practitioner's link if there is one, else the home page."""
    path = url_for("practitioner_link", slug=practitioner["slug"]) if practitioner else url_for("home")
    return BASE_URL + path


def render_form(practitioner, data=None, errors=None, status=200):
    return render_template("home.html", practitioner=practitioner, form=data or {},
                           errors=errors or [], share_url=share_url_for(practitioner),
                           today_iso=dt.date.today().isoformat(),
                           **content.home_context()), status


@app.get("/")
def home():
    return render_form(referring_practitioner())


@app.get("/<slug>")
def practitioner_link(slug):
    if not SLUG_RE.match(slug) or (slug in RESERVED_SLUGS and slug not in PLATFORM_SLUGS):
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
    source = "link"
    if practitioner is None:
        practitioner = next_round_robin_practitioner()
        source = "round_robin" if practitioner else "none"
        if practitioner:
            session["ref_id"] = practitioner["id"]  # later reports and shares stay with them
    connection = db()
    connection.execute(
        "INSERT INTO leads (practitioner_id, ref_slug, source, token, full_name, birth_date, birth_city,"
        " birth_region, birth_country, numbers, created_at, lang) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (practitioner["id"] if practitioner else None,
         practitioner["slug"] if practitioner and source == "link" else None, source,
         token, data["full_name"], data["birth_date"], data["birth_city"], data["birth_region"],
         data["birth_country"], json.dumps(numbers.as_dict()), iso(now_utc()),
         request.accept_languages.best_match(["en", "es"]) or "en"),
    )
    connection.commit()
    return redirect(url_for("report", token=token))


def next_round_robin_practitioner():
    """Fairly pick the next practitioner for a visitor who arrived without a link.

    Eligible: active practitioners with a personal link who haven't opted out. The one who
    waited longest since their last assignment goes next (never-assigned first). Picking and
    stamping happen in one write transaction, so parallel visitors can't get the same turn.
    """
    connection = db()
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            "SELECT id, display_name, slug FROM users WHERE role = 'practitioner' AND is_active = 1"
            " AND round_robin = 1 AND slug IS NOT NULL AND team_of IS NULL"
            " ORDER BY round_robin_at IS NOT NULL, round_robin_at, id LIMIT 1"
        ).fetchone()
        if row is not None:
            # microsecond precision: several visitors in the same second must still rotate
            connection.execute("UPDATE users SET round_robin_at = ? WHERE id = ?",
                               (now_utc().isoformat(timespec="microseconds"), row["id"]))
        connection.commit()
        return row
    except BaseException:
        connection.rollback()
        raise


def lead_for_token(token: str):
    if not token or len(token) > 64:
        abort(404)
    lead = db().execute("SELECT * FROM leads WHERE token = ?", (token,)).fetchone()
    if lead is None:
        abort(404)
    created = dt.datetime.fromisoformat(lead["captured_at"] or lead["created_at"])  # unlocking restarts the clock
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
        practitioner = db().execute("SELECT * FROM users WHERE id = ?",
                                    (lead["practitioner_id"],)).fetchone()
    first_name = lead["full_name"].split(" ")[0]
    share_from = None
    if practitioner is not None and practitioner["slug"] and practitioner["is_active"]:
        share_from = practitioner  # shares carry the practitioner's link, however the visitor arrived
    return {"lead": lead, "items": items, "practitioner": practitioner, "first_name": first_name,
            "share_url": share_url_for(share_from)}


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
    captured = connection.execute(
        "UPDATE leads SET email = ?, phone = ?, consent_report = 1, marketing_opt_in = ?,"
        " status = 'captured', captured_at = ? WHERE id = ? AND status = 'started'",
        (form["email"], form["phone"], 1 if form["marketing_opt_in"] else 0, iso(now_utc()), lead["id"]),
    ).rowcount
    connection.commit()
    if captured:
        import notify  # loaded after this module; imported here to avoid a circular import
        try:
            notify.queue_new_lead(lead["id"])
        except Exception:  # a notification problem must never break the visitor's report
            app.logger.exception("Could not queue the new-lead notification")
        import emails  # loaded after this module
        try:
            emails.queue_unlock_emails(lead["id"])
        except Exception:  # an email problem must never break the visitor's report
            app.logger.exception("Could not queue the report emails")
    return redirect(url_for("report", token=token))


@app.get("/privacy")
def privacy():
    return render_template("privacy.html")


@app.get("/sitemap.xml")
def sitemap():
    import public_profiles  # loaded after this module

    today = dt.date.today().isoformat()
    pages = [("/", "1.0"), *[(path, "0.7") for path in public_profiles.sitemap_profiles()], ("/privacy", "0.3")]
    urls = "".join(
        f"<url><loc>{BASE_URL}{path}</loc><lastmod>{today}</lastmod><priority>{priority}</priority></url>"
        for path, priority in pages
    )
    body = ('<?xml version="1.0" encoding="UTF-8"?>'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>')
    return body, 200, {"Content-Type": "application/xml; charset=utf-8"}


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

