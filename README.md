# numer.life

Numerology report and lead-capture app for numerology practitioners. Visitors enter their
name and birth details, see a preview of their report, and receive the full report by email.
Each practitioner shares a personal link (e.g. `numer.life/liliana`) and receives the leads
that arrive through it.

Stack: Python 3.12, Flask 3 (server-rendered), plain CSS, SQLite, Passenger on cPanel.
Decisions and roadmap: the project's **rules-and-roadmap** doc. Deployment: [DEPLOYMENT.md](DEPLOYMENT.md).

## Run locally

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

export NUMER_SECRET_KEY=dev-only-key          # Windows PowerShell: $env:NUMER_SECRET_KEY="dev-only-key"
export NUMER_DB=instance/numerlife.db          # default if unset

.venv/bin/flask --app app init-db
.venv/bin/flask --app app create-owner         # prompts for email, name and password (12+ characters)
.venv/bin/flask --app app run --debug
```

Open http://localhost:5000. Sign in at `/login`; admins also see `/admin`.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Layout

| Path | What it is |
|---|---|
| `app.py` | Settings, database (`initialize()`), security, auth and roles, routes, `init-db` / `create-owner` / `restore-db` commands |
| `intake.py` | Public report form, practitioner links (`/<slug>`), teaser and email capture, `add-practitioner` / `set-slug` commands |
| `numerology.py` | Pythagorean core numbers (deterministic) and their default meanings |
| `secretbox.py` | AES-GCM encryption for stored third-party credentials (`NUMER_CREDENTIAL_KEY`) |
| `templates/` | Jinja pages (`base.html` holds the top bar and SEO / Open Graph tags) |
| `static/` | `css/app.css`, `favicon.svg` |
| `tests/` | stdlib `unittest` suite |
| `.github/workflows/` | `ci.yml`, `deploy-production.yml`, `verify-deploy-ssh.yml` |

## Security built in

- Passwords hashed (Werkzeug scrypt); minimum 12 characters.
- Session cookie: HttpOnly, SameSite=Lax, Secure on HTTPS, 12-hour lifetime, new session on sign-in.
- CSRF token required on every POST.
- Sign-in rate limit: 5 failures per account or 20 per IP in 15 minutes; same message for wrong
  password and unknown email.
- Security headers (CSP, frame denial, nosniff); `noindex` while `NUMER_NOINDEX=1`.
- Request bodies capped at 100 KB (larger uploads hang at Passenger on this server).
- Every query that touches practitioner data must be scoped to the signed-in practitioner.
