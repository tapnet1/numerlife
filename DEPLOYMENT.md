# Deployment — numer.life

What is actually deployed and how. Decisions and status live in the project's
**rules-and-roadmap** doc.

## Where it runs

| Item | Value |
|---|---|
| URL | https://numer.life (app at the root domain; `www.` redirects to it) |
| Server | server2.tapnet.com (199.192.16.252), SSH port 21098, WHM/cPanel |
| cPanel account | `numerlife1` |
| App folder | `/home/numerlife1/numerlife` (git clone of `tapnet1/numerlife`, branch `main`) |
| Python | `/usr/bin/python3.12` → `.venv` in the app folder |
| Database | `/home/numerlife1/data/numerlife.db` (SQLite, outside the web root, chmod 600) |
| Backups | `/home/numerlife1/data/numerlife.db.<UTC timestamp>.bak`, one per deploy, chmod 600 |
| DNS | nameservers ns1/ns2.tapnet.com (zone on server2) |
| SSL | cPanel AutoSSL |

## Server-only files (never in git)

- **`.env`** (chmod 600): `NUMER_SECRET_KEY`, `NUMER_CREDENTIAL_KEY`, `NUMER_DB`, `NUMER_BASE_URL`,
  `NUMER_NOINDEX`. See `.env.example`. The deploy fails if `NUMER_DB` is missing, if
  `NUMER_SECRET_KEY` is shorter than 32 characters or still the example value, or if
  `NUMER_BASE_URL` is not an `https://` address; it warns about the rest. Values are never printed.
- **`passenger_wsgi.py`**: loads `.env` into the environment, then `from app import app as application`.

## Passenger

This server does **not** accept Passenger directives in `.htaccess` (Apache returns a 500:
"PassengerEnabled cannot occur within htaccess files"). Like Strategy Capital, the config is a
root-level include for the HTTPS virtual host:

`/etc/apache2/conf.d/userdata/ssl/2_4/numerlife1/numer.life/passenger.conf`

```apache
<IfModule mod_passenger.c>
    PassengerEnabled on
    PassengerAppRoot /home/numerlife1/numerlife
    PassengerAppType wsgi
    PassengerStartupFile passenger_wsgi.py
    PassengerPython /home/numerlife1/numerlife/.venv/bin/python
</IfModule>
```

Activated (as root, in WHM → Terminal) with:

```bash
/scripts/ensure_vhost_includes --user=numerlife1
/scripts/restartsrv_httpd
```

It applies only to the HTTPS vhost, so AutoSSL must have issued the certificate first.
Plain-HTTP requests are served from `public_html`; its `.htaccess` redirects them to HTTPS
(RewriteRule is allowed there; only Passenger directives are not).

Restarting the app: `touch /home/numerlife1/numerlife/tmp/restart.txt` (the deploy does this).

## Keys

| Key | Purpose | Private half | Public half |
|---|---|---|---|
| `numerlife_deploy` | GitHub Actions → SSH into the server | GitHub → environment `production` → secret `DEPLOY_SSH_KEY` | cPanel → SSH Access, authorized |
| `github_numerlife` | Server → read-only `git fetch` from GitHub | `~/.ssh/github_numerlife` on the server | GitHub repo → Settings → Deploy keys (read-only) |

The server reaches GitHub over `ssh.github.com:443` (`~/.ssh/config`). The server's SSH host key
is pinned in the workflows (same server as Strategy Capital).

## GitHub Actions

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` | push to `main` / `feature/**`, PRs to `main` | installs requirements, runs `python -m unittest discover -s tests -v` |
| `verify-deploy-ssh.yml` | manual | checks `DEPLOY_SSH_KEY` parses and logs in; checks branch, `.venv`, `.env`, `passenger_wsgi.py` and that the server can fetch from GitHub. Changes nothing. |
| `deploy-production.yml` | manual, `production` environment, one at a time | see below |

Note: the Claude GitHub app cannot write files under `.github/workflows/`. Workflow changes are
added by Marco through the GitHub web editor.

### What a deploy does

1. Runs the tests on `main` and records the exact commit tested.
2. SSHes in as `numerlife1` with the pinned host key.
3. Checks the server is on `main` with a clean tree, `passenger_wsgi.py` and `.env` present.
4. `git fetch origin main`, then fast-forwards to **the tested commit only**, and confirms the
   server is now exactly on it. If `main` moved after the tests ran, the deploy stops; run it
   again so the newer commit is tested first.
5. `pip install -r requirements.txt` into `.venv`, then verifies the imports.
6. Checks required `.env` settings (secret key length, https base URL) without printing values.
7. Backs up the database with SQLite's online backup API (timestamped, chmod 600).
8. Runs `initialize()` (creates missing tables only) and verifies the required tables.
9. Checks `NUMER_CREDENTIAL_KEY` is a valid 32-byte base64 key (warning if not).
10. Touches `tmp/restart.txt`.
11. From GitHub: `/health`, `/login`, `/static/css/app.css` and `/` must return exactly 200
    (a redirect counts as a failure), and `/admin` must return 302 when signed out.
12. Always deletes the temporary SSH key.

No cron jobs yet. When `jobs.py` arrives, the deploy installs them in a marked crontab block
(`# numerlife-jobs begin` / `# numerlife-jobs end`) so re-runs stay idempotent.

## One-time steps after the first deploy

Create the admin account (cPanel → Terminal as `numerlife1`):

```bash
cd ~/numerlife && set -a && . ./.env && set +a && .venv/bin/flask --app app create-owner
```

## Practitioner accounts (until invitations arrive)

Create a practitioner with a personal link (cPanel → Terminal as `numerlife1`):

```bash
cd ~/numerlife && set -a && . ./.env && set +a
.venv/bin/flask --app app add-practitioner          # prompts: email, name, link name, password
.venv/bin/flask --app app set-slug                  # change someone's link name later
```

Link names are 2-40 lowercase letters, numbers or dashes; reserved words (admin, login, privacy…)
are refused.

## Rolling back

**Code:** revert the bad commit on `main` in GitHub and deploy again.

**Database:** restore one of the timestamped backups with the app's own command. It copies
through SQLite's backup API, so it is safe while the app is running and with WAL files present,
and it saves the current database first (`numerlife.db.before-restore.<stamp>.bak`). Never copy
files over the live database by hand.

```bash
cd ~/numerlife && set -a && . ./.env && set +a
ls -t ~/data/numerlife.db.*.bak | head -5                    # pick one; use only the file name
.venv/bin/flask --app app restore-db numerlife.db.<STAMP>.bak
touch tmp/restart.txt
```

## Moving to another domain later

1. Point the new domain at server2 and add it to the `numerlife1` account (or make it the primary domain).
2. Run AutoSSL for it.
3. Copy the Passenger include to `.../userdata/ssl/2_4/numerlife1/<new-domain>/passenger.conf`
   and run the two activation commands above.
4. Change `NUMER_BASE_URL` in `.env`, update the URLs in `deploy-production.yml`, deploy.
5. 301-redirect numer.life to the new domain. Users sign in once more (cookies are per domain).

## If local git tracking breaks

```bash
git config --replace-all remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'
git fetch && git branch -u origin/<branch>
```

## Limits on this server

- Request bodies over ~120 KB hang at Passenger. The app caps requests at 100 KB; anything larger
  (e.g. a big import) needs a server-side path: upload via File Manager, then process it with a
  Terminal command.
