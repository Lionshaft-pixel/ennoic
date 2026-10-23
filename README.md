# Ennoic

Ennoic is a local-first task manager with a conversational interface, built on the existing memory assistant. Describe work in ordinary language to create tasks, then manage projects, deadlines, priorities, and completion from the dashboard or chat. Existing memory actions remain available.

Originally, the idea was to build this for mobile devices since, you know, billions of people use them. So I started with Python because that's what I know.

---

## What It Does

Ennoic supports two related workflows:

- **Tasks:** create and manage tasks from a dashboard or natural-language conversation. Tasks can have notes, a project or client, a deadline, priority, and subtasks.
- **Memory:** store, retrieve, and forget personal facts using the existing memory assistant.

Tasks, conversations, memories, and accounts are stored in the same local SQLite database (`tasks.sqlite3` by default), with server-side ownership checks for every account-scoped operation. Browser conversation history from earlier versions is retained in browser storage until you explicitly import it from **Settings → Account**.

It uses an LLM to interpret what you type and convert it into structured task or memory actions. Memory changes are stored in SQLite for the signed-in account. Existing `memory.jsonl` files remain unchanged and are not automatically shown to any account.

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

3. After you sign in, the memory is saved to your account's SQLite records.

4. Next time you ask, *"Who has the keys?"*, it reads the file, finds the match, and answers something like, *"Alex has the keys."*

---

## Memory Actions

- **remember**: store new info or update existing info
- **ask**: retrieve info (like who has something or when something happens)
- **forget**: remove matching information from stored memory
- **unknown**: inputs that don't fit the memory system, like gibberish, greetings, or unrelated questions

`unknown` doesn't mean Ennoic can answer random questions like a general-purpose chatbot. It just means the input doesn't fit the memory actions it currently supports.

---

## Memory Storage

New persistent memory is stored in SQLite and scoped to the signed-in account. Older installations may still have a `memory.jsonl` file; it remains in place until you explicitly import it using the migration procedure below. A simplified legacy entry looks like this:

```json
{"person": "Alex", "object": "keys", "time": "tomorrow"}
```

The actual structure can also include fields such as `action` and `intent`, depending on how the information is processed.

So if you say, *"Alex has the keys next Friday"* and Alex already has the keys stored, it updates the time instead of creating a duplicate entry. If it's a new person/object combination, it appends a new line.

When you use the forget action, Ennoic removes matching records from the current account's SQLite memory. The original JSONL file is left unchanged by migration.

---

## LLM Modes

**Local mode** uses Ollama at `http://localhost:11434/api/generate` with a small model like `qwen2.5:1.5b` (as configured in the code). Good for keeping model processing on your own machine, but smaller models can struggle with complicated inputs.

**Cloud mode** uses Groq's API with `openai/gpt-oss-120b`, which can handle more complicated inputs, but it requires an API key and an internet connection. Your prompts are sent to the cloud provider when you use this mode, so keep that in mind if you're storing sensitive information.

---

## Setup

You'll need:

- Python 3.10 or newer
- The packages in `requirements.txt`
- A `GROQ_API_KEY` environment variable if you're using cloud mode
- SMTP email settings for account verification and password-reset delivery

Cloud mode is the default in the current code. Natural-language chat and task interpretation require a valid Groq API key. The dashboard's manual task CRUD does not require model access.

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

Set `ENNOIC_TASK_DB` to choose a different SQLite file path. The database contains account, task, conversation, and memory records. The default database is created next to `app.py`. A persistent Flask secret key is generated under the ignored `instance/` directory for local development; set `ENNOIC_SECRET_KEY` to a long, random value for deployments and keep it private.

### Email delivery

Registration requires email verification. Password-reset requests also send a one-time, expiring link. The app loads a `.env` file from the repository root when started with `python app.py`; shell environment variables take precedence. The setting names in this section accept the `ENNOIC_SMTP_*` prefix too. Without SMTP host and sender settings, account creation is disabled and verification/reset tokens are never exposed in the browser or logs.

