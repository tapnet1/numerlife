"""Paid full reports: checkout, payment, the practitioner's review, and delivery.

1. A visitor who has unlocked their free report clicks "Get my full written reading" (CAD $29).
   We create an order and send them to PayPal.
2. PayPal sends them back to /paypal/return. The server captures the order with PayPal's API;
   PayPal's answer (completed, right amount, right order) is the only proof of payment. The
   `capture-orders` job captures any approved order whose buyer never came back.
3. Once paid: the sale is credited 75/25, the client gets a receipt, and the practitioner (and
   her team) get an email and a push alert to prepare the reading.
4. The practitioner opens /orders/<id>: the reading starts as a draft from her report texts. She
   edits and completes it, adds a personal note, previews it, and sends it.
5. The client gets an email with a private link (/p/<token>) and a PDF of their reading.

Nothing is sent to the client automatically; the practitioner always reviews first.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import secrets

import click
from flask import abort, flash, redirect, render_template, request, url_for

import billing
import paypal
import report_texts as texts
from app import (BASE_URL, SITE_NAME, app, current_user, db, iso, login_required, now_utc, open_db,
                 role_required, workspace_owner_id)

PRODUCT = "full_report"
PRICE_CENTS = 2900
CURRENCY = "CAD"
STATUSES = {"pending": "Awaiting payment", "paid": "Paid: to prepare", "delivered": "Sent to client",
            "refunded": "Refunded", "cancelled": "Cancelled"}
OPEN_PAYMENT_DAYS = 3  # an unpaid checkout is given up after this
MAX_TEXT = {"title": 120, "meaning": 2000, "strengths": 800, "challenges": 800, "lesson": 1000,
            "combination": 1500, "affirmation": 800, "note": 2000}

T = {
    "en": {
        "price_label": "Your full written reading",
        "paid_subject": "Payment received: your full reading is being prepared",
        "paid_lead": "Thank you, {first_name}. We've received your payment of {price}.",
        "paid_body": "{who} is now preparing your full written reading personally. We'll email you as soon as it's ready, usually within a few business days.",
        "ready_subject": "Your full numerology reading is ready, {first_name}",
        "ready_lead": "Hi {first_name}, your full written reading from {who} is ready.",
        "ready_button": "Open my reading",
        "ready_pdf": "You can also download it as a PDF from the reading page.",
        "receipt": "Receipt", "amount": "Amount", "order": "Order", "paid_via": "Paid with PayPal to TAPNET, which operates numer.life.",
        "your_numerologist": "your numerologist", "note_from": "A note from {who}",
        "preparing_title": "Your reading is being prepared",
        "preparing_body": "Thank you for your order. {who} is preparing your full written reading personally. We'll email you at {email} as soon as it's ready.",
        "refunded_title": "This order was refunded",
        "reading_for": "Full numerology reading for", "pdf": "Download PDF", "prepared_by": "Prepared by {who}",
        "born": "Born", "footer": "For reflection and personal growth. Not professional advice.",
    },
    "es": {
        "price_label": "Tu lectura completa por escrito",
        "paid_subject": "Pago recibido: estamos preparando tu lectura completa",
        "paid_lead": "Gracias, {first_name}. Hemos recibido tu pago de {price}.",
        "paid_body": "{who} está preparando personalmente tu lectura completa por escrito. Te escribiremos en cuanto esté lista, normalmente en pocos días hábiles.",
        "ready_subject": "Tu lectura de numerología completa está lista, {first_name}",
        "ready_lead": "Hola {first_name}, tu lectura completa por escrito de {who} está lista.",
        "ready_button": "Abrir mi lectura",
        "ready_pdf": "También puedes descargarla en PDF desde la página de la lectura.",
        "receipt": "Recibo", "amount": "Importe", "order": "Pedido", "paid_via": "Pagado con PayPal a TAPNET, que opera numer.life.",
        "your_numerologist": "tu numeróloga", "note_from": "Una nota de {who}",
        "preparing_title": "Estamos preparando tu lectura",
        "preparing_body": "Gracias por tu pedido. {who} está preparando personalmente tu lectura completa por escrito. Te escribiremos a {email} en cuanto esté lista.",
        "refunded_title": "Este pedido fue reembolsado",
        "reading_for": "Lectura de numerología completa para", "pdf": "Descargar PDF", "prepared_by": "Preparada por {who}",
        "born": "Nacimiento", "footer": "Para la reflexión y el crecimiento personal. No es asesoramiento profesional.",
    },
}


def price_text(cents: int = PRICE_CENTS) -> str:
    return f"CAD {billing.money(cents)}"


def platform_ready() -> bool:
    with open_db() as connection:
        return billing.platform_account(connection) is not None


def sales_open() -> bool:
    """Paid reports are switched on in Admin, PayPal keys are on the server and TAPNET's account exists."""
    import intake
    return intake.paid_reports_on() and paypal.configured() and platform_ready()


