"""numer.life — numerology report and lead-capture app.

Foundation: settings, database, security (sessions, CSRF, login rate limit,
security headers), authentication with admin / practitioner roles, and the
management commands used by the deploy.
"""
from __future__ import annotations

import datetime as dt
import functools
import hmac
import logging
import os
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import click
from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
log = logging.getLogger("numerlife")

# --------------------------------------------------------------------------
# Settings (all from the environment; on the server they come from .env via
# passenger_wsgi.py). Nothing secret is ever logged or shown to users.
# --------------------------------------------------------------------------
DB = os.environ.get("NUMER_DB") or str(BASE_DIR / "instance" / "numerlife.db")
BASE_URL = (os.environ.get("NUMER_BASE_URL") or "http://localhost:5000").rstrip("/")
NOINDEX = os.environ.get("NUMER_NOINDEX", "1") != "0"
SITE_NAME = "numer.life"
SITE_DESCRIPTION = (
    "Get your free personal numerology report: enter your name and birth "
    "details and receive your numbers and what they mean."
)

ROLES = ("admin", "practitioner")
LOGIN_WINDOW = dt.timedelta(minutes=15)
LOGIN_MAX_FAILURES_PER_ACCOUNT = 5
LOGIN_MAX_FAILURES_PER_IP = 20
MIN_PASSWORD_LENGTH = 12

_secret = os.environ.get("NUMER_SECRET_KEY")
if not _secret:
    # Local development only: the deploy refuses to run without the key.
    _secret = secrets.token_hex(32)
    log.warning("NUMER_SECRET_KEY is not set; using a temporary key (sessions reset on restart)")

app = Flask(__name__)
app.config.update(
    SECRET_KEY=_secret,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=BASE_URL.startswith("https://"),
    SESSION_COOKIE_NAME="numer_session",
    PERMANENT_SESSION_LIFETIME=dt.timedelta(hours=12),
    MAX_CONTENT_LENGTH=100 * 1024,  # uploads over ~120 KB hang at Passenger on this server
)

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'practitioner')),
    display_name  TEXT NOT NULL DEFAULT '',
    invited_by    INTEGER REFERENCES users(id),
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id         INTEGER PRIMARY KEY,
    ip         TEXT NOT NULL,
    email      TEXT NOT NULL COLLATE NOCASE,
    succeeded  INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_attempts_ip ON login_attempts (ip, created_at);
CREATE INDEX IF NOT EXISTS idx_login_attempts_email ON login_attempts (email, created_at);
"""

REQUIRED_TABLES = {"users", "login_attempts"}


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def connect(path: str | None = None) -> sqlite3.Connection:
    connection = sqlite3.connect(path or DB, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def open_db(path: str | None = None):
    """Connection that commits on success, rolls back on error, and always closes."""
    connection = connect(path)
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize(path: str | None = None) -> None:
    """Create missing tables. Safe to run on every deploy."""
    db_path = Path(path or DB)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with open_db(str(db_path)) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA)
    try:
        db_path.chmod(0o600)
    except OSError:
        pass


def db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def _close_db(_exc):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------
def create_user(email: str, password: str, role: str, display_name: str = "",
                invited_by: int | None = None, path: str | None = None) -> int:
    email = email.strip()
    if role not in ROLES:
        raise ValueError("Unknown role")
    if "@" not in email or len(email) > 254:
        raise ValueError("Enter a valid email address")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    with open_db(path) as connection:
        cursor = connection.execute(
            "INSERT INTO users (email, password_hash, role, display_name, invited_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (email, generate_password_hash(password), role, display_name.strip(),
             invited_by, iso(now_utc())),
        )
        return cursor.lastrowid


def current_user() -> sqlite3.Row | None:
    if "user" not in g:
        g.user = None
        user_id = session.get("user_id")
        if user_id is not None:
            row = db().execute(
                "SELECT id, email, role, display_name FROM users WHERE id = ? AND is_active = 1",
                (user_id,),
            ).fetchone()
            if row is None:
                session.clear()
            g.user = row
    return g.user


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def role_required(*roles):
    def decorator(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if user is None:
                return redirect(url_for("login", next=request.path))
            if user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


# --------------------------------------------------------------------------
# CSRF: one token per session, required on every state-changing request.
# --------------------------------------------------------------------------
def csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def _canonical_host():
    """Send www.numer.life to numer.life (Passenger serves both names)."""
    canonical = BASE_URL.split("://", 1)[-1].split("/", 1)[0]
    host = request.host.lower()
    if host == "www." + canonical and not canonical.startswith("www."):
        query = request.query_string.decode("latin-1")
        return redirect(BASE_URL + request.path + ("?" + query if query else ""), code=301)
    return None


@app.before_request
def _csrf_protect():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
    expected = session.get("csrf_token") or ""
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400, description="Your session expired. Please reload the page and try again.")


# --------------------------------------------------------------------------
# Response headers
# --------------------------------------------------------------------------
@app.after_request
def _security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'self'",
    )
    if app.config["SESSION_COOKIE_SECURE"]:
        response.headers.setdefault("Strict-Transport-Security", "max-age=86400")
    if NOINDEX:
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    if session.get("user_id") or request.endpoint == "login":
        response.headers["Cache-Control"] = "no-store"  # signed-in pages never cached
    return response


@app.context_processor
def _template_globals():
    return {
        "site_name": SITE_NAME,
        "site_description": SITE_DESCRIPTION,
        "base_url": BASE_URL,
        "noindex": NOINDEX,
        "user": current_user(),
        "year": now_utc().year,
    }


# --------------------------------------------------------------------------
# Login rate limiting
# --------------------------------------------------------------------------
def client_ip() -> str:
    return request.remote_addr or "unknown"


def login_blocked(ip: str, email: str) -> bool:
    since = iso(now_utc() - LOGIN_WINDOW)
    connection = db()
    per_account = connection.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE email = ? AND succeeded = 0 AND created_at >= ?",
        (email, since),
    ).fetchone()[0]
    per_ip = connection.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE ip = ? AND succeeded = 0 AND created_at >= ?",
        (ip, since),
    ).fetchone()[0]
    return per_account >= LOGIN_MAX_FAILURES_PER_ACCOUNT or per_ip >= LOGIN_MAX_FAILURES_PER_IP


def record_login_attempt(ip: str, email: str, succeeded: bool) -> None:
    connection = db()
    connection.execute(
        "INSERT INTO login_attempts (ip, email, succeeded, created_at) VALUES (?, ?, ?, ?)",
        (ip, email, 1 if succeeded else 0, iso(now_utc())),
    )
    # Keep the table small: attempts older than a day are no longer useful.
    connection.execute(
        "DELETE FROM login_attempts WHERE created_at < ?",
        (iso(now_utc() - dt.timedelta(days=1)),),
    )
    connection.commit()


def safe_next(target: str | None) -> str | None:
    """Only allow redirects to a path on this site."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return None


