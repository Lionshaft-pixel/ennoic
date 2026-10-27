# Ennoic

Ennoic is a task manager with a conversational interface, built on the existing memory assistant. Describe work in ordinary language to create tasks, then manage projects, deadlines, priorities, and completion from the dashboard or chat. Existing memory actions remain available. Account data uses SQLite locally or PostgreSQL when `DATABASE_URL` is configured; natural-language chat and task interpretation use Groq's cloud API.

---

## What It Does

Ennoic supports two related workflows, with email/password and Google sign-in:

- **Tasks:** create and manage tasks from a dashboard or natural-language conversation. Tasks can have notes, a project or client, a deadline, priority, and subtasks.
- **Memory:** store, retrieve, and forget personal facts using the existing memory assistant.

Tasks, conversations, memories, and accounts use the local SQLite database (`tasks.sqlite3`) during development. When `DATABASE_URL` is configured, Ennoic uses PostgreSQL instead. Account deletion from **Settings** removes the account and its server-stored records. Browser-only copies on other devices are not deleted remotely.

It uses an LLM to interpret what you type and convert it into structured task or memory actions. Memory changes are stored in the configured database for the signed-in account. Existing `memory.jsonl` files remain unchanged and are not automatically shown to any account.

The current web app uses Groq's cloud API by default. The Ollama helper remains in the codebase, but the web app does not currently expose a setting to switch providers. Cloud prompts are sent to Groq, so avoid entering sensitive information unless that use is appropriate for you.

One thing to keep in mind: the LLM can misunderstand what you type or extract the wrong information. So, even if the memory system works correctly, it might store something that wasn't what you meant.

---

## Memory Flow

1. You type something like, *"Remember that Alex has the keys tomorrow."*

2. The AI parses it into structured JSON data:

   ```json
   {"action": "remember", "intent": "...", "person": "Alex", "object": "keys", "time": "tomorrow"}
   ```

   The exact fields depend on what the AI extracts from your input.

3. After you sign in, the memory is saved to your account's database records.

4. Next time you ask, *"Who has the keys?"*, it looks up the account's saved records and answers something like, *"Alex has the keys."*

---

## Memory Actions

- **remember**: store new info or update existing info
- **ask**: retrieve info (like who has something or when something happens)
- **forget**: remove matching information from stored memory
- **unknown**: inputs that don't fit the memory system, like gibberish, greetings, or unrelated questions

`unknown` doesn't mean Ennoic can answer random questions like a general-purpose chatbot. It just means the input doesn't fit the memory actions it currently supports.

---

## Memory Storage

New persistent memory is stored in the configured database and scoped to the signed-in account. Older installations may still have a `memory.jsonl` file; Ennoic leaves it in place. A simplified legacy entry looks like this:

```json
{"person": "Alex", "object": "keys", "time": "tomorrow"}
```

When migrating legacy files, Ennoic imports the person, object, and time fields into the configured database. If you say, *"Alex has the keys next Friday"* and that person/object combination is already stored, Ennoic updates the existing record instead of creating a duplicate.

When you use the forget action, Ennoic removes matching records from the current account's memory. The original JSONL file is left unchanged by migration.

---

## AI provider

The current web app uses Groq's API with `openai/gpt-oss-120b` for natural-language task interpretation and chat. It requires an API key and an internet connection. Prompts and relevant conversation context are sent to Groq; avoid entering sensitive information unless that use is appropriate for you. Ollama helper code exists, but the web app currently has no setting to switch to local model processing.

---

## Setup

You'll need:

- Python 3.10 or newer
- The packages in `requirements.txt`
- A `GROQ_API_KEY` environment variable for chat and natural-language task interpretation
- SMTP email settings for email/password registration verification and password-reset delivery
- Google OAuth web-client credentials if you want Google sign-in

The dashboard's manual task management does not require model access. New email/password registrations cannot complete without working SMTP delivery; existing accounts can still sign in. Google sign-in does not depend on SMTP.

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

### Setting up your API key