def order_for_lead(lead_id: int):
    """The lead's paid or delivered order, if any (the one a client can open)."""
    return db().execute("SELECT * FROM orders WHERE lead_id = ? AND status IN ('paid', 'delivered', 'refunded')"
                        " ORDER BY id DESC LIMIT 1", (lead_id,)).fetchone()


def orders_for_lead(lead_id: int) -> list:
    return db().execute("SELECT * FROM orders WHERE lead_id = ? AND status != 'cancelled' ORDER BY id DESC",
                        (lead_id,)).fetchall()


app.jinja_env.globals.update(paid_order_for_lead=order_for_lead, orders_for_lead=orders_for_lead,
                             sales_open=sales_open, order_statuses=STATUSES, full_report_price=price_text)


def _lead_lang(lead) -> str:
    import numerology
    return texts.clean_lang(numerology.lead_report(lead).get("lang"))


def _who(practitioner, lang: str) -> str:
    return practitioner["display_name"] if practitioner is not None and practitioner["display_name"] \
        else T[lang]["your_numerologist"]


# --------------------------------------------------------------------------
# Checkout
# --------------------------------------------------------------------------
@app.post("/r/<token>/buy")
def buy_report(token):
    import intake
    lead = intake.lead_for_token(token)
    if lead["status"] != "captured" or not lead["email"]:
        return redirect(url_for("report", token=token))
    existing = order_for_lead(lead["id"])
    if existing is not None:
        return redirect(url_for("order_public", token=existing["token"]))
    if not sales_open():
        flash("The full written reading isn't available right now. Please try again later.", "error")
        return redirect(url_for("report", token=token))
    if not intake.reserve_attempt("buy", 10):
        flash("Too many attempts from your connection. Please try again in an hour.", "error")
        return redirect(url_for("report", token=token))
    order_token = secrets.token_urlsafe(24)
    now = iso(now_utc())
    with open_db() as connection:
        # A new checkout replaces any earlier unpaid one, which is then never captured.
        connection.execute("UPDATE orders SET status = 'cancelled', updated_at = ? WHERE lead_id = ?"
                           " AND status = 'pending'", (now, lead["id"]))
        order_id = connection.execute(
            "INSERT INTO orders (lead_id, practitioner_id, product, amount_cents, currency, status, token,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
            (lead["id"], lead["practitioner_id"], PRODUCT, PRICE_CENTS, CURRENCY, order_token, now, now),
        ).lastrowid
    lang = _lead_lang(lead)
    try:  # network call: no database lock held
        paypal_id, approve_url = paypal.create_order(
            reference=f"order-{order_id}", cents=PRICE_CENTS, currency=CURRENCY,
            description=f"{SITE_NAME}: {T[lang]['price_label']}",
            return_url=f"{BASE_URL}/paypal/return", cancel_url=f"{BASE_URL}/paypal/cancel", brand=SITE_NAME)
    except paypal.PayPalError as exc:
        app.logger.warning("PayPal create order failed: %s (%s)", exc, exc.issue)
        with open_db() as connection:
            connection.execute("UPDATE orders SET status = 'cancelled', updated_at = ? WHERE id = ?",
                               (iso(now_utc()), order_id))
        flash("We couldn't reach PayPal just now. Please try again in a few minutes.", "error")
        return redirect(url_for("report", token=token))
    with open_db() as connection:
        connection.execute("UPDATE orders SET paypal_order_id = ?, updated_at = ? WHERE id = ?",
                           (paypal_id, iso(now_utc()), order_id))
    return redirect(approve_url)


def _order_by_paypal_id(paypal_id: str):
    if not paypal_id or len(paypal_id) > 64 or not paypal_id.isalnum():
        abort(404)
    order = db().execute("SELECT * FROM orders WHERE paypal_order_id = ?", (paypal_id,)).fetchone()
    if order is None:
        abort(404)
    return order


