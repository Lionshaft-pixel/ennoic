# Ennoic PWA implementation and test report

## v0.2.1 mobile web polish

The v0.2.1 follow-up makes the composer more compact and follows keyboard/visual-viewport bounds, replaces native sign-out/account/task/conversation confirmations with accessible in-page modals, makes verification resend contextual, and contains vertical overscroll only in standalone windows. Authentication/deletion security, private-cache boundaries, native splash, package and certificate remain unchanged. See [the focused report](MOBILE_V021.md) for diagnosis, exact files, testing and physical-device checks.

Full isolated validation passed **135 Python tests and 31 Node tests**, plus 36 composer bounds checks. The existing APK receives these changes from the website; rebuilding it is unnecessary. Final browser/deployment results are recorded in that report after verification.

Audited 27 November 2026 and revalidated 28 November 2026 against original production commit `9c0736fd9d28a90065de9679b06101bbce42433f` on `vibecoded`. The PWA implementation is preserved in commit `b0a419b762ca63638a3fefaa3bf05b6b926b2c3c`. The user authorized the readiness fix, verified production push, and local Android test build. Routes, templates, branding, public cache boundaries, deployment configuration, tests, scripts, and Android preparation were reviewed. Production/build outcomes are recorded below.

## Previous mobile-polish release - 28 November 2026 (v0.2.0)

The follow-up adds `/app` installed launch, the original black-hole welcome artwork/direct email authentication, responsive chat spacing, immediate navigation feedback, accessible touch polish, clearer UPI disclosures and a white native splash. Browser `/` retains the existing marketing page. Prices, entitlements, database schemas and AI prompts are unchanged. A pre-existing Paddle cancellation transport URL was corrected with mocked regression coverage. See [the mobile report and exact file inventory](MOBILE_UX.md) and [provider findings/manual console steps](MOBILE_CHECKOUT_REVIEW.md).

- Full isolated Python suite: **133/133 passed**, zero failures/errors (122.517 s), including the follow-up scoped Paddle stylesheet CSP regression.
- Full Node suite: **19/19 passed**, including navigation behavior, worker privacy/offline, pricing region and deferred-SDK initialization.
- Full isolated Edge browser regression: **passed, no page errors**. Browser and installed email login/logout, verification/help navigation, mocked Google token/identity callback with the browser cookie jar, fixed `/app` return, task CRUD/completion, chat/history, settings, session reopening, five pricing viewports, generic offline/recovery, exact public cache, worker v1→v2 update without reload, installability, iOS instructions and simulated legacy standalone launch all passed. Held-response navigation feedback appeared in about 4.9 ms. Real Google/Paytm interaction and native standalone windows are device checks.
- Updated APK: **0.2.0-test / code 2**, `com.ennoic.app`, existing certificate preserved; 5,551,545 bytes. File SHA-256 `e70278e284be52ff94e8ae698ef47fc861f93e58bfe5dc9aa160e59fb5149497`.
- Native build: Gradle passed; actual package/version/SDK/launch `/app`, compiled white API-31 splash/vector, embedded white `/app` manifest, no notification permission and v2 signing verified. Previous code-1 APK retained locally.
- The test device is not connected here. This update's native splash, complete real provider handoffs and actual Realme network responsiveness require owner testing. The previous APK was tested by the owner on that tablet.

Code commit `23d436880f519d46ff3d40f0a756bb63684eb735` was pushed normally to `vibecoded` and Render deployed it successfully. The release was observed after two old-version polls. `/healthz` and `/readyz` each returned 200 three times. `/app` returned the welcome with `no-store`; ordinary `/` still served marketing in the same session. The live manifest exactly matched stable identity `/`, start `/app`, standalone mode and white background. The worker, PWA client, welcome assets, navigation script and responsive CSS matched the committed sources; root worker scope/revalidation headers remained correct. Pricing served the new disclosures/deferred SDK. Anonymous tasks remained 401, settings redirected to login, and Google authorization initiation retained the canonical callback and S256 PKCE.

Digital Asset Links returned 200 JSON without redirect/cookie and exactly matched the new APK's unchanged package/certificate. Google's `assetlinks:check` returned **`linked: true`**. Authenticated production data was not mutated; local regression covers authenticated behavior and mocks billable/provider transports. Production HTTP evidence is in ignored `instance/pwa-qa/production-mobile-verification.json`.

