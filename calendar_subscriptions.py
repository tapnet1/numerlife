"""Calendar yearly plan: CAD $250 = $100, then $15 a month for 10 months (PayPal subscription).

- After the free 30 days (calendar_access), the client subscribes from their calendar. The
  subscription is made in the practitioner's own PayPal when connected, otherwise TAPNET's
  (same rule as paid readings, orders.seller_account). Subscribing during the free days starts
  billing when they end, so nothing is lost.
- One PayPal product + plan per PayPal account, created on first use and remembered.
- numer.life never trusts the browser: access and money come from PayPal's own answers, read
  on return and by the hourly sync-subscriptions job (status, next billing date, completed
  transactions). Each completed payment is credited once (ledger reference per transaction):
  paid to TAPNET -> 75% credited to the practitioner, 25% to TAPNET; paid to the practitioner ->
  TAPNET's 25% is recorded as a fee they owe (monthly invoice).
- Access runs to the next billing date while the subscription is active, and a month past the
  last payment otherwise, so a cancelled plan keeps what was paid for.
"""
from __future__ import annotations

import datetime as dt

from flask import abort, flash, redirect, request, url_for

import billing
import paypal
from app import BASE_URL, SITE_NAME, app, db, iso, now_utc, open_db

YEAR_CENTS = 25000
FIRST_CENTS = 10000
MONTHLY_CENTS = 1500
MONTHLY_CYCLES = 10
CURRENCY = "CAD"
OPEN_STATUSES = ("APPROVAL_PENDING", "APPROVED", "ACTIVE", "SUSPENDED")

