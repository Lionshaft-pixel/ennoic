# Ennoic PWA implementation and test report

Audited 27 November 2026 against original production commit `9c0736fd9d28a90065de9679b06101bbce42433f` on `vibecoded`. The user authorized the verified production push and a local Android test build. The initial 36-file implementation was reviewed in full, including the uncommitted diff, routes, templates, branding, public cache boundary, deployment configuration, tests, scripts, and Android preparation. Production/build outcomes are recorded in the release verification section below.

## Behavior

Ennoic keeps its Flask backend, existing routes, responsive layouts, accounts, task logic, AI prompts, billing, and database schema. The new public resources are isolated in a Flask blueprint. Static/PWA resource requests no longer need to initialize the account database or start its background worker.

`/manifest.webmanifest` declares Ennoic, stable app identity `/`, start URL `/`, scope `/`, and `standalone` display. Black `#0b0504` matches the existing landing background; orange `#e8650f` is used on the offline retry control. The browser/OS controls the actual installed window and splash presentation.

All icons derive from **the existing `static/email/ennoic-logo.png` wordmark**. Only empty margins are cropped, then the original image is resized/padded on white. No new symbol, font, or logo was introduced. PNGs include ordinary 192/512, separate maskable 192/512, and Apple 180 icons. Maskable artwork fits within the central 80%-diameter safe circle. The original source image is unchanged. Regenerate with `python scripts/generate_pwa_icons.py` (optional development dependency: Pillow).

An optional install control appears in the landing footer, app sidebar, and Settings > General when Chromium exposes `beforeinstallprompt`. Unsupported browsers continue normally. On iOS it shows expandable Safari Add to Home Screen instructions. Installed standalone windows hide the control. There is no automatic installation, push registration, or notification permission request.

The landing and sign-in shells replace their document with saved HTML. They now preserve PWA metadata/styles and notify the install controller after replacement. Delegated installation clicks survive the landing renderer recreating elements. Existing bundle rendering and authentication integration remain in place. Script nonces are retained where required; CSP explicitly permits same-origin workers and manifests without adding `unsafe-eval` or new external permissions.

## Offline and update policy

`/service-worker.js` is served at the origin root with scope `/`, `Service-Worker-Allowed: /`, JavaScript MIME type, and revalidation headers. Registration uses `updateViaCache: none`; it also checks on page startup, returning to a visible window, and network recovery.

The **only** Cache Storage entries created are:

```text
/offline
/static/pwa/offline.css
/static/pwa/offline.js
/static/email/ennoic-logo.png
```

Installation fetches them without credentials, rejects redirects/failed responses, and uses the versioned `ennoic-public-offline-v1` cache. Activation removes only obsolete caches with that prefix. Bump the cache version whenever any of those four public assets changes.

Normal application HTML, CSS, JavaScript, API responses, tasks, conversations, credentials, auth endpoints, checkout/payment data, and external resources are **never written to Cache Storage**. No runtime response caching or offline mutations occur. Auth/API/payment requests bypass the worker; their original handlers continue to run. The worker only supplies the generic offline page on a network failure navigating to `/`, `/settings`, `/pricing`, `/terms`, `/privacy`, or `/refund-policy`, without query parameters. HTTP error responses are not replaced. Other URLs, including authentication/token URLs, retain ordinary browser offline failures.

Offline users can read the generic message and retry. Tasks and chat require a connection. Network recovery displays a retry message; it does not replay requests or submit unsent content. Browser eviction/private-mode restrictions can remove or prevent offline support, while online browser operation remains available.

Worker releases activate immediately because this worker does not cache the application UI. They do **not** force a page reload or discard text being typed. Fresh app pages/scripts come from the existing server on navigation/reload; a long-open page retains its current UI until then. Keep the worker URL stable. Existing browser storage used by Ennoic is unchanged.

Sessions still use the existing secure-cookie configuration and 12-hour permanent-session lifetime. No authentication tokens move into the manifest, worker, or local installation state. A new OS/browser web-app container may require initial sign-in; device testing must confirm OAuth handoff and return. Keep the existing production Google callback `https://ennoic.onrender.com/auth/google/callback`.

## Validation performed

All Python/browser runs used temporary SQLite data, suppressed loading `.env`, and avoided production credentials. Browser chat used a mocked AI provider with the real Flask endpoint, conversation persistence, and frontend. Tests did not create live charges or send messages to external services.