Live browser regression found an existing payment stylesheet CSP block. Follow-up commit `2acfbb4ed1b5f68d39906df938fda4a736e5aee3` permits only `https://cdn.paddle.com` in payment-page `style-src`, keeping other pages and the script/worker policy unchanged. Its new isolation test passed in the 133-test full suite. The follow-up was pushed normally and deployed; its exact payment style directive, repeated health/readiness, source resources, canonical Google initiation and `linked: true` DAL check passed again.

The final live persistent-Edge browser run passed installability (no DevTools errors), active root worker control, original browser marketing, new welcome/email reveal and pricing at 390×844, 800×1280, 1280×800 and 1440×1000, with no horizontal overflow, JavaScript page errors or CSP violations. Cache Storage contained exactly the four public resources. Going offline rendered the generic fallback; retry returned to `/app` through a simulated standalone bridge after restoring the network. Chrome's network emulation left `navigator.onLine` true in the offline document, so it did not reliably fire the cosmetic online-status event; the live retry worked, and actual device reconnect messaging remains manual. Local browser regression separately passed the status-event behavior. Evidence: ignored `production-mobile-browser.json` and screenshots.

Earlier tables in this report describe the original PWA/readiness/DAL releases, not this follow-up's test totals or APK binary.

## Behavior

Ennoic keeps its Flask backend, existing routes, responsive layouts, accounts, task logic, AI prompts, billing, and database schema. The new public resources are isolated in a Flask blueprint. Static/PWA resource requests no longer need to initialize the account database or start its background worker.

`/manifest.webmanifest` declares Ennoic, stable app identity `/`, installed start URL `/app`, scope `/`, and `standalone` display. Keeping the identity `/` avoids treating the updated start URL as a different PWA. The splash background is white `#ffffff`; the window theme remains the existing black `#0b0504`. Orange `#e8650f` is used on the offline retry control. The browser/OS controls the actual installed window and splash presentation; the web app adds no timed splash or artificial loading delay.

All icons derive from **the existing `static/email/ennoic-logo.png` wordmark**. Only empty margins are cropped, then the original image is resized/padded on white. No new symbol, font, or logo was introduced. PNGs include ordinary 192/512, separate maskable 192/512, and Apple 180 icons. Maskable artwork fits within the central 80%-diameter safe circle. The original source image is unchanged. Regenerate with `python scripts/generate_pwa_icons.py` (optional development dependency: Pillow).

An optional install control appears in the landing footer, app sidebar, and Settings > General when Chromium exposes `beforeinstallprompt`. Unsupported browsers continue normally. On iOS it shows expandable Safari Add to Home Screen instructions. Installed standalone windows hide the control. There is no automatic installation, push registration, or notification permission request.

The landing and sign-in shells replace their document with saved HTML. They now preserve PWA metadata/styles and notify the install controller after replacement. Delegated installation clicks survive the landing renderer recreating elements. Existing bundle rendering and authentication integration remain in place. Script nonces are retained where required; CSP explicitly permits same-origin workers and manifests without adding `unsafe-eval` or new external permissions.

### Installed-app launch and mobile polish

`/app` is the explicit start URL for updated Android packaging and PWA installations. Authenticated launches render the existing dashboard directly. Unauthenticated launches render `app_welcome.html`: the original Ennoic wordmark, the orange event-horizon ring/glow from the existing authentication artwork, a prominent Google button, and direct email entry. The original wordmark pixels and proportions are retained; CSS adapts its contrast to the dark welcome. Gentle background motion stops when reduced motion is requested. There is no marketing-page bundle or secondary splash in this flow.

The email button reveals the password field locally, then posts to the existing `/auth/continue` endpoint. It does not query whether an email address exists. Signup, email verification, password reset, account restoration, Google OAuth state/nonce/PKCE, canonical callback URLs, rate limits, and CSRF checks use the existing server flows. Passwords are never placed in a URL or persisted by the welcome script. `/app/email-help` provides forms for the existing protected reset and resend-verification actions.

