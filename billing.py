"""Payment hierarchy: plans, the referral tree and the CAD credits ledger.

- Every practitioner traces up to the platform account, numer.life/tapnet (TAPNET).
- A practitioner on a paid plan earns their direct referrer CAD $1 per month as a credit;
  with no active referrer the credit goes to TAPNET. Complimentary plans earn nothing.
- Sales (reports, bookings) split 25% TAPNET / 75% practitioner, recorded as credits.
- The ledger is append-only (database triggers refuse updates and deletes), amounts are
  integer cents in CAD, and every entry has a unique reference so a retried job or webhook
  can never record the same money twice.

PayPal checkout and payouts plug into record_subscription_payment(), record_sale() and
a future payout entry; until then an admin sets plans by hand.
"""
from __future__ import annotations

import datetime as dt
import secrets
import sqlite3

import click
from flask import abort, flash, redirect, request, url_for

from app import MIN_PASSWORD_LENGTH, app, create_user, current_user, db, iso, now_utc, open_db, role_required

CURRENCY = "CAD"
SUBSCRIPTION_CENTS = 500       # practitioner plan, per month
REFERRAL_CENTS = 100           # monthly reward per referred practitioner on a paid plan
PLATFORM_SHARE_PERCENT = 25    # TAPNET's share of report and booking sales
PLATFORM_SLUG = "tapnet"
PLANS = {
    "none": "No plan",
    "complimentary": "Complimentary",
    "active": "Paid (CAD $5/month)",
    "past_due": "Payment overdue",
    "cancelled": "Cancelled",
}
PAID_PLANS = ("active",)
KINDS = {
    "referral_credit": "Referral reward",
    "sale_share": "Sale share (75%)",
    "platform_share": "Platform share (25%)",
    "subscription_payment": "Subscription payment",
    "payout": "Payout",
    "adjustment": "Adjustment",
}


def money(cents: int | None) -> str:
    cents = cents or 0
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) // 100:,}.{abs(cents) % 100:02d}"


app.jinja_env.filters["money"] = money


# --------------------------------------------------------------------------
# Tree
# --------------------------------------------------------------------------
def platform_account(connection=None):
    """The TAPNET account, identified by an explicit marker (never by its link name alone)."""
    connection = connection or db()
    return connection.execute(
        "SELECT users.* FROM settings JOIN users ON users.id = CAST(settings.value AS INTEGER)"
        " WHERE settings.key = 'platform_user_id'").fetchone()


def is_paying(user, today: dt.date | None = None) -> bool:
    today = today or now_utc().date()
    return bool(user["is_active"] and user["plan"] in PAID_PLANS and user["paid_through"]
                and user["paid_through"] >= today.isoformat())


def referrer_id(connection, user) -> int | None:
    """Who earns this practitioner's referral reward: their active inviter, else TAPNET."""
    platform = platform_account(connection)
    if user["invited_by"] and user["invited_by"] != user["id"]:
        inviter = connection.execute("SELECT id, role, is_active FROM users WHERE id = ?",
                                     (user["invited_by"],)).fetchone()
        # Invitations sent from an admin account count as TAPNET's own.
        if inviter is not None and inviter["is_active"] and inviter["role"] != "admin":
            return inviter["id"]
    if platform is not None and platform["id"] != user["id"]:
        return platform["id"]
    return None


def upline(connection, user_id: int, limit: int = 20) -> list:
    """The chain of inviters from this user up to the top (cycle-safe)."""
    chain, seen = [], {user_id}
    row = connection.execute("SELECT id, display_name, slug, invited_by FROM users WHERE id = ?", (user_id,)).fetchone()
    while row is not None and row["invited_by"] and len(chain) < limit:
        if row["invited_by"] in seen:
            break
        seen.add(row["invited_by"])
        row = connection.execute("SELECT id, display_name, slug, invited_by FROM users WHERE id = ?",
                                 (row["invited_by"],)).fetchone()
        if row is not None:
            chain.append(row)
    return chain


