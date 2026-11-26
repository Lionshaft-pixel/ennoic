# Ennoic

Ennoic is an AI task and memory assistant. Users can manage tasks directly or describe task and memory actions in chat.

**Live beta:** [https://ennoic.onrender.com](https://ennoic.onrender.com)

## Features

- Email/password accounts with email verification, password reset, and Google sign-in.
- AI chat and natural-language task interpretation through Groq.
- Tasks with projects, deadlines, priorities, subtasks, duplicate suggestions, and dashboard search.
- Account-scoped conversations and saved memories.
- Free and monthly Pro plans. Defaults are Free: 40 AI requests per month, 40 memories, and the latest 10 conversations; Pro: 1,000 AI requests per month, 1,000 memories, and full conversation history. Task management is available on both plans.
- Recurring monthly Paddle Billing subscriptions, with no free trial. Ennoic verifies Paddle webhooks before changing subscription entitlements. Signed-in customers can open the Paddle customer portal to manage or cancel billing. The hosted production app uses Paddle; the Render dashboard holds its live credentials.
- A 30-day account-deletion grace period. Provider cancellation is checked before deletion is scheduled and before due account data is purged. A minimal deletion audit record is retained for a limited period.
- Public [Terms](/terms), [Privacy](/privacy), and [Refund Policy](/refund-policy) pages.

The defaults and prices can be overridden by environment variables. The Paddle catalog controls the actual charge; keep its monthly price configuration aligned with the amounts shown on Ennoic.

Current app reference prices are **$4.99 USD/month** and **₹179/month in India**. Paddle's configured catalog and checkout preview determine the final checkout amount.

## Technology

- Python and Flask, served by Gunicorn on Render.
- Neon PostgreSQL in the hosted deployment; SQLite for local development when `DATABASE_URL` is unset.
- Groq for AI requests. The default model is `openai/gpt-oss-20b`; `GROQ_MODEL` can select another available model.
- Paddle Billing for production subscriptions.
- Resend's HTTPS API or SMTP for verification and password-reset email.
- Google OAuth for optional Google sign-in.

Prompts and relevant conversation context are sent to Groq for processing. Account records, including readable conversation history, are stored in the configured database. Review the [Privacy Policy](/privacy) before using the beta with sensitive information.

## Repository layout

- `app.py` — Flask application, routes, security headers, and request handling.
- `account_store.py`, `task_store.py`, `database.py` — account, task, and database persistence.
- `task_management.py`, `main.py`, `models.py` — task interpretation, chat and memory flows, and Groq requests.
- `entitlements.py`, `billing.py`, `paddle_billing.py` — plan limits, retained Razorpay adapter, and Paddle Billing.
- `templates/` — Flask-rendered pages and the password-reset email.
- `static/` — CSS, JavaScript, image assets, and the landing/authentication HTML fragments loaded by their Flask template shells.
- `tests/` — Python application tests and Node-based JavaScript tests.
- `migrate_legacy_data.py` — explicit local legacy-data import tool.
- `purge_due_accounts.py` — one-pass account-deletion purge command; the web app also runs its deletion worker.
- `render.yaml` — Render Blueprint for the web service.
- `.agents/`, `.codex/`, `skills-lock.json` — Neon agent skills and local tooling configuration; not part of the Flask request path.

## Local setup

Use Python 3.10 or newer. Copy the example environment file and install the dependencies:

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Edit `.env` and set at least `GROQ_API_KEY` to use chat and natural-language task interpretation. Leave `DATABASE_URL` blank to use a local SQLite database, or supply a non-production PostgreSQL URL for database integration work. Do not point local experiments at the production Neon database.

Start the app from the repository root:

```powershell
python app.py
```

Then open [http://127.0.0.1:5000](http://127.0.0.1:5000). For local OAuth, add `http://127.0.0.1:5000/auth/google/callback` to the Google OAuth client's authorized redirect URIs. Production uses `https://ennoic.onrender.com/auth/google/callback`.

Local SQLite data is created as `tasks.sqlite3` by default. `ENNOIC_TASK_DB` selects another local SQLite path. Local development can generate a Flask secret key under the ignored `instance/` directory; set `ENNOIC_SECRET_KEY` explicitly for other environments.

## Configuration reference

Start from `.env.example`. Put secrets in `.env` locally or in the hosting provider's secret environment settings. Never commit `.env` or paste live credentials into source files.

| Purpose | Environment variables | Notes |
| --- | --- | --- |
| Database | `DATABASE_URL`, `ENNOIC_TASK_DB` | `DATABASE_URL` selects PostgreSQL; otherwise the app uses SQLite. Use Neon pooled connections for the hosted app. |
| Flask/session | `ENNOIC_SECRET_KEY`, `ENNOIC_COOKIE_SECURE` | Use a persistent, random secret and secure cookies over HTTPS in production. |
| Public URLs/proxy | `ENNOIC_PUBLIC_ORIGIN`, `ENNOIC_TRUST_PROXY`, `ENNOIC_COUNTRY_HEADER` | Set the public origin to `https://ennoic.onrender.com`. Trust forwarded headers only behind the configured proxy. A country header is optional and must be overwritten by a trusted proxy. |
| AI | `GROQ_API_KEY`, `GROQ_MODEL` | The key is required for AI chat and natural-language task operations. |
| Email | `RESEND_API_KEY`, `RESEND_FROM_EMAIL`; or `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, `SMTP_USE_SSL` | Resend is the recommended sender on Render Free, which blocks outbound SMTP. Real-user delivery requires a verified sending domain. |
| Google sign-in | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Configure the OAuth consent screen and the correct local or production callback URI. |
| Paddle | `PADDLE_ENVIRONMENT`, `PADDLE_CLIENT_TOKEN_SANDBOX`, `PADDLE_API_KEY_SANDBOX`, `PADDLE_WEBHOOK_SECRET_SANDBOX`, `PADDLE_PRICE_ID_SANDBOX`; matching `*_LIVE` values; `PADDLE_LIVE_CHECKOUT_ENABLED` | Keep sandbox and production credentials/catalog IDs separate. Production checkout requires `PADDLE_ENVIRONMENT=production` and explicit `PADDLE_LIVE_CHECKOUT_ENABLED=true`. Configure Paddle webhooks to `https://ennoic.onrender.com/api/paddle/webhook`. Never put API keys or webhook secrets in Git. |
| Razorpay legacy adapter | `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, `RAZORPAY_PRO_PLAN_ID_INR`, `RAZORPAY_PRO_PLAN_ID_USD` | These variables activate the retained legacy checkout adapter. Keep them unset on the Paddle production service unless Razorpay is intentionally re-enabled. |
| Plan limits/pricing | `ENNOIC_FREE_*`, `ENNOIC_PRO_*`, `ENNOIC_PRO_PRICE_INR_MINOR`, `ENNOIC_PRO_PRICE_USD_MINOR` | Defaults are defined in `entitlements.py`; `.env.example` lists the override names. Price amounts use minor currency units (paise/cents). Paddle's catalog determines the actual charge. |
| Shared rate-limit storage | `ENNOIC_RATE_LIMIT_STORAGE` | The default in-memory limiter is intended for a single app instance. Use a shared Redis-compatible store when running multiple instances. |
| Optional Discord notifications | `DISCORD_WEBHOOK_URL` | Sends best-effort alerts for registrations, successful sign-ins, new tasks, verified Pro activations, chat failures, and server errors. Successful chat exchanges also send the account email, current message, and AI reply to the configured Discord channel. Keep channel membership restricted, avoid sending sensitive content, and rotate the webhook if exposed. |

### Paddle and Razorpay status

Paddle is the production subscription checkout and entitlement provider. Paddle's signed webhook is received at `/api/paddle/webhook`; the app also provides `/api/paddle/portal` for authenticated customers. A success-page redirect alone does not grant Pro.

Razorpay's adapter and `/api/checkout` plus `/api/razorpay/webhook` routes remain in the repository for legacy/test compatibility. The legacy service selects Razorpay when its API credentials are configured. They are not the active production checkout documented for this beta; do not add Razorpay credentials to the Paddle production service unless that integration has been deliberately re-enabled and verified. Existing Razorpay-linked records remain in the database; provider operations on them require the matching Razorpay account credentials.

The Render Blueprint does not contain payment credentials. Configure production secrets and live catalog values in Render's service environment, and verify webhook delivery there. `.env.example` is a local configuration template with sandbox-safe defaults, not a copy of the deployed service configuration.

## Hosting and operations

`render.yaml` configures the `vibecoded` branch, Gunicorn, and health check for Render. It currently declares the Free plan; confirm the actual Render service plan in the dashboard because dashboard configuration can differ. The Blueprint provides `DATABASE_URL`, `ENNOIC_SECRET_KEY`, `ENNOIC_COOKIE_SECURE`, and `ENNOIC_TRUST_PROXY`; add other required application settings through Render's environment page.

Production data should remain in Neon, not Render's local filesystem. The local SQLite database is not synchronized to Neon; use `migrate_legacy_data.py` only for a deliberate local import after making a backup. The deletion worker runs in the web process and defers purging if payment-provider cancellation cannot be confirmed. The one-pass purge script is available for controlled operations; it should not be configured as a paid job without a separate operational decision.

### Current beta limitations

- Render's Free service configuration can sleep while idle and uses an ephemeral filesystem. Expect cold starts; do not store production data only on the instance disk.
- File attachments and AI file analysis are not implemented, even though placeholder entitlement values exist.
- Task and memory interpretation can be wrong; review task changes and extracted dates.
- Rate limiting defaults to in-memory storage and is not shared across instances unless configured.
- `ENNOIC_ADMIN_EMAILS` appears in `.env.example` but is not currently used as an admin allowlist. Admin access is restricted in application code; do not rely on that environment variable to change it.
- Browser-only local preferences and data on other devices are not erased by server-side account deletion. Backups are managed separately from the app's deletion worker.

## Run tests

PWA implementation, offline/cache policy, device checks, and the complete change report are documented in [docs/PWA.md](docs/PWA.md). Optional, separate Android APK packaging instructions are in [android/README.md](android/README.md).

Run the Python suite:

```powershell
python -m unittest discover -s tests -v
```

Run the JavaScript tests with Node's built-in test runner:

```powershell
node --test tests/*.test.cjs
```

Optional syntax and whitespace checks:

```powershell
python -m py_compile app.py account_store.py task_store.py task_management.py
git diff --check
```

## Public pages

- [Home](https://ennoic.onrender.com/)
- [Pricing](https://ennoic.onrender.com/pricing)
- [Terms of Service](https://ennoic.onrender.com/terms)
- [Privacy Policy](https://ennoic.onrender.com/privacy)
- [Refund Policy](https://ennoic.onrender.com/refund-policy)