Copy [`.env.example`](./.env.example) to `.env` in the project root, then replace the example values with the SMTP settings from your provider. Do not add quotes unless needed by the value, and never share or commit `.env`. For example:

```dotenv
SMTP_HOST=smtp.your-provider.example
SMTP_PORT=587
SMTP_USERNAME=your-smtp-username
SMTP_PASSWORD=your-smtp-password
SMTP_FROM_EMAIL=Ennoic <no-reply@your-domain.example>
SMTP_USE_SSL=false
PUBLIC_URL=http://127.0.0.1:5000
```

Port 587 uses verified STARTTLS by default; use port 465 with `SMTP_USE_SSL=true` for SMTP-over-TLS. `PUBLIC_URL` must be the address users can reach to open verification and reset links. Keep SMTP credentials in `.env` for local development or a secrets manager for deployment. Set `ENNOIC_COOKIE_SECURE=true` when serving over HTTPS. For deployments with multiple workers or instances, install the Redis client (`python -m pip install redis`) and configure `ENNOIC_RATE_LIMIT_STORAGE` to a shared Redis-compatible store; the default in-memory limiter is only suitable for local development and tests.

For Gmail, enable 2-Step Verification and create a Google **App Password** for SMTP; use that app password as `SMTP_PASSWORD`, not your normal Google password. Use your full Gmail address as `SMTP_USERNAME` and typically as `SMTP_FROM_EMAIL`. After changing `.env`, restart Ennoic and use **Need another verification email?** on the sign-in page to resend the verification link.

Email delivery diagnostics are written to the terminal running Ennoic. On failure, look for `account_email_delivery_failed`; its `stage` identifies configuration, connection, TLS, authentication, or message submission. Logs intentionally omit the recipient address, password, and verification URL. A successful SMTP send means the provider accepted the message, not necessarily that it reached the inbox; check spam and provider delivery status too.

### Importing existing local data

Existing tasks and SQLite memories are migrated to nullable owner columns and remain hidden from all accounts until deliberately assigned. Existing `memory.jsonl` and browser-local conversations are not automatically imported.

1. Register an account and verify its email.
2. Stop the app and back up its database.
3. Run `python migrate_legacy_data.py`, enter the verified email that should receive the old local tasks and memories, and confirm the exact assignment.
4. Sign in as that account. Use **Settings → Account → Import old browser conversations** in each browser where legacy history exists. The import copies conversations to the account and leaves the browser copy in place.

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
- Conversation menus support account-persisted renaming, pinning, and confirmed deletion. Pinned conversations are sorted above the others. **Settings → Account** includes password change, sign-out, legacy browser-conversation import, and account-scoped conversation deletion.
- **Settings → General** supports English, Spanish, and French for the interface. Preferences remain in this browser and do not modify stored account data. AI-generated replies are not automatically translated; unsupported language preferences fall back to English.

### Tests

Run the authentication, account-isolation, task storage/API, and conversational-operation tests with:

```powershell
python -m unittest discover -s tests -v
```

---

## What it can't do

Ennoic is an early prototype, so there are still limitations.

- Ennoic now has account authentication and ownership checks, but it is still a prototype and is not production-ready. Public deployment needs HTTPS, managed secret storage, reliable SMTP, backups, a shared rate-limit store, and a correctly configured production WSGI server and trusted proxy.
- SQLite is local to the app instance and is not automatically backed up. Account deletion, email changes, payments, and subscriptions are not implemented.
- Rate limits are IP-based. Configure the deployment's trusted proxy chain carefully so the app sees the correct client address; do not trust arbitrary forwarded headers.
- Natural-language task extraction depends on the configured model; review extracted tasks and deadlines.
- Prompts and conversation context are sent to Groq in cloud mode. Avoid sending sensitive content unless that use is acceptable to you.
