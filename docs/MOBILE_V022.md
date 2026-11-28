# Ennoic Android v0.2.2: Back navigation

28 November 2026. The owner confirmed v0.2.1 on a Realme Pad X. This follow-up preserves Flask authentication, Google state/nonce/PKCE and token exchange, account/billing rules, the existing interface, package `com.ennoic.app` and signing certificate.

## Why a native update is required

Successful email and Google authentication already return to `/app`; explicit sign-out already clears the session through the existing CSRF-protected POST and returns to the unauthenticated welcome. An HTTP redirect or `location.replace()` cannot erase older Google chooser entries. Browser history is deliberately isolated across origins. [HTML history APIs](https://html.spec.whatwg.org/multipage/nav-history-apis.html)

The TWA page is rendered in the browser's Android activity. Overriding Bubblewrap's launcher Back method would not receive that activity's Back events. This release instead uses supported Custom Tabs messaging and the web CloseWatcher API. It adds no dummy history entries, `popstate` loop, Google-page interception, WebView, or automatic activity relaunch. Ordinary website/PWA sessions without the native bridge keep their existing Back behavior. [TWA architecture](https://developer.chrome.com/docs/android/trusted-web-activity), [verified messaging](https://developer.chrome.com/docs/android/post-message-twa)

## Web navigation

The shared Android Back controller activates only after a trusted, source-null browser message from exactly `android-app://ennoic.onrender.com` transfers an origin-targeted MessagePort with an empty payload. The native HELLO then arrives separately on that port and must supply the expected version/capability and nonce; a live reset acknowledgement completes the handshake. Both the HELLO and acknowledgement have bounded timeouts. Origin validation is also performed by the native side using Digital Asset Links. The port never carries credentials, OAuth tokens, conversation content or task data.

An actual close request closes a conversation menu, rename editor, mobile sidebar or mobile Tasks view through existing UI actions before requesting exit. Settings subsections return to General first. Native HTML dialogs handle their own Back request; the app watcher is suspended before opening task/confirmation dialogs and resumes after closing them. Escape closes one layer without triggering background shortcuts.

On other Ennoic screens, Back follows a known safe same-origin previous entry when available. Otherwise it replaces the current page with `/app`; authentication endpoints, external origins and checkout predecessors are excluded. At the authenticated dashboard, Back requests native exit confirmation instead of traversing completed authentication or external checkout history. The unauthenticated welcome requests exit directly, so explicit sign-out stays signed out.

Only one app CloseWatcher is active. Its real `close` event is used without canceling browser close requests. Acknowledgement timeouts are bounded even during rapid presses: if the host/port is missing, stale or unavailable, the controller destroys its watcher and leaves browser Back available. Unsupported browsers do not receive this feature. [CloseWatcher specification](https://html.spec.whatwg.org/multipage/interaction.html#close-watcher-infrastructure)

## Native toast and exit

The checked-in Java launcher uses a browser-owned TWA session with the existing white branded splash and Custom Tabs fallback. It keeps a scoped messaging connection while the TWA is active. `common.use_as_origin` is added alongside the existing `common.handle_all_urls` association for the same package and public certificate; no Google/payment origin becomes trusted.

At the root, the first Back shows the native, non-blocking **“Press back again to exit”** toast. A monotonic two-second deadline controls the second press. Expiry cancels the toast and resets the confirmation; navigation, hiding the app, opening a modal or interacting with the page also reset it. A second Back within the interval finishes/removes only the captured Ennoic task after checking its ID and base component. It does not close unrelated browser tasks or clear cookies. Launching from the app icon returns to the Android launcher when this task closes.

The APK must be updated for this feature; v0.2.0's wrapper has no messaging capability. The website remains compatible with that older APK. The new build is `0.2.2-test`, version code `3`, using the same application ID and existing external debug key. Install it as an update rather than uninstalling Ennoic. Provider pages continue to show their normal security UI and own Back behavior.

## Validation and rollout

- Full Node suite: **45 passed**, including 14 new bridge/dialog tests for native handshake validation, root/panel/dialog Back ordering, safe history, lifecycle and bounded failure recovery. Final review corrected the mock to match Chrome's two-stage empty-bootstrap/port-HELLO transport and exact native origin; the protocol tests cover that actual sequence.
- Native JVM state test: **10 checks passed** (first/second press, exact deadline, expiry, reset and repeated reset).
- Full isolated Python suite: **135 passed**, zero failures/errors (188.547 seconds of test execution). An existing structural Escape-handler assertion was updated to allow the new handled-event guard.
- Complete isolated browser regression: **passed with no page errors**. Existing email/Google callback, sign-out, task CRUD/completion, mocked AI chat, history/reopening, pricing, installability, service-worker updates, public-only cache and offline/recovery checks remained successful.
- Focused Back browser regression: **passed using real Edge CloseWatcher/ trusted Escape**, with a modeled native MessagePort/clock. Root first/expiry/rearm/second, unchanged history, one-layer menu/sidebar/Tasks dismissal, task/custom dialogs, Settings sections/safe history, checkout exclusion, stale-channel failure, sign-out and mocked Google cross-origin history all passed. Ordinary browser Back remained normal. Native toast/task exit are not proven by this desktop mock.
- Gradle `assembleDebug`: **passed**, including the final native source and launcher/service manifest integration. Signature v2 and package/version/SDK metadata were checked on the resulting APK.

| APK property | Result |
| --- | --- |
| Delivered file | `android/build/ennoic-test-debug.apk` |
| Absolute path | `C:\Users\anuka\Desktop\ENNOIC\android\build\ennoic-test-debug.apk` |
| Package/version | `com.ennoic.app`, `0.2.2-test`, code `3`, min SDK 24, target SDK 36 |
| Size | 5,553,647 bytes |
| File SHA-256 | `74e16e6a4409cc9880227225cc6f9aeef375d15ca50dc17c29cae38436c18187` |
| Signing certificate SHA-256 | `60:F5:54:7E:71:0B:4A:3C:D0:6C:0F:8D:CB:8F:EB:80:17:6C:65:D7:7E:D8:21:C1:9A:4B:0E:75:72:2A:78:98` (unchanged) |
| Previous v0.2.0 binary | `android/build/ennoic-test-debug-v020.apk` (preserved locally) |

Local authenticated tests use disposable SQLite accounts and mocked/blocked provider transports, never production customer data or `.env`. Generated projects, APKs, keystores and local evidence remain ignored; no signing secrets enter Git. An initial concurrent emulator/browser run exhausted the host's available memory; the successful browser runs were serialized after stopping that emulator. Native Android runtime and final production results are recorded after their checks below.

Evidence remains under ignored `instance/pwa-qa/` (`python-v022-final.json`, `results.json`, `android-back-browser-v022.json`) and `android/build/v022-verification.json`.

## Files

| Area | Files |
| --- | --- |
| Native bridge/timer | Added `android/java/com/ennoic/app/LauncherActivity.java`, `RootBackState.java`, `android/tests/RootBackStateTest.java`; modified `android/ennoic.config.json`, `android/generate-debug.cjs` |
| Web Back controller | Added `static/android-back.js`, `tests/android_back.test.cjs`; modified `templates/_pwa_head.html`, `static/script.js`, `static/settings.js`, `static/app-confirmation.js`, `tests/app_confirmation.test.cjs` |
| Origin association | Modified `pwa.py`, `tests/test_pwa.py` |
| Browser regression | Added `scripts/android_back_browser_smoke.py`; updated the existing structural Escape test in `tests/test_accounts.py` |
| Documentation | Added this report; modified `docs/PWA.md`, `android/README.md` |

No authentication redirect, account logic, task/AI logic, database schema, subscription pricing, prompts, brand assets or offline cache contents changed.

## Production deployment

Code commit **`808b052321220a07ce8b871d680745128129c8c9`** was pushed normally to `vibecoded` and deployed by Render on 28 November 2026. The third verification poll observed the exact new Back controller and shared head after an initial connection error and an old-version response. `/healthz` and `/readyz` each returned 200 three times. The new/updated static resources, manifest and root-scoped service worker matched the release sources. Guest welcome remained `no-store`, browser `/` remained marketing, anonymous tasks remained 401 and settings redirected to login. Google authorization initiation retained the canonical callback and S256 PKCE.

The production Digital Asset Links endpoint returned 200 JSON with the unchanged package/certificate and both URL-handling and native-message relations. It set no cookie and did not redirect. Google's verification API returned **`linked: true` for both `common.handle_all_urls` and `common.use_as_origin`**. Evidence: ignored `instance/pwa-qa/production-v022-verification.json`. Production verification created no accounts, tasks, conversations, emails or payment actions.

The Back feature requires the updated APK, a live verified native connection and a CloseWatcher-capable browser (Chrome 126 or newer). Older wrappers, standalone PWAs and unsupported/disconnected providers retain normal browser Back. No physical Android runtime test was performed here; the native toast/task-exit behavior and real Google identity handoff remain for owner verification using the checklist below.

## Physical Realme checklist

1. Install `android/build/ennoic-test-debug.apk` over the existing app, open from its icon online, and confirm the existing splash/session/layout.
2. Complete real Google sign-in, then press Back at the loaded dashboard. Expect the native toast rather than the account chooser. Repeat with email sign-in.
3. Press Back again within two seconds: expect the app task to close. Relaunch and check the session/history. Repeat after waiting over two seconds: the next press should show a fresh toast and keep the dashboard open.
4. Test Back with the keyboard open, in portrait/landscape, after Tasks, with the mobile sidebar/menu open, in task/confirmation dialogs and from Settings subsections. One UI layer should close before exit confirmation.
5. Cancel and confirm sign-out; confirm the welcome stays unauthenticated. Test normal Back in ordinary Chrome separately.
6. Test return from an appropriate Google/billing test flow and after backgrounding/reopening. Unsupported browsers or loss of the native host connection fall back to normal browser Back; no history trap is installed.

Actual OEM keyboard/navigation gestures and real Google/provider identity handoffs still need owner testing.