def _lead_token(order) -> str:
    return db().execute("SELECT token FROM leads WHERE id = ?", (order["lead_id"],)).fetchone()[0]


@app.get("/paypal/return")
def paypal_return():
    order = _order_by_paypal_id(request.args.get("token", ""))
    if order["status"] in ("paid", "delivered", "refunded"):
        return redirect(url_for("order_public", token=order["token"]))
    if order["status"] != "pending":
        flash("That checkout was replaced by a newer one, so it wasn't charged.", "info")
        return redirect(url_for("report", token=_lead_token(order)))
    outcome = finalize(order["id"])
    if outcome == "paid":
        return redirect(url_for("order_public", token=order["token"]))
    flash("Your payment didn't go through, so you haven't been charged. Please try again."
          if outcome == "declined" else
          "We couldn't confirm your payment with PayPal yet. If you were charged, we'll confirm it within "
          "a few minutes and email you.", "error")
    return redirect(url_for("report", token=_lead_token(order)))


@app.get("/paypal/cancel")
def paypal_cancel():
    order = _order_by_paypal_id(request.args.get("token", ""))
    if order["status"] == "pending":
        with open_db() as connection:
            connection.execute("UPDATE orders SET status = 'cancelled', updated_at = ? WHERE id = ? AND status = 'pending'",
                               (iso(now_utc()), order["id"]))
    flash("Checkout cancelled. You haven't been charged.", "info")
    return redirect(url_for("report", token=_lead_token(order)))


def finalize(order_id: int) -> str:
    """Capture a pending order with PayPal and, if PayPal confirms payment, mark it paid.

    Returns "paid", "declined" (PayPal refused the payment) or "unconfirmed" (try again later).
    """
    order = db().execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if order is None or not order["paypal_order_id"]:
        return "unconfirmed"
    if order["status"] in ("paid", "delivered", "refunded"):
        return "paid"
    if order["status"] != "pending":
        return "declined"
    try:  # network call: no database lock held
        result = paypal.capture_order(order["paypal_order_id"])
    except paypal.PayPalError as exc:
        app.logger.warning("PayPal capture failed for order %s: %s (%s)", order_id, exc, exc.issue)
        if exc.issue in ("INSTRUMENT_DECLINED", "PAYER_ACTION_REQUIRED", "ORDER_NOT_APPROVED"):
            return "declined"
        return "unconfirmed"
    capture = paypal.completed_capture(result, reference=f"order-{order_id}", cents=order["amount_cents"],
                                       currency=order["currency"])
    if capture is None:
        app.logger.warning("PayPal order %s for order %s not completed or mismatched (status %s)",
                           order["paypal_order_id"], order_id, result.get("status"))
        return "declined" if result.get("status") in ("VOIDED",) else "unconfirmed"
    mark_paid(order_id, capture["capture_id"], capture["payer_email"])  # False: already recorded
    return "paid"


def mark_paid(order_id: int, capture_id: str, payer_email: str) -> bool:
    """Record a confirmed payment once: order paid, sale credited, client and practitioner told."""
    with open_db() as connection:
        changed = connection.execute(
            "UPDATE orders SET status = 'paid', paypal_capture_id = ?, payer_email = ?, paid_at = ?, updated_at = ?"
            " WHERE id = ? AND status = 'pending'",
            (capture_id or None, payer_email[:200], iso(now_utc()), iso(now_utc()), order_id)).rowcount
        if not changed:
            return False
        order = connection.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        _credit_sale(connection, order)
        recipients = _queue_paid_emails(connection, order)
    import emails
    import notify
    emails.kick()
    lead = db().execute("SELECT full_name FROM leads WHERE id = ?", (order["lead_id"],)).fetchone()
    first_name = (lead["full_name"].split() or ["A client"])[0] if lead else "A client"
    notify.queue([r["id"] for r in recipients], {
        "title": f"Paid report: {first_name}", "body": "Review, complete and send their full reading.",
        "url": f"/orders/{order_id}", "tag": f"order-{order_id}"})
    return True