| Check | Result |
| --- | --- |
| Final full Python suite | **117/117 passed**, including the 4 new PWA tests and both corrected baseline tests (124.790 seconds) |
| Original failure reproduction | Both previously failing tests fail on an isolated clean archive of original commit `9c0736f`, and before test corrections in the PWA working tree; neither was a PWA regression |
| New Flask PWA tests | 4 passed: manifest/icon dimensions, MIME/cache/scope/CSP headers, metadata/session preservation, public resources without DB/cookies, DAL validation |
| Existing Node tests | 6 passed |
| New worker Node tests | 5 passed: exact credential-free precache, auth/API/payment bypass, fresh navigation/offline recovery, cache cleanup, storage-eviction fallback |
| Chromium installability | DevTools `Page.getInstallabilityErrors` returned no errors in a normal persistent Edge/Chromium profile |
| Install option | User-action prompt flow passed with a simulated browser install event; no native mobile install claimed |
| Desktop browser flow | Login/logout using existing forms; create/edit/complete/delete disposable tasks; real chat endpoint with mocked AI replies; two saved conversations and history switching; settings; close/reopen with session/chat preserved |
| Android viewport | Chromium Pixel 7 emulation: landing, login, authenticated task/chat view switching, no horizontal overflow |
| iOS compatibility | iPhone user-agent/viewport emulation: Apple metadata, expandable Safari instructions, install control hidden in simulated standalone mode; actual Safari not exercised |
| Offline/recovery | Generic fallback rendered; all supporting assets available; reconnect and retry restored authenticated app |
| Cache privacy | Inspected Cache Storage after authenticated tasks/chat; exactly the four public resources above |
| Worker update | Served changed worker bytes/cache version in isolated test server; verified controller change, old-cache removal, and retained chat without page reload |
| Icon assets | Dimensions, opaque backgrounds, maskable safe-circle geometry, and rendered appearance checked |
| Syntax/whitespace | Python compilation, JS syntax checks, and `git diff --check` passed |

### Resolution of the two original failures

Both failures were reproduced again in a temporary `git archive` of **unchanged `9c0736f`**, without local `.env`, production data, or PWA files. The PWA working tree initially ran 117 tests with the same two failures and no errors. These were stale test expectations, not environment/dependency failures or PWA regressions.

| Original failure | Root cause | Low-risk correction |
| --- | --- | --- |
| `AccountAuthenticationTests.test_public_landing_links_to_account_forms` | Commit `9c0736f` deliberately removed the landing setup section and its CSS selector; the test still expected `#setup h2` | Updated only the expected selector list to the existing sections. No section or styling was restored. |
| `EntitlementTests.test_failed_ai_attempt_releases_usage_and_same_request_can_retry` | Commit `0d284cc` added a successful `chat_response` notification. The retry test expected a single call across an error and a successful retry. | Assert exactly `chat_error` then `chat_response`; keep privacy checks scoped to the error notification/log and verify the recovered response. Quota assertions remain intact. No notification or account logic changed. |

Existing tests still cover CSRF rejection, rate limiting, Google OAuth's canonical redirect and mocked callback, account isolation, session invalidation, task APIs, plan enforcement, and billing provider mocks. No browser security flags were disabled for PWA tests.

## Release verification

Local release audit completed: **117 Python tests and 11 Node tests pass**, browser regression checks pass with no page errors, icon regeneration is deterministic, and syntax/whitespace checks pass. A filename/content-pattern audit found no sensitive files or credentials in the intended changes. Render continues to use the existing `vibecoded` branch and Gunicorn/Python build. Runtime dependencies, database schemas, pricing, entitlements, AI prompts, and existing account/payment logic are unchanged. No signing material, real environment files, local data, or generated builds are included in Git.

### Deployment held: existing production readiness failure

At approximately **01:52 IST on 27 November 2026**, before any commit was pushed or any deployment triggered, repeated read-only checks of the existing production service returned:

| Production path | Result |
| --- | --- |
| `/healthz` | 200, `{"status":"ok"}` |
| `/readyz` | Repeated 503, `{"status":"not_ready"}`; also an earlier timeout |
| `/`, `/login`, `/pricing` | 200 |
| `/api/tasks` without authentication | 401, expected |
| `/api/auth/me` without authentication | 200, expected anonymous response |
| `/manifest.webmanifest`, `/service-worker.js`, `/.well-known/assetlinks.json` | 404, because the PWA has not been deployed |

