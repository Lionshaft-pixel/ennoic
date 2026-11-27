# Android and tablet polish — 28 November 2026

This release preserves the desktop marketing page, Flask backend, account verification, task/AI logic, prices, entitlements, and database schema. No production account, charge, mandate, provider setting, or secret was changed during testing.

Code commit `23d436880f519d46ff3d40f0a756bb63684eb735` is deployed from `vibecoded`. Health/readiness, new welcome/manifest/assets, anonymous-access boundaries, canonical Google initiation and the unchanged Digital Asset Links association passed live HTTP checks; Google returned `linked: true`. Full local validation passed 133 Python tests, 19 Node tests and the complete browser regression without page errors. The verified update APK is `android/build/ennoic-test-debug.apk`, version `0.2.0-test`/code `2`, with the original package/certificate.

Follow-up `2acfbb4ed1b5f68d39906df938fda4a736e5aee3` fixes the existing Paddle stylesheet CSP block and is also deployed. The final live browser run passed installability, root worker control, welcome/pricing across phone/tablet/desktop sizes, cache privacy and offline/retry recovery with no page errors or CSP violations. Health/readiness and `linked: true` were rechecked successfully.

## Delivered behavior

- Installed PWA and updated Android launches use `/app`. Guests see the existing black-hole/event-horizon artwork, original wordmark, Google sign-in, direct email entry and signup/help links. Authenticated users see the existing dashboard. Browser `/` remains the marketing page. A signed, privilege-free session preference keeps authentication/logout returns in the app flow; Chrome/TWA cookie sharing can also apply this presentation to a browser login tab.
- Phone/tablet messages, actions and composer share a centered column with safe-area padding. The desktop layout above the responsive breakpoint is retained. Long URLs, code and tables remain contained.
- Same-origin navigation immediately shows a thin progress indicator and polite status, without delaying/intercepting the link. Back/forward restoration clears it. Reduced-motion preferences are respected. Android tap highlighting is suppressed while keyboard focus remains visible.
- Dashboard/settings initial HTML now requests only the subscription status it actually renders. The previous full entitlement call added three usage-count database round trips. `/api/entitlements` and all quota/plan enforcement still use the full authoritative data; no billing data is cached.
- Pricing's Paddle SDK downloads with `defer` and initializes after deferred scripts. This allows parsing/painting during its network wait. Checkout authentication, CSRF, configured price/quantity and intent payload are preserved.
- Live regression also found an existing CSP block on Paddle's injected stylesheet. Its exact CDN origin is now allowed for styles on pricing/payment pages only, with a regression test; other pages and script/worker protections retain their policies.
- Pricing explains monthly charge, mandate maximum, validity dates, cancellation and UPI revocation separately. No price or mandate is altered. A pre-existing Paddle account-deletion cancellation transport URL was corrected to `/subscriptions/{id}/cancel`, preserving ownership, effective-date and confirmation safeguards.

## Native splash and Android update

Native packaging changes require the updated APK; the website changes arrive through Render without reinstalling. Package `com.ennoic.app` and its signing certificate are preserved. Version `0.2.0-test`, code `2`, updates the previous code-1 test build.

The launcher and Browser Helper splash handoff use a white background and the same static, centered wordmark, derived deterministically from `static/email/ennoic-logo.png`. There is no logo box, new font, separate splash activity, timer or artificial delay. The generator crops empty margins and encodes the original pixel geometry/colors as compact, overlapping vector contours to avoid raster seams. Android 12's 288 dp icon area contains a 180 dp-wide wordmark inside its 192 dp safe circle. System entry motion/masking/sizing remains controlled by Android, so exact appearance and the browser handoff require the Realme test. The TWA splash fade is zero.

See [the Android guide](../android/README.md) for the verified artifact, certificate, installation and rebuild commands. Debug keys and generated APK/project files remain outside Git. Google Play is optional.

## Performance evidence and limits

Before changes, read-only production Edge testing at 800×1280 measured pricing cold/warm TTFB about 586/332 ms, DOMContentLoaded 1297/702 ms and first paint 1592/752 ms. Login measured TTFB 344 ms, DOMContentLoaded 728 ms and first paint 776 ms. A synchronous Paddle SDK request took about 682 ms; Google fonts about 326 ms. Independent fresh HTTPS requests took roughly 0.68–1.33 s to first byte, including about 0.32–0.56 s TLS setup. These are samples from this development machine, not a Realme mobile-network benchmark.

The reported 3–4 second delay was not reproduced as a single JavaScript stall. The release removes demonstrated HTML database work and parser blocking, and acknowledges unavoidable navigation immediately. Local SQLite timings do not predict production PostgreSQL or device latency. Confirm navigation after deployment on the actual tablet/network before claiming the full delay is eliminated.

The complete local browser regression passed with no page errors. Navigation feedback appeared in 4.9 ms while the destination response was held. One local dashboard sample had 33 ms TTFB but 2.5 s DOMContentLoaded, demonstrating that external styles/scripts and device/rendering conditions can still dominate after a fast server response. These samples are diagnostic, not a production speed guarantee.