A signed Flask session preference, `app_launch`, records visits to the dedicated entry route. Successful email or Google authentication returns these sessions to `/app`, and logout returns to its welcome screen after clearing the authentication session. The preference grants no account access and accepts no arbitrary redirect URL. Existing allowlisted checkout continuation still takes precedence. Changing a password retains the app preference while preserving the existing session rotation. The Google callback remains `https://ennoic.onrender.com/auth/google/callback`.

Ordinary browser visits to `/` retain the existing marketing page, including when another tab has visited `/app` in the shared browser session. Login pages in a session that has used the app entry may use the simplified welcome; this preference is shared wherever that browser shares cookies. Older PWA installations whose stored start URL is `/` are bridged to `/app` only when the browser reports standalone display mode or iOS standalone mode. Older Android launches also recognize the exact `android-app://com.ennoic.app` referrer. The redirect stays on this origin and preserves query/hash; no user-agent sniffing decides installed-app entry. Updated packaging does not depend on this compatibility bridge.

Mobile and tablet chat receives a centered conversation column, comfortable responsive horizontal padding, aligned actions, and composer spacing. Existing desktop structure remains in place. Shared navigation feedback acknowledges a same-origin page transition immediately without adding a delay; returning through browser history clears its pending state. Mobile tap highlights are suppressed while keyboard focus indicators remain visible. Pricing and subscription entitlements remain unchanged. Android native splash settings and physical-device limitations are documented separately in [android/README.md](../android/README.md).

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

Normal application HTML, CSS, JavaScript, API responses, tasks, conversations, credentials, auth endpoints, checkout/payment data, and external resources are **never written to Cache Storage**. No runtime response caching or offline mutations occur. Auth/API/payment requests bypass the worker; their original handlers continue to run. The worker only supplies the generic offline page on a network failure navigating to `/`, `/app`, `/settings`, `/pricing`, `/terms`, `/privacy`, or `/refund-policy`, without query parameters. `/app` is network-only while online, including its authenticated dashboard and unauthenticated welcome; neither response is cached. HTTP error responses are not replaced. Other URLs, including `/app/email-help`, authentication/token URLs, and query-bearing signup pages, retain ordinary browser offline failures.

Offline users can read the generic message and retry. Tasks and chat require a connection. Network recovery displays a retry message; it does not replay requests or submit unsent content. Browser eviction/private-mode restrictions can remove or prevent offline support, while online browser operation remains available.

Worker releases activate immediately because this worker does not cache the application UI. They do **not** force a page reload or discard text being typed. Fresh app pages/scripts come from the existing server on navigation/reload; a long-open page retains its current UI until then. Keep the worker URL stable. Existing browser storage used by Ennoic is unchanged.

Sessions still use the existing secure-cookie configuration and 12-hour permanent-session lifetime. No authentication tokens move into the manifest, worker, or local installation state. A new OS/browser web-app container may require initial sign-in; device testing must confirm OAuth handoff and return. Keep the existing production Google callback `https://ennoic.onrender.com/auth/google/callback`.

## Validation performed

All Python/browser runs used temporary SQLite data, suppressed loading `.env`, and avoided production credentials. Browser chat used a mocked AI provider with the real Flask endpoint, conversation persistence, and frontend. Tests did not create live charges or send messages to external services.

The mobile-polish follow-up adds focused tests in `tests/test_app_launch.py` for distinct browser/app entry, app-context email login/logout, CSRF rejection, verification-gated signup, reset/verification forms, mocked canonical Google OAuth/PKCE return, invalid-state rejection, and fixed redirect targets. Worker tests cover online/offline `/app` navigation while asserting that only the four public resources are cached. Initial welcome browser checks passed at 390×844, 800×1280 portrait-tablet, and 1280×800 landscape sizes, including local email progression, no horizontal overflow, and no page errors. The full-suite and APK totals are in the current mobile-release section above; final browser/deployment results are recorded with this release. The table below records the preceding completed PWA/DAL releases.

| Check | Result |
| --- | --- |
| Preceding DAL Python suite | **123/123 passed** in the Android DAL follow-up on 28 November, including 4 readiness tests, 6 PWA tests, and both corrected baseline tests |
| Original failure reproduction | Both previously failing tests fail on an isolated clean archive of original commit `9c0736f`, and before test corrections in the PWA working tree; neither was a PWA regression |
| Flask PWA tests | 6 passed: manifest/icon dimensions, MIME/cache/scope/CSP headers, metadata/session preservation, public resources without DB/cookies, exact test DAL identity, override/revocation validation, and package isolation |
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
| Android packaging | Bubblewrap 1.28.0 project generation and Gradle debug build passed; actual APK signature, package/version, SDK levels, launch URL, and notification permission checked |
| Syntax/whitespace | Python compilation, JS syntax checks, and `git diff --check` passed |

