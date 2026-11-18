"""Monthly invoice: TAPNET's 25% from practitioners who are paid directly into their own PayPal.

- Each practitioner's ledger balance is what TAPNET owes them (positive: 75% shares of sales
  TAPNET collected, referral rewards) minus what they owe TAPNET (negative: platform_fee,
  25% of sales paid into their own PayPal). One invoice per practitioner per month bills
  what they owe, net of what TAPNET owes them, less invoices still open. Paid-plan
  practitioners also get the CAD $5/month line (complimentary plans, like Liliana's, don't).
- Admin -> Invoices: "Prepare invoices for <last month>" makes drafts here (nothing is sent),
  then "Send" creates and sends each as a PayPal invoice from TAPNET's own PayPal account.
- The hourly job reads each invoice's status from PayPal. When it's paid, the fee part is
  recorded in the ledger (invoice_payment), so the balance comes back to zero.
- Due 7 days after sending. 7 days overdue: PayPal sends a reminder. 14 days overdue: the
  practitioner's sales pause (readings and calendar plans for their clients) until it's paid.
"""
from __future__ import annotations

import datetime as dt
import json

from flask import abort, flash, redirect, request, url_for

import billing
import paypal
from app import BASE_URL, app, current_user, db, iso, login_required, now_utc, open_db, role_required

DUE_DAYS = 7
REMIND_AFTER_DAYS = 7    # days overdue
PAUSE_AFTER_DAYS = 14    # days overdue
OPEN_STATUSES = ("sent",)


