"""Practitioners sell through their own PayPal Business account.

The practitioner pastes the Client ID and Secret of a PayPal REST app from their own
PayPal Business account (Settings > PayPal). We check the keys with PayPal, then store them
encrypted (secretbox, NUMER_CREDENTIAL_KEY). From then on their clients pay them directly;
numer.life still confirms every payment itself with PayPal before anything is unlocked.

Until a practitioner connects PayPal, their clients pay TAPNET's PayPal account (the .env keys),
and the practitioner's 75% is credited to them as before. Sales paid to the practitioner record
TAPNET's 25% as a fee they owe (ledger kind platform_fee), billed on the monthly invoice.
"""
from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, url_for

import paypal
import secretbox
from app import app, current_user, db, iso, login_required, now_utc, open_db


def account_for_user(user) -> paypal.Account | None:
    if user is None or not user["paypal_client_id"] or not user["paypal_secret"]:
        return None
    try:
        secret = secretbox.decrypt(user["paypal_secret"])
    except Exception:  # noqa: BLE001  (wrong key on the server, damaged value)
        app.logger.error("Could not decrypt the PayPal secret of user %s", user["id"])
        return None
    env = "live" if user["paypal_env"] == "live" else "sandbox"
    return paypal.Account(f"user:{user['id']}", env, user["paypal_client_id"], secret)


def account_for_key(key: str | None) -> paypal.Account | None:
    """The account an order was created with ('tapnet' or 'user:<id>')."""
    if not key or key == "tapnet":
        return paypal.tapnet_account()
    if key.startswith("user:") and key[5:].isdigit():
        user = db().execute("SELECT * FROM users WHERE id = ?", (int(key[5:]),)).fetchone()
        return account_for_user(user)
    return None


def connected(user_id) -> bool:
    if not user_id:
        return False
    user = db().execute("SELECT * FROM users WHERE id = ? AND is_active = 1", (user_id,)).fetchone()
    return account_for_user(user) is not None


def any_connected() -> bool:
    return db().execute("SELECT 1 FROM users WHERE is_active = 1 AND paypal_client_id IS NOT NULL"
                        " AND paypal_secret IS NOT NULL LIMIT 1").fetchone() is not None


app.jinja_env.globals["paypal_connected"] = connected


def _owner():
    user = current_user()
    if user["role"] != "practitioner" or user["team_of"]:
        abort(403)  # the practitioner owns the PayPal account, not team members
    return db().execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()


@app.get("/settings/paypal")
@login_required
def paypal_settings():
    user = _owner()
    return render_template("paypal_settings.html", account=user, connected=account_for_user(user) is not None,
                           tapnet_ready=paypal.configured())


@app.post("/settings/paypal")
@login_required
def paypal_settings_save():
    user = _owner()
    if request.form.get("action") == "disconnect":
        with open_db() as connection:
            connection.execute("UPDATE users SET paypal_client_id = NULL, paypal_secret = NULL, paypal_env = NULL,"
                               " paypal_connected_at = NULL WHERE id = ?", (user["id"],))
        flash("PayPal disconnected. Until you connect it again, your clients pay TAPNET's PayPal "
              "and your share is credited to you.", "info")
        return redirect(url_for("paypal_settings"))
    client_id = (request.form.get("client_id") or "").strip()
    secret = (request.form.get("secret") or "").strip()
    env = "live" if request.form.get("env") == "live" else "sandbox"
    if not (20 <= len(client_id) <= 200 and 20 <= len(secret) <= 200) or not client_id.replace("-", "").replace("_", "").isalnum():
        flash("Paste the Client ID and Secret exactly as PayPal shows them.", "error")
        return redirect(url_for("paypal_settings"))
    account = paypal.Account(f"user:{user['id']}", env, client_id, secret)
    try:  # network call: no database lock held
        paypal.verify(account)
    except paypal.PayPalError as exc:
        app.logger.warning("PayPal keys rejected for user %s: %s (%s)", user["id"], exc, exc.status)
        if exc.status in (401, 403):
            flash(f"PayPal didn't accept those keys for {env}. Check you copied the {env.title()} keys "
                  "and chose the matching option.", "error")
        else:
            flash("We couldn't reach PayPal just now. Please try again in a few minutes.", "error")
        return redirect(url_for("paypal_settings"))
    with open_db() as connection:
        connection.execute("UPDATE users SET paypal_client_id = ?, paypal_secret = ?, paypal_env = ?,"
                           " paypal_connected_at = ? WHERE id = ?",
                           (client_id, secretbox.encrypt(secret), env, iso(now_utc()), user["id"]))
    flash(f"PayPal connected ({'live' if env == 'live' else 'sandbox: test payments only'}). "
          "Your clients now pay you directly.", "info")
    return redirect(url_for("paypal_settings"))