The remote production branch remains `9c0736fd9d28a90065de9679b06101bbce42433f`. The readiness failure therefore predates these changes. The existing readiness implementation performs a bounded database connection and `SELECT 1`, then deliberately returns a generic failure without private error details. The public response establishes a failed readiness probe, **not its root cause** or proof that every database operation is unavailable. Landing/login HTML availability does not establish that authenticated database operations are healthy.

Because this could indicate production database danger, the user's explicit release-blocking condition applies: **no push and no deployment** until the failure can be explained or resolved safely. The implementation is preserved in a local commit on `vibecoded`; use `git log -1` for that checkpoint's exact hash. No history was rewritten and no remote work was overwritten.

The Render plugin was found but is not installed/connected; no connected browser/dashboard session or Render API credential was available. GitHub's combined commit status exposed no deployment statuses. The [Render](https://status.render.com/) and [Neon](https://neonstatus.com/) public status pages reported operational services during investigation, which does not diagnose this specific app. No production environment variables, credentials, account data, or database settings were changed. No actual customer login, AI request, charge, email, or data mutation was used for production testing.

To resume under the existing authorization:

1. Install/connect Render access, inspect the service logs around `database_readiness_failed`, and determine the actual exception/configuration/connectivity issue. Do not expose database URLs or credentials in logs or documentation.
2. Resolve or explain the readiness failure without hiding it, weakening checks, altering customer data, or making unrelated account/schema changes. Verify `/readyz` returns 200 reliably, or establish an explicitly justified safe resolution.
3. Fetch `origin/vibecoded` again, reconcile any new remote work safely, run checks appropriate to any fixes, then push the verified local commit normally. Do not force-push.
4. Verify Render's resulting deployment, health/readiness, exact PWA resources and browser behavior. Then proceed to the Android test build.

Local QA evidence is retained under ignored `instance/pwa-qa/`, including `python-before-audit.json`, `original-head-failures.log`, `python-final.json`, `browser-audit.log`, `results.json`, screenshots, and `production-release-gate.json`. Logs and generated evidence are not committed.

Run the usual suites with environment loading disabled and local test configuration, as described below. The browser script sanitizes its own environment automatically:

```powershell
# Optional browser tooling, separate from production requirements:
python -m pip install playwright
python -m playwright install chromium
python scripts/pwa_browser_smoke.py

node --test tests/*.test.cjs
```

On Windows the browser script uses installed Edge when available; otherwise it uses Playwright Chromium. QA screenshots/results are written to ignored `instance/pwa-qa/`. Playwright and Pillow are development tools only; Flask runtime requirements and Render commands are unchanged.

For a full isolated Python run without loading local secrets:

```powershell
@'
import os, tempfile, unittest
import dotenv
dotenv.load_dotenv = lambda *a, **k: False
for key in list(os.environ):
    if key.startswith(('ENNOIC_', 'GOOGLE_', 'GROQ_', 'PADDLE_', 'RAZORPAY_', 'SMTP_', 'RESEND_')) or key in {'DATABASE_URL', 'DISCORD_WEBHOOK_URL'}:
        os.environ.pop(key, None)
os.environ['ENNOIC_SECRET_KEY'] = 'isolated-test-secret-key-over-32-characters'
with tempfile.TemporaryDirectory() as directory:
    os.environ['ENNOIC_TASK_DB'] = os.path.join(directory, 'fallback.sqlite3')
    result = unittest.TextTestRunner().run(unittest.defaultTestLoader.discover('tests'))
    raise SystemExit(not result.wasSuccessful())
'@ | python -
```

## Physical-device checks after deployment