def _credit_sale(connection, order) -> None:
    """Credit the sale 75/25. The buyer has already been charged, so this never fails the payment:
    a share that can't be credited (no TAPNET account yet) is logged for an admin adjustment."""
    reference = f"order-{order['id']}"
    note = f"Full report, order {order['id']}"
    platform = billing.platform_account(connection)
    platform_cents, practitioner_cents = billing.split(order["amount_cents"])
    if not order["practitioner_id"]:
        platform_cents, practitioner_cents = order["amount_cents"], 0
    if practitioner_cents:
        billing.post(connection, order["practitioner_id"], "sale_share", practitioner_cents,
                     f"{reference}:practitioner", note=note)
    if platform is not None:
        billing.post(connection, platform["id"], "platform_share", platform_cents, f"{reference}:platform",
                     source_user_id=order["practitioner_id"], note=note)
    else:
        app.logger.error("Order %s: no TAPNET platform account, platform share of %s cents not credited",
                         order["id"], platform_cents)


def _staff_recipients(connection, practitioner_id):
    if practitioner_id:
        practitioner = connection.execute("SELECT * FROM users WHERE id = ? AND is_active = 1",
                                          (practitioner_id,)).fetchone()
        if practitioner is not None:
            return [practitioner, *connection.execute("SELECT * FROM users WHERE team_of = ? AND is_active = 1",
                                                      (practitioner_id,)).fetchall()]
    return connection.execute("SELECT * FROM users WHERE role = 'admin' AND is_active = 1").fetchall()


def _queue_paid_emails(connection, order) -> list:
    import emails
    lead = connection.execute("SELECT * FROM leads WHERE id = ?", (order["lead_id"],)).fetchone()
    practitioner = connection.execute("SELECT * FROM users WHERE id = ?", (order["practitioner_id"],)).fetchone() \
        if order["practitioner_id"] else None
    lang = _lead_lang(lead)
    t = T[lang]
    first_name = (lead["full_name"].split() or ["there"])[0]
    who = _who(practitioner, lang)
    context = {"t": t, "lang": lang, "email_lang": lang, "first_name": first_name, "who": who, "price": price_text(order["amount_cents"]),
               "order_id": order["id"], "page_url": f"{BASE_URL}/p/{order['token']}",
               "card": emails._card(practitioner) if practitioner is not None and practitioner["is_active"] else None}
    subject = t["paid_subject"]
    text, html = emails._render_pair("order_paid", **context)
    emails.enqueue(connection, "order_paid", lead["email"], subject, text, html, lead_id=lead["id"])
    recipients = _staff_recipients(connection, order["practitioner_id"])
    for recipient in recipients:
        staff = {"recipient_name": (recipient["display_name"] or "").split(" ")[0], "full_name": lead["full_name"],
                 "price": price_text(order["amount_cents"]), "order_url": f"{BASE_URL}/orders/{order['id']}"}
        text, html = emails._render_pair("order_alert", **staff)
        emails.enqueue(connection, "order_alert", recipient["email"],
                       f"Paid report to prepare: {lead['full_name']}", text, html,
                       lead_id=lead["id"], user_id=recipient["id"])
    return list(recipients)


def capture_open_orders() -> dict:
    """Job: capture approved orders whose buyer never returned from PayPal; give up on old ones."""
    cutoff_new = iso(now_utc() - dt.timedelta(minutes=2))
    cutoff_old = iso(now_utc() - dt.timedelta(days=OPEN_PAYMENT_DAYS))
    with open_db() as connection:
        connection.execute("UPDATE orders SET status = 'cancelled', updated_at = ? WHERE status = 'pending'"
                           " AND created_at < ?", (iso(now_utc()), cutoff_old))
        rows = connection.execute("SELECT id, paypal_order_id FROM orders WHERE status = 'pending'"
                                  " AND paypal_order_id IS NOT NULL AND created_at < ?", (cutoff_new,)).fetchall()
    result = {"checked": 0, "paid": 0}
    if not paypal.configured():
        return result
    with app.app_context():
        for row in rows:
            result["checked"] += 1
            try:
                status = paypal.get_order(row["paypal_order_id"]).get("status")
            except paypal.PayPalError:
                continue
            if status in ("APPROVED", "COMPLETED") and finalize(row["id"]) == "paid":
                result["paid"] += 1
    return result


# --------------------------------------------------------------------------
# The reading: draft, editing, client view
# --------------------------------------------------------------------------
def initial_draft(lead) -> dict:
    import reports
    reading = reports.full_reading(lead)
    return {
        "lang": reading["lang"],
        "items": [{key: item[key] for key in ("key", "label", "number", "display", "title", "meaning",
                                               "strengths", "challenges", "lesson")} for item in reading["items"]],
        "combination": reading["combination"],
        "affirmation": " ".join(reading["affirmations"]),
        "note": "",
    }