T = {
    "en": {"subscribe": "Subscribe: CAD $250 a year",
           "terms_now": "$100 today, then $15 a month for 10 months. Cancel any time in PayPal.",
           "terms_later": "Nothing is charged until {date}. Then $100, and $15 a month for 10 months. Cancel any time in PayPal.",
           "banner": "Your free month runs until {date}. Keep your daily guidance and your journal for the full year.",
           "thanks": "Thank you! Your full year is unlocked.",
           "pending": "Thank you! PayPal is confirming your subscription; your calendar unlocks in a few minutes.",
           "failed": "We couldn't start the subscription with PayPal. You haven't been charged. Please try again.",
           "unavailable": "Subscriptions aren't available right now. Please contact {who}.",
           "subscribed": "Subscribed · your calendar is open until {date}"},
    "es": {"subscribe": "Suscribirme: CAD $250 al año",
           "terms_now": "$100 hoy y luego $15 al mes durante 10 meses. Cancela cuando quieras en PayPal.",
           "terms_later": "No se cobra nada hasta el {date}. Luego $100 y $15 al mes durante 10 meses. Cancela cuando quieras en PayPal.",
           "banner": "Tu mes gratis dura hasta el {date}. Conserva tu guía diaria y tu diario durante todo el año.",
           "thanks": "¡Gracias! Tu año completo está desbloqueado.",
           "pending": "¡Gracias! PayPal está confirmando tu suscripción; tu calendario se desbloquea en unos minutos.",
           "failed": "No pudimos iniciar la suscripción con PayPal. No se te ha cobrado. Inténtalo de nuevo.",
           "unavailable": "Las suscripciones no están disponibles ahora. Contacta a {who}.",
           "subscribed": "Suscrito · tu calendario está abierto hasta el {date}"},
    "fr": {"subscribe": "M'abonner : 250 $ CA par an",
           "terms_now": "100 $ aujourd'hui, puis 15 $ par mois pendant 10 mois. Annulez à tout moment dans PayPal.",
           "terms_later": "Rien n'est facturé avant le {date}. Ensuite 100 $, puis 15 $ par mois pendant 10 mois. Annulez à tout moment dans PayPal.",
           "banner": "Votre mois gratuit dure jusqu'au {date}. Gardez vos conseils quotidiens et votre journal toute l'année.",
           "thanks": "Merci ! Votre année complète est débloquée.",
           "pending": "Merci ! PayPal confirme votre abonnement ; votre calendrier se débloque dans quelques minutes.",
           "failed": "Nous n'avons pas pu démarrer l'abonnement avec PayPal. Rien n'a été facturé. Réessayez.",
           "unavailable": "Les abonnements ne sont pas disponibles pour le moment. Contactez {who}.",
           "subscribed": "Abonné · votre calendrier est ouvert jusqu'au {date}"},
    "pt": {"subscribe": "Assinar: CAD $250 por ano",
           "terms_now": "$100 hoje e depois $15 por mês durante 10 meses. Cancele quando quiser no PayPal.",
           "terms_later": "Nada é cobrado até {date}. Depois $100 e $15 por mês durante 10 meses. Cancele quando quiser no PayPal.",
           "banner": "Seu mês grátis vai até {date}. Mantenha sua orientação diária e seu diário durante o ano todo.",
           "thanks": "Obrigado! Seu ano completo está desbloqueado.",
           "pending": "Obrigado! O PayPal está confirmando sua assinatura; seu calendário será desbloqueado em alguns minutos.",
           "failed": "Não conseguimos iniciar a assinatura com o PayPal. Nada foi cobrado. Tente de novo.",
           "unavailable": "As assinaturas não estão disponíveis agora. Fale com {who}.",
           "subscribed": "Assinante · seu calendário está aberto até {date}"},
    "it": {"subscribe": "Abbonati: CAD $250 all'anno",
           "terms_now": "$100 oggi, poi $15 al mese per 10 mesi. Annulla quando vuoi su PayPal.",
           "terms_later": "Nessun addebito fino al {date}. Poi $100 e $15 al mese per 10 mesi. Annulla quando vuoi su PayPal.",
           "banner": "Il tuo mese gratuito dura fino al {date}. Mantieni la guida quotidiana e il tuo diario per tutto l'anno.",
           "thanks": "Grazie! Il tuo anno completo è sbloccato.",
           "pending": "Grazie! PayPal sta confermando l'abbonamento; il calendario si sblocca tra pochi minuti.",
           "failed": "Non siamo riusciti ad avviare l'abbonamento con PayPal. Nessun addebito. Riprova.",
           "unavailable": "Gli abbonamenti non sono disponibili ora. Contatta {who}.",
           "subscribed": "Abbonato · il tuo calendario è aperto fino al {date}"},
    "de": {"subscribe": "Abonnieren: 250 CAD pro Jahr",
           "terms_now": "Heute 100 $, dann 15 $ monatlich für 10 Monate. Jederzeit in PayPal kündbar.",
           "terms_later": "Bis zum {date} wird nichts abgebucht. Dann 100 $ und 15 $ monatlich für 10 Monate. Jederzeit in PayPal kündbar.",
           "banner": "Dein kostenloser Monat läuft bis zum {date}. Behalte deine täglichen Impulse und dein Tagebuch das ganze Jahr.",
           "thanks": "Danke! Dein volles Jahr ist freigeschaltet.",
           "pending": "Danke! PayPal bestätigt dein Abo; dein Kalender wird in wenigen Minuten freigeschaltet.",
           "failed": "Wir konnten das Abo bei PayPal nicht starten. Es wurde nichts abgebucht. Bitte versuch es erneut.",
           "unavailable": "Abos sind gerade nicht verfügbar. Bitte kontaktiere {who}.",
           "subscribed": "Abonniert · dein Kalender ist bis zum {date} offen"},
}


def labels(lang: str) -> dict:
    return T.get(lang) or T["en"]