# A real hash to compare against when the email is unknown, so response time
# doesn't reveal which emails have accounts.
_DUMMY_HASH = generate_password_hash(secrets.token_hex(16))

# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@app.get("/health")
def health():
    try:
        tables = {row[0] for row in db().execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    except sqlite3.Error:
        return {"status": "error", "database": "unavailable"}, 503
    if not REQUIRED_TABLES <= tables:
        return {"status": "error", "database": "not initialized"}, 503
    return {"status": "ok"}


@app.get("/")
def home():
    return render_template("home.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if current_user() is not None:
            return redirect(url_for("dashboard"))
        return render_template("login.html", next=safe_next(request.args.get("next")))

    email = (request.form.get("email") or "").strip()[:254]
    password = request.form.get("password") or ""
    next_url = safe_next(request.form.get("next"))
    ip = client_ip()

    if login_blocked(ip, email):
        flash("Too many failed attempts. Please wait 15 minutes and try again.", "error")
        return render_template("login.html", email=email, next=next_url), 429

    row = db().execute(
        "SELECT id, password_hash FROM users WHERE email = ? AND is_active = 1", (email,)
    ).fetchone()
    valid = check_password_hash(row["password_hash"] if row else _DUMMY_HASH, password) and row is not None
    record_login_attempt(ip, email, valid)

    if not valid:
        flash("Email or password is incorrect.", "error")
        return render_template("login.html", email=email, next=next_url), 401

    session.clear()  # new session on login (prevents session fixation)
    session.permanent = True
    session["user_id"] = row["id"]
    connection = db()
    connection.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (iso(now_utc()), row["id"]))
    connection.commit()
    return redirect(next_url or url_for("dashboard"))


@app.post("/logout")
def logout():
    session.clear()
    flash("You're signed out.", "info")
    return redirect(url_for("login"))


@app.get("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html")


@app.get("/admin")
@role_required("admin")
def admin():
    users = db().execute(
        "SELECT id, email, role, display_name, is_active, created_at, last_login_at"
        " FROM users ORDER BY created_at"
    ).fetchall()
    return render_template("admin.html", users=users)


@app.get("/robots.txt")
def robots():
    if NOINDEX:
        body = "User-agent: *\nDisallow: /\n"
    else:
        body = "User-agent: *\nDisallow: /admin\nDisallow: /dashboard\nDisallow: /login\n"
    return body, 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.get("/favicon.ico")
def favicon():
    return redirect(url_for("static", filename="favicon.svg"), code=301)


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(413)
@app.errorhandler(429)
def _client_error(error):
    return render_template("error.html", code=error.code,
                           message=getattr(error, "description", "")), error.code


@app.errorhandler(500)
def _server_error(_error):
    return render_template("error.html", code=500,
                           message="Something went wrong on our side."), 500


# --------------------------------------------------------------------------
# Management commands:  flask --app app init-db | create-owner
# --------------------------------------------------------------------------
@app.cli.command("init-db")
def init_db_command():
    """Create missing database tables (safe to re-run)."""
    initialize()
    click.echo("Database initialized.")


@app.cli.command("create-owner")
@click.option("--email", prompt=True)
@click.option("--name", "display_name", prompt="Display name", default="")
@click.password_option()
def create_owner_command(email, display_name, password):
    """Create an admin account."""
    initialize()
    try:
        create_user(email, password, "admin", display_name)
    except ValueError as exc:
        raise click.ClickException(str(exc))
    except sqlite3.IntegrityError:
        raise click.ClickException("An account with that email already exists.")
    click.echo("Admin account created.")