After deployment, the read-only production browser measured `/app` TTFB 431 ms, DOMContentLoaded 1165 ms and first contentful paint 924 ms. Two pricing samples measured TTFB 337/366 ms, DOMContentLoaded 1268/1121 ms and first contentful paint 900/828 ms. Different runs have different network/cache conditions; these are observed release timings rather than a controlled before/after benchmark.

## Provider findings

Google's account chooser branding requires Google Auth Platform configuration and verification/publication. The application cannot hide legitimate hosted-domain security information. An owned custom domain may help domain verification but requires a coordinated origin, OAuth, Paddle and Android/DAL migration.

The reported ₹15,000 maximum and 2036 expiry are mandate fields, separate from monthly billing. Public documentation does not establish why this particular checkout selected those exact values. Verify the configured Paddle price is monthly and ask Paddle about the existing redacted mandate record; do not create another live mandate solely for diagnosis. Full primary-source findings and owner steps are in [MOBILE_CHECKOUT_REVIEW.md](MOBILE_CHECKOUT_REVIEW.md).

## Validation and remaining device work

Final automated/browser/build and production results are recorded in [PWA.md](PWA.md) and [android/README.md](../android/README.md). The repeatable browser script uses temporary SQLite, suppresses `.env`, blocks server-side external requests, mocks AI/Google identity transport, and exercises the real application endpoints/storage/UI. Screenshots and machine-readable QA evidence remain ignored under `instance/pwa-qa/`.

The responsive chat probe covered 320×740, 360×800, 390×844, 430×932, 768×1024, 800×1280, 1024×768, 1280×800 and 1440×1000. Through 1280 px the message/composer edges matched within one pixel; no tested viewport overflowed with long URLs/code/tables. The 1440 px desktop retained its original layout. The welcome was checked at phone, portrait-tablet and landscape sizes. Pricing is checked at narrow phone, phone, portrait/landscape tablet and desktop sizes.

On the Realme Pad X, install this APK as an update, then check:

1. Cold/warm launch: white, centered, legible original wordmark without a rectangle or prolonged/double splash; then black-hole welcome or authenticated dashboard. Ennoic-origin pages should have no browser toolbar. External Google/payment pages may show one.
2. Email login, verification/reset as appropriate, logout and Google sign-in: successful return to the dashboard, no loops, and no browser toolbar left on Ennoic. Real Google branding changes require the owner's console work.
3. Create/edit/complete a disposable task and send an authorized AI chat message. Close/remove from Recents and reopen; confirm the session and saved data.
4. Portrait/landscape, narrow split-screen, keyboard, long chat/code, message actions, composer and Android Back. Check comfortable edges, focus and no horizontal overflow.
5. Open Update/Upgrade, settings and pricing on the actual network: immediate subtle feedback, reasonable destination time, readable monthly/UPI disclosure and no blue tap flash.
6. Offline/reconnect: only the generic fallback, then retry into the authenticated app; no offline private cache or replay. Test provider handoffs in a suitable sandbox, without a live charge for routine QA.

No device or emulator is connected here. Native Android launch, physical Safari, real Google/Paddle/Paytm handoffs and the tablet's actual network responsiveness remain manual checks.

Production browser network emulation rendered the cached offline page and successfully retried after restoration, but kept `navigator.onLine` true while offline, so its cosmetic online-status event was not reliable. That event passed locally; confirm the reconnect message using airplane mode on the device. No private data was cached or replayed in either run.

## File inventory for this release

| Area | Added or modified files |
| --- | --- |
| Server launch and navigation work | Modified `app.py`; modified `paddle_billing.py` for the documented cancellation URL correction |
| Welcome | Added `templates/app_welcome.html`, `static/app-welcome.css`, `static/app-welcome.js` |
| Shared navigation and tablet layout | Added `static/app-navigation.css`, `static/app-navigation.js`; modified `templates/_pwa_head.html`, `static/script.js`, `static/mobile-responsive.css` |
| Pricing | Modified `templates/pricing.html`, `static/pricing-region.js` |
| PWA launch/privacy | Modified `static/pwa/manifest.webmanifest`, `static/pwa/pwa.js`, `static/pwa/service-worker.js` |
| Native packaging | Modified `android/ennoic.config.json`, `android/generate-debug.cjs`; added `android/resources/drawable/ennoic_splash_icon.xml`, `android/resources/values/ennoic_launch.xml`, `android/resources/values-v31/ennoic_launch.xml`, `scripts/generate_android_splash.py` |
| Automated tests | Added `tests/test_app_launch.py`, `tests/app_navigation.test.cjs`; modified `tests/test_pwa.py`, `tests/pwa_worker.test.cjs`, `tests/test_entitlements.py`, `tests/test_paddle_billing.py`, `tests/pricing_region.test.cjs` |
| Browser regression | Modified `scripts/pwa_browser_smoke.py` |
| Documentation | Added this report and `docs/MOBILE_CHECKOUT_REVIEW.md`; modified `docs/PWA.md`, `android/README.md` |

The APK, old-artifact backup, generated Gradle project, preview screenshots and QA logs are local ignored outputs. `.env`, signing keys, pricing configuration, database schemas and AI prompts were not edited.