def order_draft(order, lead) -> dict:
    if order["draft"]:
        return json.loads(order["draft"])
    return initial_draft(lead)


def reading_view(draft: dict) -> dict:
    """The draft in the shape _reading.html expects."""
    lang = texts.clean_lang(draft.get("lang"))
    items = []
    for item in draft["items"]:
        items.append({**item, "strength_list": texts.as_list(item.get("strengths", "")),
                      "challenge_list": texts.as_list(item.get("challenges", ""))})
    affirmation = draft.get("affirmation", "").strip()
    return {"lang": lang, "labels": texts.LABELS[lang], "items": items, "combination": draft.get("combination", ""),
            "affirmations": [affirmation] if affirmation else [], "note": draft.get("note", "")}


def _clean(value: str | None, limit: int) -> str:
    lines = [" ".join(line.split()) for line in (value or "").replace("\r", "").split("\n")]
    return "\n".join(line for line in lines if line)[:limit]


def _scoped_order(order_id: int):
    user = current_user()
    order = db().execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if order is None or order["status"] in ("pending", "cancelled"):
        abort(404)
    if user["role"] != "admin" and order["practitioner_id"] != workspace_owner_id(user):
        abort(404)  # not found and not yours look the same
    return order


@app.get("/orders")
@login_required
def orders_list():
    user = current_user()
    where, params = ("orders.status NOT IN ('pending', 'cancelled')", [])
    if user["role"] != "admin":
        where += " AND orders.practitioner_id = ?"
        params.append(workspace_owner_id(user))
    rows = db().execute(
        "SELECT orders.*, leads.full_name, users.display_name AS practitioner_name FROM orders"
        " JOIN leads ON leads.id = orders.lead_id LEFT JOIN users ON users.id = orders.practitioner_id"
        f" WHERE {where} ORDER BY orders.status = 'paid' DESC, orders.id DESC LIMIT 200", params).fetchall()
    return render_template("orders.html", orders=rows)


@app.route("/orders/<int:order_id>", methods=["GET", "POST"])
@login_required
def edit_order(order_id):
    order = _scoped_order(order_id)
    lead = db().execute("SELECT * FROM leads WHERE id = ?", (order["lead_id"],)).fetchone()
    draft = order_draft(order, lead)
    if request.method == "POST":
        action = request.form.get("action")
        if order["status"] == "refunded":
            abort(400, description="This order was refunded.")
        if action == "reset":
            if order["status"] != "paid":
                abort(400, description="A sent reading can't be reset; edit it instead.")
            with open_db() as connection:
                connection.execute("UPDATE orders SET draft = NULL, updated_at = ? WHERE id = ?",
                                   (iso(now_utc()), order_id))
            flash("The draft was rebuilt from your current report texts.", "info")
            return redirect(url_for("edit_order", order_id=order_id))
        for index, item in enumerate(draft["items"]):
            for field in ("title", "meaning", "strengths", "challenges", "lesson"):
                key = f"{index}-{field}"
                if key in request.form:
                    item[field] = _clean(request.form.get(key), MAX_TEXT[field])
        for field in ("combination", "affirmation", "note"):
            if field in request.form:
                draft[field] = _clean(request.form.get(field), MAX_TEXT[field])
        with open_db() as connection:
            connection.execute("UPDATE orders SET draft = ?, updated_at = ? WHERE id = ?",
                               (json.dumps(draft), iso(now_utc()), order_id))
        if action == "send":
            deliver(order_id)
            flash(f"Sent. {lead['full_name']} has an email with their reading and PDF."
                  if order["status"] == "paid" else "The updated reading is on their page.", "info")
        else:
            flash("Draft saved.", "info")
        return redirect(url_for("edit_order", order_id=order_id))
    return render_template("order_edit.html", order=order, lead=lead, draft=draft, reading=reading_view(draft),
                           no_strengths=texts.NO_STRENGTHS, client_url=f"{BASE_URL}/p/{order['token']}")