def _money(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def previous_month(today: dt.date | None = None) -> str:
    today = today or now_utc().date()
    first = today.replace(day=1)
    return (first - dt.timedelta(days=1)).strftime("%Y-%m")


def open_total(connection, user_id: int) -> int:
    return connection.execute("SELECT COALESCE(SUM(fee_cents), 0) FROM invoices WHERE user_id = ? AND status IN"
                              " ('draft', 'sent')", (user_id,)).fetchone()[0]


def amounts_for(connection, user) -> tuple[int, int]:
    """(fee owed in cents, plan cents) to bill now."""
    owed = -billing.balance(user["id"], connection) - open_total(connection, user["id"])
    plan = billing.SUBSCRIPTION_CENTS if user["plan"] in billing.PAID_PLANS else 0
    return max(owed, 0), plan


def prepare(period: str) -> int:
    """Create draft invoices for the period. Returns how many were made."""
    made = 0
    with open_db() as connection:
        platform = billing.platform_account(connection)
        users = connection.execute("SELECT * FROM users WHERE role IN ('practitioner', 'admin') AND team_of IS NULL"
                                   " AND is_active = 1").fetchall()
        for user in users:
            if platform is not None and user["id"] == platform["id"]:
                continue
            if connection.execute("SELECT 1 FROM invoices WHERE user_id = ? AND period = ?",
                                  (user["id"], period)).fetchone():
                continue
            fee, plan = amounts_for(connection, user)
            if fee + plan <= 0:
                continue
            lines = []
            if fee:
                lines.append({"name": f"numer.life platform fee (25% of your sales), up to {period}", "cents": fee})
            if plan:
                lines.append({"name": f"numer.life practitioner plan, {period}", "cents": plan})
            connection.execute(
                "INSERT INTO invoices (user_id, period, fee_cents, plan_cents, amount_cents, lines, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'draft', ?)",
                (user["id"], period, fee, plan, fee + plan, json.dumps(lines), iso(now_utc())))
            made += 1
    return made


def send(invoice_id: int) -> str:
    """Create and send one draft as a PayPal invoice from TAPNET's account. Returns an outcome word."""
    row = db().execute("SELECT invoices.*, users.email, users.display_name FROM invoices JOIN users"
                       " ON users.id = invoices.user_id WHERE invoices.id = ?", (invoice_id,)).fetchone()
    if row is None or row["status"] != "draft":
        return "not-draft"
    account = paypal.tapnet_account()
    if account is None:
        return "no-paypal"
    today = now_utc().date()
    due = today + dt.timedelta(days=DUE_DAYS)
    body = {
        "detail": {"currency_code": billing.CURRENCY, "invoice_number": f"NUMER-{row['period']}-{row['user_id']}",
                   "invoice_date": today.isoformat(), "payment_term": {"due_date": due.isoformat()},
                   "note": "Thank you for working with numer.life. Questions: reply to this invoice.",
                   "terms_and_conditions": f"Unpaid invoices pause numer.life sales {PAUSE_AFTER_DAYS} days after the due date."},
        "primary_recipients": [{"billing_info": {"email_address": row["email"],
                                                 "name": {"full_name": (row["display_name"] or "")[:100]}}}],
        "items": [{"name": line["name"][:200], "quantity": "1",
                   "unit_amount": {"currency_code": billing.CURRENCY, "value": _money(line["cents"])}}
                  for line in json.loads(row["lines"])]}
    try:  # network calls: no database lock held
        created = paypal.call("POST", "/v2/invoicing/invoices", body, account=account,
                              request_id=f"numer-invoice-{invoice_id}")
        paypal_id = created.get("id") or created.get("href", "").rstrip("/").rsplit("/", 1)[-1]
        if not paypal_id:
            raise paypal.PayPalError("PayPal returned no invoice id")
        paypal.call("POST", f"/v2/invoicing/invoices/{paypal_id}/send",
                    {"send_to_invoicer": True, "send_to_recipient": True}, account=account,
                    request_id=f"numer-invoice-send-{invoice_id}")
        details = paypal.call("GET", f"/v2/invoicing/invoices/{paypal_id}", account=account)
    except paypal.PayPalError as exc:
        app.logger.warning("PayPal invoice %s failed: %s (%s %s)", invoice_id, exc, exc.status, exc.issue)
        with open_db() as connection:
            connection.execute("UPDATE invoices SET last_error = ? WHERE id = ?",
                               (f"{exc.status or ''} {exc.issue or exc}"[:200], invoice_id))
        return "failed"
    view = ((details.get("detail") or {}).get("metadata") or {}).get("recipient_view_url", "")
    with open_db() as connection:
        connection.execute("UPDATE invoices SET status = 'sent', paypal_invoice_id = ?, paypal_status = ?,"
                           " view_url = ?, sent_at = ?, due_date = ?, last_error = NULL WHERE id = ?",
                           (paypal_id, details.get("status", "SENT"), view, iso(now_utc()), due.isoformat(),
                            invoice_id))
    return "sent"


def sync_open(today: dt.date | None = None) -> dict:
    """Hourly: read open invoices from PayPal; record payments; remind once; pause late payers."""
    today = today or now_utc().date()
    account = paypal.tapnet_account()
    result = {"checked": 0, "paid": 0, "reminded": 0}
    if account is None:
        return result
    rows = db().execute("SELECT * FROM invoices WHERE status = 'sent'").fetchall()
    for row in rows:
        result["checked"] += 1
        try:
            details = paypal.call("GET", f"/v2/invoicing/invoices/{row['paypal_invoice_id']}", account=account)
        except paypal.PayPalError as exc:
            app.logger.warning("PayPal invoice sync %s failed: %s", row["id"], exc)
            continue
        status = details.get("status", "")
        if status in ("PAID", "MARKED_AS_PAID"):
            with open_db() as connection:
                if connection.execute("UPDATE invoices SET status = 'paid', paypal_status = ?, paid_at = ?"
                                      " WHERE id = ? AND status = 'sent'", (status, iso(now_utc()), row["id"])).rowcount:
                    if row["fee_cents"]:
                        billing.post(connection, row["user_id"], "invoice_payment", row["fee_cents"],
                                     f"invoice-{row['id']}", note=f"Invoice {row['period']} paid")
                    result["paid"] += 1
            continue
        if status == "CANCELLED":
            with open_db() as connection:
                connection.execute("UPDATE invoices SET status = 'cancelled', paypal_status = ? WHERE id = ?",
                                   (status, row["id"]))
            continue
        overdue = (today - dt.date.fromisoformat(row["due_date"])).days if row["due_date"] else 0
        if overdue >= REMIND_AFTER_DAYS and not row["reminded_at"]:
            try:
                paypal.call("POST", f"/v2/invoicing/invoices/{row['paypal_invoice_id']}/remind",
                            {"send_to_invoicer": True, "send_to_recipient": True}, account=account)
                with open_db() as connection:
                    connection.execute("UPDATE invoices SET reminded_at = ? WHERE id = ?", (iso(now_utc()), row["id"]))
                result["reminded"] += 1
            except paypal.PayPalError as exc:
                app.logger.warning("PayPal invoice reminder %s failed: %s", row["id"], exc)
        with open_db() as connection:
            connection.execute("UPDATE invoices SET paypal_status = ? WHERE id = ?", (status, row["id"]))
    return result


def is_paused(user_id, today: dt.date | None = None) -> bool:
    """True while the practitioner has an invoice more than PAUSE_AFTER_DAYS overdue."""
    if not user_id:
        return False
    today = today or now_utc().date()
    limit = (today - dt.timedelta(days=PAUSE_AFTER_DAYS)).isoformat()
    return db().execute("SELECT 1 FROM invoices WHERE user_id = ? AND status = 'sent' AND due_date <= ?",
                        (user_id, limit)).fetchone() is not None


def for_user(user_id: int) -> list:
    return db().execute("SELECT * FROM invoices WHERE user_id = ? ORDER BY id DESC LIMIT 24", (user_id,)).fetchall()


def overview() -> dict:
    rows = db().execute("SELECT invoices.*, users.display_name FROM invoices JOIN users ON users.id = invoices.user_id"
                        " ORDER BY invoices.id DESC LIMIT 60").fetchall()
    return {"rows": rows, "period": previous_month(), "paypal": paypal.configured(),
            "drafts": sum(1 for r in rows if r["status"] == "draft")}


app.jinja_env.globals.update(invoices_overview=overview, invoices_for_user=for_user, invoices_paused=is_paused,
                             invoice_due_days=DUE_DAYS, invoice_pause_days=PAUSE_AFTER_DAYS)


# --------------------------------------------------------------------------
# Admin
# --------------------------------------------------------------------------
@app.post("/admin/invoices/prepare")
@role_required("admin")
def admin_prepare_invoices():
    period = request.form.get("period") or previous_month()
    try:
        dt.date.fromisoformat(f"{period}-01")
    except ValueError:
        abort(400)
    made = prepare(period)
    flash(f"{made} draft invoice{'s' if made != 1 else ''} for {period}. Check the amounts, then send."
          if made else f"Nothing to invoice for {period}: no practitioner owes TAPNET anything.", "info")
    return redirect(url_for("admin") + "#invoices")


@app.post("/admin/invoices/<int:invoice_id>/send")
@role_required("admin")
def admin_send_invoice(invoice_id):
    outcome = send(invoice_id)
    flash({"sent": "Invoice sent by PayPal.",
           "no-paypal": "Add TAPNET's PayPal keys to .env first.",
           "not-draft": "That invoice was already sent or cancelled.",
           "failed": "PayPal didn't accept the invoice. The error is shown next to it; check that Invoicing is "
                     "enabled for TAPNET's PayPal app."}[outcome], "info" if outcome == "sent" else "error")
    return redirect(url_for("admin") + "#invoices")


@app.post("/admin/invoices/<int:invoice_id>/cancel")
@role_required("admin")
def admin_cancel_invoice(invoice_id):
    with open_db() as connection:
        changed = connection.execute("UPDATE invoices SET status = 'cancelled' WHERE id = ? AND status = 'draft'",
                                     (invoice_id,)).rowcount
    flash("Draft removed." if changed else "Only drafts can be removed here; cancel a sent invoice in PayPal.",
          "info" if changed else "error")
    return redirect(url_for("admin") + "#invoices")