### Resolution of the two original failures

Both failures were reproduced again in a temporary `git archive` of **unchanged `9c0736f`**, without local `.env`, production data, or PWA files. The PWA working tree initially ran 117 tests with the same two failures and no errors. These were stale test expectations, not environment/dependency failures or PWA regressions.

| Original failure | Root cause | Low-risk correction |
| --- | --- | --- |
| `AccountAuthenticationTests.test_public_landing_links_to_account_forms` | Commit `9c0736f` deliberately removed the landing setup section and its CSS selector; the test still expected `#setup h2` | Updated only the expected selector list to the existing sections. No section or styling was restored. |
| `EntitlementTests.test_failed_ai_attempt_releases_usage_and_same_request_can_retry` | Commit `0d284cc` added a successful `chat_response` notification. The retry test expected a single call across an error and a successful retry. | Assert exactly `chat_error` then `chat_response`; keep privacy checks scoped to the error notification/log and verify the recovered response. Quota assertions remain intact. No notification or account logic changed. |

Existing tests still cover CSRF rejection, rate limiting, Google OAuth's canonical redirect and mocked callback, account isolation, session invalidation, task APIs, plan enforcement, and billing provider mocks. No browser security flags were disabled for PWA tests.

## Release verification

The 28 November release audit passed 121 Python tests and 11 Node tests; the subsequent Android DAL follow-up passed **123 Python tests and all 11 Node tests**. Browser regression checks from the release also passed with no page errors, including worker updates, offline/network recovery, login/logout, tasks, mocked chat/history, and session reopening. Independent source reviews found no release-blocking readiness, PWA privacy/auth/cache, or DAL override issue. Render continues to use the existing `vibecoded` branch and Gunicorn/Python build. Runtime dependencies, database schemas, pricing, entitlements, AI prompts, and existing account/payment logic are unchanged. No signing material, real environment files, local data, or generated builds are included in Git.

### Readiness diagnosis and fix

The initial deployment was held on 27 November because the existing production `/healthz` returned 200 while `/readyz` repeatedly returned 503. Landing/login/pricing returned 200, unauthenticated task requests correctly returned 401, and the new PWA resources were still 404. This predated the PWA changes. The user then supplied Render logs showing `database_readiness_failed error_type=OperationalError` and confirmed a newly created task persisted after refreshing.