def deliver(order_id: int) -> None:
    """Mark the reading delivered and email the client (only the first time; later edits update the page)."""
    import emails
    with open_db() as connection:
        order = connection.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        first = order["status"] == "paid"
        connection.execute("UPDATE orders SET status = 'delivered', delivered_at = COALESCE(delivered_at, ?),"
                           " updated_at = ? WHERE id = ? AND status IN ('paid', 'delivered')",
                           (iso(now_utc()), iso(now_utc()), order_id))
        if not first:
            return
        lead = connection.execute("SELECT * FROM leads WHERE id = ?", (order["lead_id"],)).fetchone()
        practitioner = connection.execute("SELECT * FROM users WHERE id = ?", (order["practitioner_id"],)).fetchone() \
            if order["practitioner_id"] else None
        lang = _lead_lang(lead)
        t = T[lang]
        first_name = (lead["full_name"].split() or ["there"])[0]
        who = _who(practitioner, lang)
        context = {"t": t, "lang": lang, "email_lang": lang, "first_name": first_name, "who": who,
                   "page_url": f"{BASE_URL}/p/{order['token']}",
                   "card": emails._card(practitioner) if practitioner is not None and practitioner["is_active"] else None}
        text, html = emails._render_pair("order_ready", **context)
        emails.enqueue(connection, "order_ready", lead["email"], t["ready_subject"].format(first_name=first_name),
                       text, html, lead_id=lead["id"])
    emails.kick()


def _public_order(token: str):
    if not token or len(token) > 64:
        abort(404)
    order = db().execute("SELECT * FROM orders WHERE token = ? AND status IN ('paid', 'delivered', 'refunded')",
                         (token,)).fetchone()
    if order is None:
        abort(404)
    lead = db().execute("SELECT * FROM leads WHERE id = ?", (order["lead_id"],)).fetchone()
    practitioner = db().execute("SELECT * FROM users WHERE id = ?", (order["practitioner_id"],)).fetchone() \
        if order["practitioner_id"] else None
    return order, lead, practitioner


@app.get("/p/<token>")
def order_public(token):
    order, lead, practitioner = _public_order(token)
    lang = _lead_lang(lead)
    reading = reading_view(json.loads(order["draft"])) if order["status"] == "delivered" and order["draft"] else None
    return render_template("order_public.html", order=order, lead=lead, practitioner=practitioner, t=T[lang],
                           lang=lang, who=_who(practitioner, lang), reading=reading)


@app.get("/p/<token>/reading.pdf")
def order_pdf(token):
    order, lead, practitioner = _public_order(token)
    if order["status"] != "delivered" or not order["draft"]:
        abort(404)
    lang = _lead_lang(lead)
    pdf = render_pdf(lead, reading_view(json.loads(order["draft"])), T[lang], _who(practitioner, lang))
    first = (lead["full_name"].split() or ["reading"])[0].lower()
    safe = "".join(ch for ch in first if ch.isalnum()) or "reading"
    return pdf, 200, {"Content-Type": "application/pdf",
                      "Content-Disposition": f'inline; filename="numerology-reading-{safe}.pdf"'}


def render_pdf(lead, reading: dict, t: dict, who: str) -> bytes:
    from xml.sax.saxutils import escape

    from reportlab.lib.colors import Color
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

    import calendars
    calendars._register_fonts()
    ink, muted, plum = Color(*calendars.INK), Color(*calendars.MUTED), Color(*calendars.PURPLE)
    h1 = ParagraphStyle("h1", fontName="Cormorant-Bold", fontSize=26, leading=30, textColor=plum, spaceAfter=4)
    h2 = ParagraphStyle("h2", fontName="Cormorant-SemiBold", fontSize=17, leading=21, textColor=plum,
                        spaceBefore=14, spaceAfter=4)
    h3 = ParagraphStyle("h3", fontName="Inter-SemiBold", fontSize=9.5, leading=13, textColor=ink, spaceBefore=6)
    body = ParagraphStyle("body", fontName="Inter-Regular", fontSize=9.5, leading=14, textColor=ink, spaceAfter=4)
    small = ParagraphStyle("small", parent=body, fontSize=8.5, textColor=muted)
    quote = ParagraphStyle("quote", fontName="Cormorant-SemiBoldItalic", fontSize=14, leading=19, textColor=plum,
                           leftIndent=12, spaceBefore=4)

    def p(text, style=body):
        return Paragraph(escape(text).replace("\n", "<br/>"), style)

    def bullets(values):
        return ListFlowable([ListItem(p(v), leftIndent=10) for v in values], bulletType="bullet",
                            start="•", leftIndent=12, bulletFontName="Inter-Regular", bulletFontSize=8)

    labels = reading["labels"]
    story = [p(t["reading_for"], small), p(lead["full_name"], h1),
             p(f"{t['born']}: {lead['birth_date']}", small), p(t["prepared_by"].format(who=who), small)]
    if reading.get("note"):
        story += [p(t["note_from"].format(who=who), h2), p(reading["note"])]
    for item in reading["items"]:
        number = item["display"] if item["number"] else "–"
        story.append(p(f"{item['label']} {number}: {item['title']}", h2))
        story.append(p(item["meaning"]))
        if item["strength_list"]:
            story += [p(labels["strengths"], h3), bullets(item["strength_list"])]
        if item["challenge_list"]:
            story += [p(labels["challenges"], h3), bullets(item["challenge_list"])]
        if item.get("lesson"):
            story += [p(labels["lesson"], h3), p(item["lesson"])]
    if reading.get("combination") or reading.get("affirmations"):
        story.append(p(labels["together"], h2))
        if reading.get("combination"):
            story.append(p(reading["combination"]))
        for affirmation in reading.get("affirmations", []):
            story.append(p(affirmation, quote))
    story += [Spacer(1, 18), p(f"{SITE_NAME} · {t['footer']}", small)]
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=0.9 * inch, rightMargin=0.9 * inch,
                            topMargin=0.8 * inch, bottomMargin=0.8 * inch, title=f"{SITE_NAME}: {lead['full_name']}",
                            author=who)
    doc.build(story)
    return buffer.getvalue()