1. Confirm the HTTPS manifest, worker, all icon files, and offline resources return 200 directly with correct MIME types. Confirm the worker controls `/`. Current production checks found the new root resources absent (404), as expected before deployment.
2. On Android Chrome, install from the optional control or browser menu. Launch from the home-screen icon and check that the address bar is absent, the actual wordmark is legible, and the OS splash/background looks correct. Test portrait/landscape and keyboard use.
3. On iPhone/iPad Safari, use Share > Add to Home Screen, retain Open as Web App if offered, then Add. Launch from the icon and inspect icon/splash/status-bar appearance and safe areas. Follow [Apple's instructions](https://support.apple.com/en-ie/guide/iphone/iphea86e5236/27/ios/27).
4. In both installed apps, sign in/out, perform a real Google sign-in and return, create/edit/complete tasks, send an authorized live AI request, open settings and billing/customer portal, then close/reopen. Verify expected session expiry. Test payments in an appropriate sandbox; do not create a live charge merely for installation testing.
5. With an established worker, go offline and relaunch from the icon. Verify only the generic fallback is available, API requests fail normally, and no private data appears in Cache Storage. Reconnect and retry. Offline support requires a prior successful online worker installation.
6. Deploy a subsequent approved public-offline version, increment the cache version, and return to the app. Confirm the new worker/cache, removal of its obsolete cache, fresh app files on navigation, and no forced reload of a draft. Check another desktop browser and actual Safari as well as Chromium.

Physical Android/iOS installation, native standalone windows/splashes, real provider redirects, and signed APK validation remain manual. No real mobile device was connected for this task.

## Android APK handoff

See [android/README.md](../android/README.md) for exact prerequisite findings, commands, signing/keystore management, and release steps. No APK, signing key, or password was generated. The user authorized safe prerequisite installation, but the requested Android stage follows a successful PWA deployment. Because the production readiness gate failed before pushing, Android tool downloads/configuration and APK generation were not started. First resolve the deployment blocker; then use a supported JDK/SDK setup and a local testing signing identity. A test certificate does not establish verified production TWA fullscreen behavior.

The provisional Android ID is `com.ennoic.app`. `/.well-known/assetlinks.json` is implemented and returns an empty array until valid public certificate fingerprints are configured. `.env.example` documents `ENNOIC_ANDROID_PACKAGE_ID` and `ENNOIC_ANDROID_SHA256_FINGERPRINTS`. Digital Asset Links is unnecessary for ordinary PWA installation; it is needed for verified TWA behavior.

## Exact file changes

Modified:

- `.env.example` — optional public Android verification settings; `.env` was not edited or read.
- `.gitignore` — signing/build artifact exclusions.
- `README.md` — links to the PWA report and Android guide.
- `app.py` — register public blueprint, exempt static/PWA requests from DB initialization, allow same-origin workers/manifests in CSP.
- `static/landing-page.html` — optional install slot in footer.
- `tests/test_accounts.py`, `tests/test_entitlements.py` — correct the two proven pre-existing stale assertions described above.
- `templates/admin_users.html`, `templates/authentication.html`, `templates/checkout_success.html`, `templates/index.html`, `templates/landing.html`, `templates/legal_page.html`, `templates/pricing.html`, `templates/settings.html` — shared PWA head; landing/auth preserve it across document replacement; app/settings provide optional install slots.

Added:

- `pwa.py` — manifest, worker, offline, and Digital Asset Links routes.
- `templates/_pwa_head.html` — manifest, icons, Apple metadata, theme, and PWA client assets.
- `static/pwa/manifest.webmanifest`.
- `static/pwa/service-worker.js`.
- `static/pwa/pwa.js`, `static/pwa/pwa.css` — optional installation and worker registration/update checks.
- `static/pwa/offline.html`, `static/pwa/offline.css`, `static/pwa/offline.js` — public offline fallback.
- `static/pwa/icons/icon-192.png`, `static/pwa/icons/icon-512.png`.
- `static/pwa/icons/maskable-192.png`, `static/pwa/icons/maskable-512.png`.
- `static/pwa/icons/apple-touch-icon.png`.
- `scripts/generate_pwa_icons.py` — reproducible derivation from the existing wordmark.
- `scripts/pwa_browser_smoke.py` — isolated optional browser regression checks.
- `tests/test_pwa.py`, `tests/pwa_worker.test.cjs` — focused automated coverage.
- `android/.gitignore`, `android/package.json`, `android/ennoic.config.json`, `android/README.md` — separate Android packaging preparation.
- `docs/PWA.md` — this report.

References: [Chromium install criteria](https://web.dev/articles/install-criteria), [Chrome installation guidance](https://developer.chrome.com/docs/modern-web-guidance/guides/pwa/install-web-app), [service-worker lifecycle](https://web.dev/articles/service-worker-lifecycle), [browser installation prompts](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/How_to/Trigger_install_prompt). Android-specific primary references are linked in its guide.