# --------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------
def post(connection, user_id: int, kind: str, cents: int, reference: str, source_user_id: int | None = None,
         note: str = "", created_by: int | None = None) -> bool:
    """Add one ledger entry. Returns False (and changes nothing) if the reference already exists."""
    if kind not in KINDS:
        raise ValueError(f"unknown ledger kind {kind}")
    cursor = connection.execute(
        "INSERT OR IGNORE INTO ledger (user_id, kind, amount_cents, currency, source_user_id, reference, note,"
        " created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, kind, int(cents), CURRENCY, source_user_id, reference, note[:200], created_by, iso(now_utc())))
    return cursor.rowcount == 1


def balance(user_id: int, connection=None) -> int:
    connection = connection or db()
    return connection.execute("SELECT COALESCE(SUM(amount_cents), 0) FROM ledger WHERE user_id = ?",
                              (user_id,)).fetchone()[0]


def split(gross_cents: int) -> tuple[int, int]:
    """(platform, practitioner) shares; the practitioner keeps any odd cent."""
    platform = gross_cents * PLATFORM_SHARE_PERCENT // 100
    return platform, gross_cents - platform


def record_sale(connection, practitioner_id: int, gross_cents: int, reference: str, note: str = "") -> bool:
    """Credit a sale 75/25. Call inside the transaction that marks the payment as captured."""
    platform = platform_account(connection)
    if platform is None:
        raise RuntimeError("Set up the numer.life/tapnet platform account first")
    platform_cents, practitioner_cents = split(gross_cents)
    added = post(connection, practitioner_id, "sale_share", practitioner_cents, f"{reference}:practitioner",
                 note=note)
    post(connection, platform["id"], "platform_share", platform_cents, f"{reference}:platform",
         source_user_id=practitioner_id, note=note)
    return added


def accrue_referrals(today: dt.date | None = None) -> dict:
    """Credit referral rewards: one per paid billing period (each new paid-through date), so a
    period that spans two calendar months still earns once. Safe to run daily."""
    today = today or now_utc().date()
    stats = {"credited": 0, "already": 0, "no_referrer": 0}
    with open_db() as connection:
        paying = connection.execute(
            f"SELECT * FROM users WHERE is_active = 1 AND plan IN ({','.join('?' * len(PAID_PLANS))})"
            " AND paid_through >= ?", (*PAID_PLANS, today.isoformat())).fetchall()
        for user in paying:
            earner = referrer_id(connection, user)
            if earner is None:
                stats["no_referrer"] += 1
                continue
            period = user["paid_through"]
            if post(connection, earner, "referral_credit", REFERRAL_CENTS, f"referral:{user['id']}:{period}",
                    source_user_id=user["id"], note=f"{user['display_name']}, paid through {period}"):
                stats["credited"] += 1
            else:
                stats["already"] += 1
    return stats


def earnings_summary(user_id: int) -> dict:
    connection = db()
    referred = connection.execute(
        "SELECT COUNT(*) AS total, SUM(plan IN ('active') AND paid_through >= ?) AS paying FROM users"
        " WHERE invited_by = ? AND is_active = 1", (now_utc().date().isoformat(), user_id)).fetchone()
    user = connection.execute("SELECT plan, paid_through FROM users WHERE id = ?", (user_id,)).fetchone()
    recent = connection.execute("SELECT kind, amount_cents, note, created_at FROM ledger WHERE user_id = ?"
                                " ORDER BY id DESC LIMIT 5", (user_id,)).fetchall()
    return {"balance": balance(user_id, connection), "referred": referred["total"] or 0,
            "paying": referred["paying"] or 0, "plan": user["plan"] if user else "none",
            "plan_label": PLANS.get(user["plan"] if user else "none", ""), "recent": recent,
            "monthly": (referred["paying"] or 0) * REFERRAL_CENTS, "kinds": KINDS}


app.jinja_env.globals["earnings_summary"] = earnings_summary


# --------------------------------------------------------------------------
# Admin
# --------------------------------------------------------------------------
def billing_overview() -> dict:
    connection = db()
    users = connection.execute(
        "SELECT users.*, inviter.display_name AS inviter_name,"
        " (SELECT COALESCE(SUM(amount_cents), 0) FROM ledger WHERE ledger.user_id = users.id) AS balance_cents"
        " FROM users LEFT JOIN users AS inviter ON inviter.id = users.invited_by"
        " ORDER BY users.created_at").fetchall()
    entries = connection.execute(
        "SELECT ledger.*, users.display_name AS owner, source.display_name AS source_name FROM ledger"
        " JOIN users ON users.id = ledger.user_id LEFT JOIN users AS source ON source.id = ledger.source_user_id"
        " ORDER BY ledger.id DESC LIMIT 25").fetchall()
    platform = platform_account(connection)
    paying = sum(1 for u in users if is_paying(u))
    return {"users": users, "entries": entries, "platform": platform, "plans": PLANS, "kinds": KINDS,
            "paying": paying, "mrr": paying * SUBSCRIPTION_CENTS}


app.jinja_env.globals["billing_overview"] = billing_overview


@app.post("/admin/billing/plan")
@role_required("admin")
def admin_set_plan():
    user_id = request.form.get("user_id", type=int)
    plan = request.form.get("plan", "")
    if plan not in PLANS or not user_id:
        abort(400, description="Unknown plan.")
    paid_through = None
    if plan == "active":
        try:
            paid_through = dt.date.fromisoformat(request.form.get("paid_through") or "").isoformat()
        except ValueError:
            paid_through = (now_utc().date() + dt.timedelta(days=31)).isoformat()
    with open_db() as connection:
        changed = connection.execute("UPDATE users SET plan = ?, paid_through = ? WHERE id = ?",
                                     (plan, paid_through, user_id)).rowcount
    if not changed:
        abort(404)
    flash("Plan updated.", "info")
    return redirect(url_for("admin") + "#billing")


@app.post("/admin/billing/platform")
@role_required("admin")
def admin_create_platform():
    from intake import EMAIL_RE

    email = (request.form.get("email") or "").strip()[:254]
    password = request.form.get("password") or ""
    taken = db().execute("SELECT display_name FROM users WHERE slug = ?", (PLATFORM_SLUG,)).fetchone()
    if platform_account() is not None:
        flash("The numer.life/tapnet account already exists.", "error")
    elif taken is not None:
        flash(f"The link numer.life/{PLATFORM_SLUG} is already used by {taken['display_name']}. "
              "Change their link first.", "error")
    elif not EMAIL_RE.match(email):
        flash("Enter the email address for the TAPNET account.", "error")
    elif len(password) < MIN_PASSWORD_LENGTH:
        flash(f"Choose a password of at least {MIN_PASSWORD_LENGTH} characters for the TAPNET account.", "error")
    else:
        try:
            platform_id = create_user(email, password, "practitioner", "TAPNET",
                                      invited_by=None, slug=PLATFORM_SLUG)
        except sqlite3.IntegrityError:
            flash("That email already has an account.", "error")
        else:
            with open_db() as connection:
                connection.execute("UPDATE users SET plan = 'complimentary', round_robin = 0 WHERE id = ?",
                                   (platform_id,))
                connection.execute("INSERT INTO settings (key, value) VALUES ('platform_user_id', ?)"
                                   " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(platform_id),))
            flash("Created numer.life/tapnet. Sign in with that email to edit its profile; its credits show here.",
                  "info")
    return redirect(url_for("admin") + "#billing")


@app.post("/admin/billing/adjust")
@role_required("admin")
def admin_adjust():
    user_id = request.form.get("user_id", type=int)
    note = " ".join((request.form.get("note") or "").split())[:200]
    try:
        cents = round(float(request.form.get("amount") or "") * 100)
    except ValueError:
        cents = 0
    if not user_id or not cents or not note:
        flash("Adjustment needs an account, a non-zero amount and a note.", "error")
        return redirect(url_for("admin") + "#billing")
    with open_db() as connection:
        if connection.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
            abort(404)
        post(connection, user_id, "adjustment", cents, f"adjustment:{secrets.token_hex(8)}", note=note,
             created_by=current_user()["id"])
    flash(f"Adjustment of {money(cents)} recorded.", "info")
    return redirect(url_for("admin") + "#billing")


@app.cli.command("set-plan")
@click.option("--email", prompt=True)
@click.option("--plan", type=click.Choice(sorted(PLANS)), prompt=True)
@click.option("--paid-through", "paid_through", default="", help="YYYY-MM-DD; paid plans default to 31 days from today")
def set_plan_command(email, plan, paid_through):
    """Set a practitioner's plan (e.g. complimentary for Liliana)."""
    until = None
    if plan in PAID_PLANS:
        try:
            until = dt.date.fromisoformat(paid_through).isoformat()
        except ValueError:
            until = (now_utc().date() + dt.timedelta(days=31)).isoformat()
    with open_db() as connection:
        changed = connection.execute("UPDATE users SET plan = ?, paid_through = ? WHERE email = ?",
                                     (plan, until, email)).rowcount
    click.echo("Plan updated." if changed else "No account with that email.")