# --------------------------------------------------------------------------
# Admin: switch sales on/off, refunds
# --------------------------------------------------------------------------
def admin_overview() -> dict:
    rows = db().execute(
        "SELECT orders.*, leads.full_name, users.display_name AS practitioner_name FROM orders"
        " JOIN leads ON leads.id = orders.lead_id LEFT JOIN users ON users.id = orders.practitioner_id"
        " WHERE orders.status NOT IN ('pending', 'cancelled') ORDER BY orders.id DESC LIMIT 50").fetchall()
    import intake
    return {"switch_on": intake.paid_reports_on(), "paypal_ready": paypal.configured(),
            "platform_ready": platform_ready(),
            "paypal_env": paypal.environment(), "orders": rows, "price": price_text()}


app.jinja_env.globals["paid_reports_overview"] = admin_overview


@app.post("/admin/paid-reports")
@role_required("admin")
def admin_paid_reports():
    import emails
    turn_on = request.form.get("on") == "yes"
    if turn_on and not paypal.configured():
        flash("Add the PayPal keys to .env on the server first.", "error")
        return redirect(url_for("admin") + "#paid-reports")
    if turn_on and not platform_ready():
        flash("Set up the numer.life/tapnet (TAPNET) account under Billing first, so sales can be credited.", "error")
        return redirect(url_for("admin") + "#paid-reports")
    emails.set_setting("paid_reports", "on" if turn_on else "off")
    flash("Paid reports are on sale." if turn_on else "Paid reports are off. Visitors no longer see the offer.", "info")
    return redirect(url_for("admin") + "#paid-reports")


@app.post("/admin/orders/<int:order_id>/refunded")
@role_required("admin")
def admin_mark_refunded(order_id):
    """After refunding in PayPal: mark the order refunded and reverse its credits."""
    with open_db() as connection:
        order = connection.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if order is None or order["status"] not in ("paid", "delivered"):
            abort(404)
        connection.execute("UPDATE orders SET status = 'refunded', updated_at = ? WHERE id = ?",
                           (iso(now_utc()), order_id))
        reference = f"order-{order_id}"
        for entry in connection.execute("SELECT * FROM ledger WHERE reference IN (?, ?, ?)",
                                         (f"{reference}:practitioner", f"{reference}:platform", reference)).fetchall():
            billing.post(connection, entry["user_id"], "adjustment", -entry["amount_cents"],
                         f"{entry['reference']}:refund", source_user_id=entry["source_user_id"],
                         note=f"Refund of order {order_id}", created_by=current_user()["id"])
    flash(f"Order {order_id} marked refunded and its credits reversed.", "info")
    return redirect(url_for("admin") + "#paid-reports")


@app.cli.command("capture-orders")
def capture_orders_command():
    """Capture approved PayPal orders whose buyer didn't come back to the site."""
    click.echo(capture_open_orders())
