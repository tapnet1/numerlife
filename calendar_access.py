"""How far ahead a client can see their calendar.

Every calendar starts with 30 free days from the day it was started (sign-up, the client page,
or the practitioner). Months from then on are locked until the client has the yearly plan
(CAD $250: $100, then $15/month for 10 months; the PayPal subscription comes next) or their
practitioner gives access on the lead page. Past months and the current free month stay open.
Calendar emails stop on the last free day. The practitioner always sees everything.
"""
from __future__ import annotations

import datetime as dt

from flask import abort, flash, redirect, request, url_for

from app import app, db, login_required, open_db

FREE_DAYS = 30
PLAN_TEXT = "CAD $250"

T = {
    "en": {"title": "This month is part of your full year",
           "body": "Your free access covers your calendar until {date}. Continue with your full year of daily guidance and your journal: CAD $250 a year ($100, then $15 a month for 10 months).",
           "ask": "Online subscriptions open very soon. To continue now, contact {who}.",
           "until": "Free until {date}", "back": "Back to {month}"},
    "es": {"title": "Este mes es parte de tu año completo",
           "body": "Tu acceso gratuito incluye tu calendario hasta el {date}. Continúa con tu año completo de guía diaria y tu diario: CAD $250 al año ($100 y luego $15 al mes durante 10 meses).",
           "ask": "Las suscripciones en línea abren muy pronto. Para continuar ahora, contacta a {who}.",
           "until": "Gratis hasta el {date}", "back": "Volver a {month}"},
    "fr": {"title": "Ce mois fait partie de votre année complète",
           "body": "Votre accès gratuit couvre votre calendrier jusqu'au {date}. Continuez avec votre année complète de conseils quotidiens et votre journal : 250 $ CA par an (100 $, puis 15 $ par mois pendant 10 mois).",
           "ask": "Les abonnements en ligne ouvrent très bientôt. Pour continuer dès maintenant, contactez {who}.",
           "until": "Gratuit jusqu'au {date}", "back": "Retour à {month}"},
    "pt": {"title": "Este mês faz parte do seu ano completo",
           "body": "Seu acesso gratuito inclui seu calendário até {date}. Continue com seu ano completo de orientação diária e seu diário: CAD $250 por ano ($100 e depois $15 por mês durante 10 meses).",
           "ask": "As assinaturas online abrem em breve. Para continuar agora, fale com {who}.",
           "until": "Grátis até {date}", "back": "Voltar a {month}"},
    "it": {"title": "Questo mese fa parte del tuo anno completo",
           "body": "Il tuo accesso gratuito copre il calendario fino al {date}. Continua con il tuo anno completo di guida quotidiana e il tuo diario: CAD $250 all'anno ($100, poi $15 al mese per 10 mesi).",
           "ask": "Gli abbonamenti online aprono a breve. Per continuare ora, contatta {who}.",
           "until": "Gratis fino al {date}", "back": "Torna a {month}"},
    "de": {"title": "Dieser Monat gehört zu deinem vollen Jahr",
           "body": "Dein kostenloser Zugang umfasst deinen Kalender bis zum {date}. Mach weiter mit deinem vollen Jahr täglicher Impulse und deinem Tagebuch: 250 CAD pro Jahr (100 $, dann 15 $ monatlich für 10 Monate).",
           "ask": "Online-Abos starten in Kürze. Um jetzt weiterzumachen, kontaktiere {who}.",
           "until": "Kostenlos bis {date}", "back": "Zurück zu {month}"},
}


def labels(lang: str) -> dict:
    return T.get(lang) or T["en"]


def access_until(cal) -> dt.date:
    """Last day the client can see."""
    value = cal["access_until"] if "access_until" in cal.keys() else None
    if value:
        return dt.date.fromisoformat(value[:10])
    return started_on(cal) + dt.timedelta(days=FREE_DAYS)


def started_on(cal) -> dt.date:
    """The local (Edmonton) day the calendar was started; created_at is stored in UTC."""
    import calendar_engine as engine
    from zoneinfo import ZoneInfo
    if not cal["created_at"]:
        return engine.local_today()
    moment = dt.datetime.fromisoformat(cal["created_at"])
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(ZoneInfo(engine.DEFAULT_TZ)).date()


def month_open(cal, year: int, month: int) -> bool:
    return dt.date(year, month, 1) <= access_until(cal)


def day_open(cal, day: dt.date) -> bool:
    return day <= access_until(cal)


app.jinja_env.globals.update(calendar_access_until=access_until, calendar_access_labels=labels)


@app.post("/leads/<int:lead_id>/calendar/access")
@login_required
def calendar_set_access(lead_id):
    """The practitioner gives the client access until a date (e.g. paid outside numer.life)."""
    import workspace
    workspace.scoped_lead(lead_id)
    cal = db().execute("SELECT * FROM calendars WHERE lead_id = ?", (lead_id,)).fetchone()
    if cal is None:
        abort(404)
    if request.form.get("year") == "yes":
        until = max(access_until(cal), dt.date.today()) + dt.timedelta(days=365)
    else:
        try:
            until = dt.date.fromisoformat(request.form.get("until", ""))
        except ValueError:
            flash("Choose a date.", "error")
            return redirect(url_for("lead_detail", lead_id=lead_id) + "#calendar")
    if not dt.date(2020, 1, 1) <= until <= dt.date.today() + dt.timedelta(days=3 * 366):
        flash("Choose a date within the next three years.", "error")
        return redirect(url_for("lead_detail", lead_id=lead_id) + "#calendar")
    with open_db() as connection:
        connection.execute("UPDATE calendars SET access_until = ? WHERE id = ?", (until.isoformat(), cal["id"]))
    flash(f"Calendar access until {until.isoformat()}.", "info")
    return redirect(url_for("lead_detail", lead_id=lead_id) + "#calendar")