def _money(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


# --------------------------------------------------------------------------
# PayPal product and plan (one per account, created on first use)
# --------------------------------------------------------------------------
def plan_id_for(account: paypal.Account) -> str:
    row = db().execute("SELECT plan_id FROM paypal_plans WHERE account_key = ? AND env = ? AND client_id = ?",
                       (account.key, account.env, account.client_id)).fetchone()
    if row is not None:
        return row["plan_id"]
    product = paypal.call("POST", "/v1/catalogs/products", {
        "name": f"{SITE_NAME} personal calendar", "type": "SERVICE", "category": "SOFTWARE",
        "description": "Personal numerology calendar with daily guidance and journal (yearly plan)"},
        account=account, request_id=f"numer-product-{account.key}-{account.env}")
    plan = paypal.call("POST", "/v1/billing/plans", {
        "product_id": product["id"], "name": "Personal calendar: full year", "status": "ACTIVE",
        "description": "CAD $250 a year: $100, then $15 a month for 10 months",
        "billing_cycles": [
            {"frequency": {"interval_unit": "MONTH", "interval_count": 1}, "tenure_type": "TRIAL", "sequence": 1,
             "total_cycles": 1, "pricing_scheme": {"fixed_price": {"value": _money(FIRST_CENTS), "currency_code": CURRENCY}}},
            {"frequency": {"interval_unit": "MONTH", "interval_count": 1}, "tenure_type": "REGULAR", "sequence": 2,
             "total_cycles": MONTHLY_CYCLES,
             "pricing_scheme": {"fixed_price": {"value": _money(MONTHLY_CENTS), "currency_code": CURRENCY}}}],
        "payment_preferences": {"auto_bill_outstanding": True, "payment_failure_threshold": 2}},
        account=account, request_id=f"numer-plan-{account.key}-{account.env}")
    with open_db() as connection:
        connection.execute("INSERT OR IGNORE INTO paypal_plans (account_key, env, client_id, product_id, plan_id,"
                           " created_at) VALUES (?, ?, ?, ?, ?, ?)",
                           (account.key, account.env, account.client_id, product["id"], plan["id"], iso(now_utc())))
    return plan["id"]


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------
def current(calendar_id: int):
    """The calendar's latest subscription that is still open (or None)."""
    marks = ",".join("?" * len(OPEN_STATUSES))
    return db().execute(f"SELECT * FROM calendar_subscriptions WHERE calendar_id = ? AND status IN ({marks})"
                        " ORDER BY id DESC LIMIT 1", (calendar_id, *OPEN_STATUSES)).fetchone()


def latest(calendar_id: int):
    return db().execute("SELECT * FROM calendar_subscriptions WHERE calendar_id = ? AND status != 'CREATED'"
                        " ORDER BY id DESC LIMIT 1", (calendar_id,)).fetchone()


def can_subscribe(lead) -> bool:
    import orders
    return orders.seller_account(lead) is not None


app.jinja_env.globals.update(calendar_sub_labels=labels, calendar_subscription=current,
                             calendar_subscription_latest=latest, calendar_can_subscribe=can_subscribe)


# --------------------------------------------------------------------------
# Client: subscribe and come back
# --------------------------------------------------------------------------
def _calendar(token: str):
    import calendars
    return calendars._public_calendar(token)


def _start_time(cal) -> str | None:
    """Start billing when the free days end (at least a day ahead), otherwise now."""
    import calendar_access
    until = calendar_access.access_until(cal)
    tomorrow = now_utc().date() + dt.timedelta(days=1)
    if until < tomorrow:
        return None
    return f"{until.isoformat()}T18:00:00Z"


@app.post("/c/<token>/subscribe")
def calendar_subscribe(token):
    import orders
    cal, lead = _calendar(token)
    t = labels(cal["lang"])
    back = url_for("calendar_public", token=token)
    if current(cal["id"]) is not None and current(cal["id"])["status"] in ("ACTIVE", "APPROVED"):
        return redirect(back)
    account = orders.seller_account(lead)
    if account is None:
        flash(t["unavailable"].format(who="your numerologist"), "error")
        return redirect(back)
    now = iso(now_utc())
    with open_db() as connection:
        sub_id = connection.execute(
            "INSERT INTO calendar_subscriptions (calendar_id, lead_id, practitioner_id, paypal_account, status,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, 'CREATED', ?, ?)",
            (cal["id"], lead["id"], lead["practitioner_id"], account.key, now, now)).lastrowid
    try:  # network calls: no database lock held
        body = {"plan_id": plan_id_for(account), "custom_id": f"calsub-{sub_id}",
                "application_context": {"brand_name": SITE_NAME[:127], "shipping_preference": "NO_SHIPPING",
                                        "user_action": "SUBSCRIBE_NOW",
                                        "return_url": f"{BASE_URL}/paypal/subscription/return",
                                        "cancel_url": f"{BASE_URL}/c/{token}"}}
        start = _start_time(cal)
        if start:
            body["start_time"] = start
        result = paypal.call("POST", "/v1/billing/subscriptions", body, account=account,
                             request_id=f"numer-calsub-{sub_id}")
        link = paypal.approve_link(result)
    except paypal.PayPalError as exc:
        app.logger.warning("PayPal subscription create failed: %s (%s %s)", exc, exc.status, exc.issue)
        with open_db() as connection:
            connection.execute("UPDATE calendar_subscriptions SET status = 'FAILED', updated_at = ? WHERE id = ?",
                               (iso(now_utc()), sub_id))
        flash(t["failed"], "error")
        return redirect(back)
    with open_db() as connection:
        connection.execute("UPDATE calendar_subscriptions SET paypal_subscription_id = ?, status = 'APPROVAL_PENDING',"
                           " updated_at = ? WHERE id = ?", (result["id"], iso(now_utc()), sub_id))
    return redirect(link)


@app.get("/paypal/subscription/return")
def calendar_subscription_return():
    paypal_id = (request.args.get("subscription_id") or "")[:64]
    row = db().execute("SELECT * FROM calendar_subscriptions WHERE paypal_subscription_id = ?",
                       (paypal_id,)).fetchone()
    if row is None:
        abort(404)
    cal = db().execute("SELECT * FROM calendars WHERE id = ?", (row["calendar_id"],)).fetchone()
    t = labels(cal["lang"])
    status = sync(row["id"])
    if status in ("ACTIVE", "APPROVED"):
        _alert_staff(row["id"])
        flash(t["thanks"], "info")
    else:
        flash(t["pending"], "info")
    return redirect(url_for("calendar_public", token=cal["token"]))


# --------------------------------------------------------------------------
# Sync with PayPal (return and hourly job)
# --------------------------------------------------------------------------
def _date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def sync(sub_id: int) -> str | None:
    """Refresh one subscription from PayPal; extend access and credit new payments. Returns the status."""
    import practitioner_paypal
    row = db().execute("SELECT * FROM calendar_subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if row is None or not row["paypal_subscription_id"]:
        return None
    account = practitioner_paypal.account_for_key(row["paypal_account"])
    if account is None:
        app.logger.error("Calendar subscription %s: PayPal account %s not connected", sub_id, row["paypal_account"])
        return row["status"]
    try:  # network calls: no database lock held
        details = paypal.call("GET", f"/v1/billing/subscriptions/{row['paypal_subscription_id']}", account=account)
        start = (_date(row["created_at"]) or now_utc().date()) - dt.timedelta(days=2)
        end = now_utc().date() + dt.timedelta(days=1)
        transactions = paypal.call(
            "GET", f"/v1/billing/subscriptions/{row['paypal_subscription_id']}/transactions"
                   f"?start_time={start.isoformat()}T00:00:00Z&end_time={end.isoformat()}T00:00:00Z",
            account=account).get("transactions", []) if details.get("status") != "APPROVAL_PENDING" else []
    except paypal.PayPalError as exc:
        app.logger.warning("PayPal subscription sync failed for %s: %s (%s)", sub_id, exc, exc.status)
        return row["status"]
    if details.get("custom_id") != f"calsub-{sub_id}":
        app.logger.error("Calendar subscription %s: PayPal custom_id mismatch", sub_id)
        return row["status"]
    status = details.get("status") or row["status"]
    info = details.get("billing_info") or {}
    next_date = _date(info.get("next_billing_time"))
    last_date = _date((info.get("last_payment") or {}).get("time"))
    with open_db() as connection:
        cal = connection.execute("SELECT * FROM calendars WHERE id = ?", (row["calendar_id"],)).fetchone()
        import calendar_access
        until = calendar_access.access_until(cal)
        candidates = [until]
        if last_date:
            candidates.append(last_date + dt.timedelta(days=31))
        if status == "ACTIVE" and next_date and last_date:
            candidates.append(next_date + dt.timedelta(days=3))  # a few days' grace for the next payment
        new_until = max(candidates)
        if new_until != until:
            connection.execute("UPDATE calendars SET access_until = ? WHERE id = ?", (new_until.isoformat(), cal["id"]))
        paid = 0
        for txn in transactions:
            if txn.get("status") != "COMPLETED":
                continue
            gross = (txn.get("amount_with_breakdown") or {}).get("gross_amount") or {}
            if gross.get("currency_code") != CURRENCY:
                continue
            cents = round(float(gross.get("value", "0")) * 100)
            if cents > 0 and _credit(connection, row, txn["id"], cents):
                paid += cents
        connection.execute("UPDATE calendar_subscriptions SET status = ?, paid_cents = paid_cents + ?, updated_at = ?,"
                           " next_billing = ? WHERE id = ?",
                           (status, paid, iso(now_utc()), next_date.isoformat() if next_date else None, sub_id))
    return status


def _credit(connection, row, txn_id: str, cents: int) -> bool:
    """Record one completed payment once. Returns True if it was new."""
    reference = f"calsub-{row['id']}-{txn_id}"
    note = f"Calendar subscription {row['id']}, payment {txn_id}"
    platform_cents, practitioner_cents = billing.split(cents)
    if (row["paypal_account"] or "").startswith("user:") and row["practitioner_id"]:
        return billing.post(connection, row["practitioner_id"], "platform_fee", -platform_cents, f"{reference}:fee",
                            note=note)
    platform = billing.platform_account(connection)
    if platform is None:
        app.logger.error("Calendar payment %s: no TAPNET account to credit", reference)
        return False
    new = billing.post(connection, platform["id"], "platform_share",
                       cents if not row["practitioner_id"] else platform_cents, f"{reference}:platform", note=note)
    if row["practitioner_id"]:
        billing.post(connection, row["practitioner_id"], "sale_share", practitioner_cents, f"{reference}:practitioner",
                     note=note)
    return new


def _alert_staff(sub_id: int) -> None:
    import emails
    import notify
    import orders
    row = db().execute("SELECT * FROM calendar_subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if row is None or row["alerted_at"]:
        return
    with open_db() as connection:
        if connection.execute("UPDATE calendar_subscriptions SET alerted_at = ? WHERE id = ? AND alerted_at IS NULL",
                              (iso(now_utc()), sub_id)).rowcount != 1:
            return
        lead = connection.execute("SELECT * FROM leads WHERE id = ?", (row["lead_id"],)).fetchone()
        recipients = orders._staff_recipients(connection, row["practitioner_id"])
        for recipient in recipients:
            if recipient["email_alerts"]:
                text = (f"{lead['full_name']} subscribed to their personal calendar for the full year "
                        f"(CAD $250: $100, then $15 a month for 10 months).\n\n{BASE_URL}/leads/{lead['id']}#calendar\n")
                html = (f"<p><strong>{lead['full_name']}</strong> subscribed to their personal calendar for the full "
                        f"year (CAD $250: $100, then $15 a month for 10 months).</p>"
                        f"<p><a href=\"{BASE_URL}/leads/{lead['id']}#calendar\">Open their lead page</a></p>")
                emails.enqueue(connection, "calendar_subscribed", recipient["email"],
                               f"Calendar subscription: {lead['full_name']}", text, html,
                               lead_id=lead["id"], user_id=recipient["id"])
    emails.kick()
    notify.queue([r["id"] for r in recipients], {
        "title": "New calendar subscription", "body": f"{lead['full_name']} subscribed for the full year.",
        "url": f"/leads/{lead['id']}#calendar", "tag": f"calsub-{lead['id']}"})


def sync_open() -> dict:
    """Hourly job: refresh every open subscription (and give up on checkouts left for 3 days)."""
    cutoff = iso(now_utc() - dt.timedelta(days=3))
    with open_db() as connection:
        connection.execute("UPDATE calendar_subscriptions SET status = 'ABANDONED', updated_at = ?"
                           " WHERE status IN ('CREATED', 'APPROVAL_PENDING') AND created_at < ?",
                           (iso(now_utc()), cutoff))
        marks = ",".join("?" * len(OPEN_STATUSES))
        ids = [r["id"] for r in connection.execute(
            f"SELECT id FROM calendar_subscriptions WHERE status IN ({marks})", OPEN_STATUSES)]
    result = {"checked": 0, "active": 0}
    with app.app_context():
        for sub_id in ids:
            result["checked"] += 1
            status = sync(sub_id)
            if status in ("ACTIVE", "APPROVED"):
                result["active"] += 1
                _alert_staff(sub_id)
    return result