The readiness probe used a **different connection path** from serving requests: every check opened a new PostgreSQL connection, applied a two-second connection timeout, and supplied `options=-c statement_timeout=2000` during startup. Normal operations use the existing psycopg pool without that startup option. Neon documents that its pooled PgBouncer endpoint rejects unsupported startup parameters; this probe-only setting is the most likely cause of the reported `OperationalError`. The exception type alone does not establish the exact server error, and cold connection handshakes can also exceed the probe's old timeout. See [Neon's unsupported-startup-parameter documentation](https://neon.com/docs/connect/connection-errors#unsupported-startup-parameter).

`database.py` now shares the existing application pool with readiness. It borrows a connection for at most two seconds, opens an explicit transaction, applies `set_config('statement_timeout', '2000', true)`, and executes `SELECT 1`. The timeout is transaction-local and resets on commit/rollback. The pool context returns or discards the connection on success/failure. No new direct connection bypass is used and no normal application timeout was increased. Pool acquisition and SQL execution have separate bounds; this is not a strict total network deadline. A genuinely exhausted/unavailable pool still fails readiness.

`app.py` requires no readiness-route change: failures still return generic 503 and log only the exception type. SQLite behavior is unchanged. New `tests/test_database.py` covers shared-pool reuse, absence of startup options/direct connects, cleanup and retry after query failure, pool exhaustion, and SQLite compatibility.

### Deployment results

On **28 November 2026**, a normal fast-forward push delivered PWA commit `b0a419b762ca63638a3fefaa3bf05b6b926b2c3c` and readiness fix `228da55c4d229ffa21bb0dd602bdd364cb1c7e32` to `origin/vibecoded`. No remote work was overwritten. Render automatically deployed the new application. The old 503/404 responses changed to the following verified results:

| Production check | Result |
| --- | --- |
| `/healthz` | 200, `{"status":"ok"}` |
| `/readyz` | 200, `{"status":"ready"}`; repeated successfully after browser checks and in three additional requests |
| `/manifest.webmanifest` | 200, `application/manifest+json`; JSON matches the committed manifest |
| `/service-worker.js` | 200, JavaScript MIME, root scope and revalidation headers; source matches the committed worker |
| Five ordinary/maskable/Apple icons | 200, correct PNG dimensions; bytes match the local assets |
| `/offline`, `/.well-known/assetlinks.json` | 200; initial DAL response was `[]`; the Android testing follow-up configures the verified test certificate as described below |
| `/`, `/login`, `/pricing` | 200; landing and existing sign-in form render without browser errors |
| `/api/tasks`, `/api/auth/me`, `/settings` without login | Expected 401, anonymous 200, and login redirect respectively |
| Google OAuth initiation | 302 to `accounts.google.com`, with the existing `https://ennoic.onrender.com/auth/google/callback`; provider flow not completed |
| Live Chromium installability | `Page.getInstallabilityErrors` returned no errors in a normal persistent browser profile |
| Live worker/offline/privacy | Root controller active; exactly four public cache entries; generic fallback rendered offline; landing restored after reconnect |
| Live iOS emulation | Apple metadata and expandable Safari Add to Home Screen instructions passed |

The successful readiness check after this change supports the diagnosis of a probe-specific connection failure. No production environment variables, credentials, customer account data, or database settings were changed. Authenticated task/chat/login/logout flows were tested in the isolated local application; no actual customer credentials, paid AI request, charge, email, or production data mutation was used for live verification. The user independently confirmed task persistence before deployment. Real mobile installation, complete provider handoffs, and live authenticated flows remain device/account checks below.

Local QA evidence is retained under ignored `instance/pwa-qa/`, including `python-readiness-final.json`, `python-readiness-final.log`, `browser-readiness-final.log`, `results.json`, `production-deployment-poll.json`, `production-final.json`, screenshots, and earlier baseline failure reproductions. Logs and generated evidence are not committed.

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

1. The deployed HTTPS manifest, worker, icons, and offline resources already passed direct HTTP/MIME checks, and Chromium confirmed root worker control. Recheck them after future releases.
2. On Android Chrome, install from the optional control or browser menu. Launch from the home-screen icon and check that the address bar is absent, the actual wordmark is legible, and the OS splash/background looks correct. Test portrait/landscape and keyboard use.
3. On iPhone/iPad Safari, use Share > Add to Home Screen, retain Open as Web App if offered, then Add. Launch from the icon and inspect icon/splash/status-bar appearance and safe areas. Follow [Apple's instructions](https://support.apple.com/en-ie/guide/iphone/iphea86e5236/27/ios/27).
4. In both installed apps, sign in/out, perform a real Google sign-in and return, create/edit/complete tasks, send an authorized live AI request, open settings and billing/customer portal, then close/reopen. Verify expected session expiry. Test payments in an appropriate sandbox; do not create a live charge merely for installation testing.
5. With an established worker, go offline and relaunch from the icon. Verify only the generic fallback is available, API requests fail normally, and no private data appears in Cache Storage. Reconnect and retry. Offline support requires a prior successful online worker installation.
6. Deploy a subsequent approved public-offline version, increment the cache version, and return to the app. Confirm the new worker/cache, removal of its obsolete cache, fresh app files on navigation, and no forced reload of a draft. Check another desktop browser and actual Safari as well as Chromium.

Physical Android/iOS installation, native standalone windows/splashes, complete real provider handoffs, and on-device APK behavior remain manual. No real mobile device was connected for this task.

## Initial Android APK and DAL handoff (historical)

**A signed test APK was generated after successful deployment:** `android/build/ennoic-test-debug.apk`, 5,531,296 bytes. File SHA-256: `49daeef2d60af06928cb7496786f6482838865e8ae07a416fddb687482a7048a`. It remains an ignored local deliverable and is not committed to Git. See [android/README.md](../android/README.md) for exact tooling, validation, debug certificate fingerprint, reproduction commands, signing/keystore management, and release steps.

Bubblewrap CLI/core 1.28.0 generated the project from the live manifest through the reproducible `npm run generate-debug` helper. A checksum-verified portable Temurin JDK 17 was installed user-locally; Gradle 8.11.1 used the existing SDK and automatically added Build-Tools 35.0.0 under its already accepted license. The first build exposed an upstream mismatch: Bubblewrap's default API 21 was lower than Browser Helper 2.8.0's required API 24. Setting `minSdkVersion: 24` in the Android reference configuration fixed it. The subsequent `assembleDebug` passed in 3m44s.

`apksigner` verified the actual APK (Signature Scheme v2), and `aapt` confirmed `com.ennoic.app`, Ennoic, version `0.1.0-test`/code `1`, minimum API 24, target/compile SDK 36, the production launch URL, and no `POST_NOTIFICATIONS` permission. Generated launcher/splash artwork retains the full existing wordmark. Gradle uses the standard external `~/.android/debug.keystore`; no release key was created, and signing credentials were not collected or committed. No device was connected, so installation, native splash/window appearance, and full on-device functionality remain manual.

The provisional Android ID is `com.ennoic.app`. The Android testing follow-up explicitly authorizes `/.well-known/assetlinks.json` to associate the existing APK's verified public certificate, `60:F5:54:7E:71:0B:4A:3C:D0:6C:0F:8D:CB:8F:EB:80:17:6C:65:D7:7E:D8:21:C1:9A:4B:0E:75:72:2A:78:98`. `pwa.py` now supplies this package-bound checked-in default; existing environment values can replace it, and an explicit empty or invalid fingerprint override publishes `[]` rather than restoring default trust. A different package must provide its own fingerprint. No Render secret changes or APK rebuild are needed. Additional tests cover the exact default, public/no-DB response, package isolation, replacement, multiple certificates/deduplication, revocation, and invalid configuration.

Digital Asset Links is unnecessary for ordinary PWA installation; it is needed for verified TWA behavior. The existing APK has been reverified with `apksigner`/`aapt`; its signature and file hash are unchanged. After deployment, a supported Android browser should launch this APK without browser controls on the Ennoic origin. Physical-device verification remains manual; external Google/payment pages may show browser controls. The [Android guide](../android/README.md#install-and-launch-on-your-phone) contains exact installation instructions, troubleshooting, and a mobile test checklist. Finalize the app ID and replace/remove the debug trust when setting up release signing. Google Play is optional.

The DAL update was committed as `725fff75a13284dcf40b07ec46639aa493f2e8c6` and pushed normally to `vibecoded`. Render deployed it successfully: the live endpoint returned 200 JSON with the exact package/fingerprint independently extracted from the existing APK, no redirect/cookie, and revalidation headers. Google's Digital Asset Links check returned **`linked: true`**. Health, readiness, manifest, and worker checks remained 200. QA evidence is in ignored `instance/pwa-qa/production-dal-verification.json`; full follow-up results are in `python-dal-final.json`. No release keys, private signing data, or APK binaries were committed.

## Exact file changes

Modified:

- `.env.example` — optional public Android verification settings; `.env` was not edited or read.
- `.gitignore` — signing/build artifact exclusions.
- `README.md` — links to the PWA report and Android guide.
- `app.py` — register public blueprint, exempt static/PWA requests from DB initialization, allow same-origin workers/manifests in CSP.
- `database.py` — reuse the application pool for readiness with a transaction-local query timeout.
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
- `tests/test_database.py` — pooled readiness success/failure and SQLite regression coverage.
- `android/.gitignore`, `android/package.json`, `android/ennoic.config.json`, `android/README.md` — independent Android packaging configuration, tooling, and build/signing guide.
- `android/generate-debug.cjs` — reproduce the Bubblewrap debug project from the deployed manifest without collecting signing passwords.
- `docs/PWA.md` — this report.

References: [Chromium install criteria](https://web.dev/articles/install-criteria), [Chrome installation guidance](https://developer.chrome.com/docs/modern-web-guidance/guides/pwa/install-web-app), [service-worker lifecycle](https://web.dev/articles/service-worker-lifecycle), [browser installation prompts](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/How_to/Trigger_install_prompt). Android-specific primary references are linked in its guide.