Get an API key from [Groq Console](https://console.groq.com/keys). Keep it private and **never put it directly into your source code or commit it to GitHub**.

On Windows PowerShell, you can set it for your current terminal session like this:

```powershell
$env:GROQ_API_KEY = "your_api_key_here"
```

On Linux, you can set it for your current terminal session like this:

```bash
export GROQ_API_KEY="your_api_key_here"
```

Replace `your_api_key_here` with your actual key. These commands set the variable for the current terminal session. If you want it available permanently, configure a user environment variable through your operating system's settings.

### Running Ennoic

From the project directory, set your key in the current PowerShell session, then run the Flask web app:

```powershell
$env:GROQ_API_KEY = "your_api_key_here"
python app.py
```

Open `http://127.0.0.1:5000`. On Linux or macOS, use `export GROQ_API_KEY="your_api_key_here"` before `python app.py`.

Set `ENNOIC_TASK_DB` to choose a different SQLite file path. The default local database is created next to `app.py`. Set `DATABASE_URL` to use PostgreSQL; use Neon's pooled connection string for the running app. Ennoic creates or updates its tables when the app starts. A persistent Flask secret key is generated under the ignored `instance/` directory for local development; set `ENNOIC_SECRET_KEY` to a long, random value for deployments and keep it private.

### Deploy with Neon and Render

The repository includes a Render Blueprint in `render.yaml`. It deploys the Flask app from the `vibecoded` branch on a free Render web service and connects it to Neon PostgreSQL. Render's free web service sleeps after 15 minutes without traffic and has a temporary filesystem, so production data must stay in Neon rather than a local SQLite file or uploaded local files. The first request after sleep can take about a minute. See [Render's free service limits](https://render.com/docs/free).

1. In Render, choose **New > Blueprint**, connect `Lionshaft-pixel/ennoic`, and select the `vibecoded` branch. Render reads `render.yaml` for the service configuration.
2. When asked for `DATABASE_URL`, copy the pooled production connection string from Neon (**Project > Connect > production > Pooled connection**). Keep the value private. `ENNOIC_SECRET_KEY` is generated by Render.
3. In the service's **Environment** page, add `GROQ_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, `RAZORPAY_PRO_PLAN_ID_INR`, `RAZORPAY_PRO_PLAN_ID_USD`, and `ENNOIC_ADMIN_EMAILS`. Do not put these secrets in GitHub. Render Free blocks outbound SMTP, so account verification and password reset use Resend's HTTPS API; sending from a real Ennoic address requires a verified domain. [Resend domain setup](https://resend.com/docs/dashboard/domains/introduction)
4. After Render creates the service URL, add `https://<your-service>.onrender.com/auth/google/callback` to the Google OAuth client's authorized redirect URIs. Configure the Razorpay webhook as `https://<your-service>.onrender.com/api/razorpay/webhook` and use the same webhook secret in Render.
5. Open the Render service URL and check sign-in, task creation, conversations, memories, password reset email, and checkout setup.

`tasks.sqlite3` stays on your computer and is not copied to Neon automatically. If you have local accounts or data you want on the hosted app, migrate it before inviting those users; otherwise, the hosted database starts empty.

### Email delivery

Registration requires email verification. Password-reset requests also send a one-time, expiring link. The app loads a `.env` file from the repository root when started with `python app.py`; shell environment variables take precedence. Email can be sent with Resend's HTTPS API or SMTP. Resend is used when `RESEND_API_KEY` and `RESEND_FROM_EMAIL` are set; otherwise the app falls back to SMTP.

For Render Free, use Resend because Render blocks outbound SMTP. Create a Resend API key and verify a sending domain in its dashboard, then set these values in `.env` locally or the Render service environment. A verified domain is needed for sending to real users. Never share or commit the key.

```dotenv
RESEND_API_KEY=re_your_api_key
RESEND_FROM_EMAIL=Ennoic <no-reply@your-domain.example>
```

For local development, SMTP remains available as a fallback. Port 587 uses verified STARTTLS by default; use port 465 with `SMTP_USE_SSL=true` for SMTP-over-TLS. `PUBLIC_URL` can override the origin used in verification and reset links; otherwise the app uses the request's public URL. Set `ENNOIC_TRUST_PROXY=true` only when the app is behind the one trusted Render proxy, so secure URLs and client IP-based limits work correctly. For deployments with multiple workers or instances, configure `ENNOIC_RATE_LIMIT_STORAGE` to a shared Redis-compatible store; the default in-memory limiter is intended for a single instance.

For local Gmail SMTP, enable 2-Step Verification and create a Google **App Password**; use it as `SMTP_PASSWORD`, not your normal Google password. Use your full Gmail address as `SMTP_USERNAME` and usually as `SMTP_FROM_EMAIL`.

Google sign-in requires a Google OAuth client with the **Web application** type. Configure the OAuth consent screen in Google Cloud Console and add the redirect URI `http://127.0.0.1:5000/auth/google/callback` (use the matching host and scheme for your deployment). Add these values to `.env`:

```dotenv
GOOGLE_CLIENT_ID=your-google-oauth-client-id
GOOGLE_CLIENT_SECRET=your-google-oauth-client-secret
```

The client secret is private: keep it out of source control. Install dependencies with `python -m pip install -r requirements.txt` and restart Ennoic. Google sign-in accepts verified Google email addresses and automatically creates a verified Ennoic account when one does not already exist. If an Ennoic account already uses that verified address, Google sign-in opens the existing account.

Email delivery diagnostics are written to the terminal running Ennoic. On failure, look for `account_email_delivery_failed`; logs omit the recipient address, API key, password, and verification URL. A successful API or SMTP send means the provider accepted the message, not necessarily that it reached the inbox; check spam and provider delivery status too.

### Plans and entitlements

Every account starts on Free. The server enforces monthly AI request limits, memory capacity, and the number of conversations visible in history. Free defaults are 40 AI requests per UTC calendar month, 40 saved memories, and up to 10 conversations in history. Pro defaults are 1,000 AI requests, 1,000 memories, and full searchable history. Basic task management remains available on both plans. Usage is tracked in the configured database; repeated chat submissions with the same request ID reuse the saved result instead of consuming another request. Failed processing releases the reservation so the user can retry.

Limits and prices are centralized in `entitlements.py` and can be overridden in `.env` with the `ENNOIC_FREE_*`, `ENNOIC_PRO_*`, and `ENNOIC_PRO_PRICE_*` settings shown in `.env.example`. Change the India price with `ENNOIC_PRO_PRICE_INR_MINOR` (paise; `17900` means ₹179) and the USD price with `ENNOIC_PRO_PRICE_USD_MINOR` (cents; `499` means $4.99). The pricing page and checkout use the same server-selected currency: India receives INR, other regions USD. The app uses `Accept-Language` as a region hint; for IP-based country selection, set `ENNOIC_COUNTRY_HEADER` only when the app is behind a proxy that overwrites that header. Conversation history outside the Free window stays stored and becomes visible again if Pro is granted. Memory limits block new memories while preserving existing records. Attachments, file analysis, and advanced planning do not exist in the app yet; their entitlement settings are prepared, but they are not offered as available features.

Razorpay subscriptions are integrated in test mode when the Razorpay credentials and plan IDs are configured. Set `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, and the matching `RAZORPAY_PRO_PLAN_ID_INR` or `RAZORPAY_PRO_PLAN_ID_USD` in the deployment's secret settings. The Razorpay plan amount and currency must match the app's `ENNOIC_PRO_PRICE_*_MINOR` settings. In Razorpay's Webhooks page, set the webhook URL to `https://YOUR-DOMAIN/api/razorpay/webhook`, subscribe to `subscription.activated`, `subscription.resumed`, `subscription.charged`, `subscription.cancelled`, `subscription.completed`, `subscription.halted`, and `subscription.paused`, then copy Razorpay's webhook secret into `RAZORPAY_WEBHOOK_SECRET`. Keep the webhook secret separate from the API key secret. A successful checkout signature is verified in the browser callback, but Pro access is granted only after a valid Razorpay webhook confirms the subscription lifecycle event. Verify the webhook delivery and subscription state before enabling live payments.

The checkout page can save a user's name, contact email, and phone number to that account. No request or client-side value can activate Pro. Before launch, configure production credentials and prices, verify webhook delivery and idempotency, and complete legal/tax and refund setup.

### User administration

Set `ENNOIC_ADMIN_EMAILS` in `.env` to a comma-separated allowlist of account sign-in emails. Those accounts can view `/admin/users`, which lists the account email and ID, verification and plan status, saved checkout contact details, and last sign-in time and IP. The IP is the address Flask sees; configure trusted proxy handling before relying on it. The app stores only the last sign-in IP, not an IP history. Checkout contact data is account-linked and is removed when the account's scheduled deletion is purged. Admin access is server-side and an unlisted account receives a 404.

### Importing existing local data

Existing tasks and SQLite memories are migrated to nullable owner columns and remain hidden from all accounts until deliberately assigned. Existing `memory.jsonl` and browser-local conversations are not automatically imported.

1. Register an account and verify its email, or sign in with Google to create a verified account automatically.
2. Stop the app and back up its database.
3. Run `python migrate_legacy_data.py`, enter the verified email that should receive the old local tasks and memories, and confirm the exact assignment.
4. Sign in as that account to view the migrated tasks and memories. Browser-only conversations remain in their original browser and are not imported automatically.

The migration script creates a timestamped SQLite backup before assigning unowned rows. Do not run it on a shared deployment unless you intentionally want to assign all unowned legacy task and memory rows to that one account.

### Task management

- Use **New task** to create a task directly, or describe one or more explicit tasks in the chat.
- Task records can be edited, completed, reopened, deleted, and grouped under a project or client. Use chat to find tasks, change a deadline or other details, or break a task into subtasks.
- When a new task resembles existing work, Ennoic shows the existing match and asks whether to update it, create a separate task, or cancel. Choosing update preserves the existing title and all details that were not specified in the new request. Clear edit requests still update the named task directly.
- Dashboard views include due today, upcoming, overdue, all open, and completed. Search covers task titles, notes, and project names.
- Task records are validated and persisted by the application; the language model only interprets requests. Ambiguous task references are not changed and prompt for clarification.
- Task, conversation, and memory APIs require an authenticated account and scope reads and writes by its server-side user ID.

### Interface and preferences

- The task dashboard can be collapsed from its heading and reopened from the workspace bar. Drag the divider between tasks and chat to resize the panels; keyboard users can focus the divider and use the arrow keys, Home, and End. On narrower screens the panes stack and the divider adjusts their heights.
- Conversation menus support account-persisted renaming, pinning, and confirmed deletion. Pinned conversations are sorted above the others. **Settings** includes language preferences, sign-out, and account deletion. Deletion disables sign-in immediately, revokes existing sessions, and queues server data for permanent deletion after 30 days. The account owner can cancel during that period by signing in again. A database-backed worker processes due deletions in batches, so the request returns without waiting for the data purge. The audit record retains the account email and server-observed remote IP for 90 days after the purge (or cancellation), then removes it. Browser-only copies on other devices and retained database backups must be handled separately.
- Email verification and password-reset tokens are invalidated when deletion is scheduled. The app does not persist Google refresh tokens or user-generated API keys.
- **Settings → General** supports English, Spanish, and French for the interface. Preferences remain in this browser and do not modify stored account data. AI-generated replies are not automatically translated; unsupported language preferences fall back to English.

### Tests

Run the authentication, account-isolation, task storage/API, and conversational-operation tests with:

```powershell
python -m unittest discover -s tests -v
```

---

## What it can't do

Ennoic is an early prototype, so there are still limitations.

- Ennoic has account authentication, scheduled account deletion, and ownership checks, but it is still a prototype and is not production-ready. Public deployment needs HTTPS, managed secret storage, reliable email delivery, backup retention and deletion procedures, and a correctly configured production WSGI server and trusted proxy. Audit IP addresses reflect the address Flask sees; configure trusted proxy handling before relying on them as client IPs.
- Local SQLite is not automatically backed up. Razorpay subscriptions are integrated, but webhook delivery and live payments still need deployment-specific setup and verification.
- Rate limits are IP-based. Configure the deployment's trusted proxy chain carefully so the app sees the correct client address; do not trust arbitrary forwarded headers.
- Natural-language task extraction depends on the configured model; review extracted tasks and deadlines.
- Prompts and conversation context are sent to Groq. Avoid sending sensitive content unless that use is acceptable to you.

### Hosting limitations

Render Free web services sleep after 15 minutes idle, take about a minute to wake, and have ephemeral filesystems. Neon keeps the database outside the Render instance. Render also blocks outbound SMTP ports 25, 465, and 587, so deployments use the Resend HTTPS API for account emails. A verified sender domain is needed for real-user email delivery; the test sender is for development. [Render free limits](https://render.com/docs/free) · [Resend test emails](https://resend.com/changelog/sending-test-emails)
