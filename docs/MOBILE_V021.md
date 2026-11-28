# Ennoic mobile web release v0.2.1

28 November 2026. This is a web update for the existing PWA/TWA. The owner confirmed that the v0.2.0 APK's splash, welcome, authentication and tablet layout work on the Realme Pad X. Those native resources, package `com.ennoic.app`, signing identity and Digital Asset Links are unchanged. No APK rebuild is required; the Android package still reports `0.2.0-test`/code `2` while loading this updated website.

## Composer and keyboard visibility

The previous mobile textarea minimum was 58px, producing a roughly 78px composer. The responsive composer is now **58px overall**, with a **44px textarea and send button**, readable 16px text and a bounded multiline input. Longer drafts scroll inside the textarea, capped at 128px or 28% of the visible viewport height. Desktop sizing stays as before.

The likely keyboard failure was a mismatch between `100dvh` and Android's visual viewport: Chrome can shrink only the visible viewport when opening the keyboard, leaving layout viewport units unchanged. The app now requests `interactive-widget=resizes-content`, and a small feature-detected helper follows actual VisualViewport height/top offset on resize, pan, rotation and page restoration. Reading actual bounds also supports browsers that already resize the layout, without subtracting the keyboard twice. Pinch zoom stays browser-controlled. [Chrome viewport behavior](https://developer.chrome.com/blog/viewport-resize-behavior)

Only mobile/coarse-pointer layouts are bounded to the visible area. The composer remains a normal flex child rather than a separate sticky/fixed overlay. The empty hero can shrink, safe-area padding is retained, and draft wrapping is recalculated when width or chat visibility changes. There is no forced scroll reset, focus stealing or touch interception.

## In-page confirmations

Sign-out, account deletion, task deletion and conversation deletion now use a shared Ennoic-styled HTML dialog instead of browser `confirm`/`prompt`. Account deletion combines its existing policy explanation and typed-email confirmation in one prominent modal. The 30-day restoration policy, possible provider-related deletion delay and billing safeguards remain unchanged. Existing translated labels are reused; historical subscription wording now covers either payment provider.

Cancel receives initial focus. Escape cancels, Tab stays within the modal, and closing restores focus. Modal keyboard events do not trigger background sidebar shortcuts. Dialogs follow keyboard viewport changes, keep the action row reachable and scroll long policy text internally. Content is inserted as text. Sign-out still submits the existing CSRF-protected POST; deletion still sends the typed email to the existing authenticated, CSRF-protected DELETE endpoint. No account/billing decision moved into the browser.

## Contextual verification help

The general Verify email link was a resend-verification action, not a separate authentication method. It is removed from the initial installed sign-in/signup screen. Resend help appears after registration/pending email authentication, invalid verification links and other relevant recovery responses. The saved browser authentication also exposes contextual resend help.

Help and notices remain uniform across unknown addresses and wrong passwords, preserving the existing anti-enumeration behavior. Verification tokens, expiry/one-time use, CSRF, rate limits and email delivery are unchanged. Already-verified users do not need this option to sign in.

## Installed scrolling

`overscroll-behavior-y: contain` on installed root/body elements suppresses Android Chrome's pull-to-refresh boundary gesture. CSS standalone display mode and the existing iOS standalone API determine installation; the `/app` URL and shared session cookies do not. Saved-document replacement and display-mode changes resynchronize the class. Ordinary browser root scrolling policy remains `auto`; horizontal navigation, text selection and internal scrolling are retained. No `touchmove` cancellation or native wrapper change is added. [Chrome overscroll guidance](https://developer.chrome.com/blog/overscroll-behavior/)

This depends on the selected TWA browser honoring CSS overscroll behavior. External authentication/payment pages keep their own browser controls and policies. Real pull gestures require the physical device check below.

## Validation

- Full isolated Python suite: **135 passed**, zero failures/errors (157.957s).
- Full Node suite: **31 passed**, including confirmation behavior, keyboard/viewport recovery, installed-mode isolation, existing pricing, subscription UI and worker privacy/update behavior.
- Composer probe: **36 bounds checks**, no page errors. Phone portrait/landscape, Realme-sized 800×1280 and 1280×800, resized-layout keyboard heights, visual-only height reduction/panning, keyboard close/rotation recovery, short/multiline drafts, long chat/code and Tasks→Chat all passed. A simulated 34px bottom navigation inset stayed clear. Fine-pointer 1440px desktop retained its original position, sizing and 38px send control.
- Contextual verification probe: browser phone and installed-entry tablet passed initial-link removal, pending-account resend, existing CSRF POSTs and recovery modal keyboard interaction; 30 focused Python authentication tests also passed before the full suite.
- Full isolated Edge regression: **passed, no page errors or native dialogs**. Existing login/logout, task CRUD/completion, mocked AI chat and Google callback, history/reopening, installability, worker updates and private-cache/offline boundaries passed. New checks covered confirmation Cancel/Escape/Tab/focus restoration, accepted task/conversation deletion, wrong-email rejection and exact-email account deletion, and a focused deletion field plus reachable actions at a 390×350 keyboard-sized viewport. Browser overscroll remained `auto`; simulated standalone mode used `contain`.

All authenticated QA uses disposable local SQLite accounts. AI, email and Google transport are mocked or blocked; no production credentials, customer data, payment transactions, mandates or `.env` contents were used. Screenshots/logs and machine-readable evidence remain ignored under `instance/pwa-qa/`.

## APK and rollout

Every changed behavior is rendered by the existing production website. The service worker still caches only the same four generic offline resources; new application scripts/styles are not placed in Cache Storage. None of those four offline assets changed, so the offline cache version is unchanged. Navigate/reload or close and reopen the app to load the fresh web files; an already-open document is not forcibly reloaded and drafts are not discarded.

The existing test APK remains `android/build/ennoic-test-debug.apk`, package `com.ennoic.app`, version `0.2.0-test`/code `2`, signed with the same certificate documented in [the Android guide](../android/README.md). Its binary and native splash are untouched. Release commits and live verification are recorded after deployment below.

## Physical Realme checks

1. Reopen the installed app online. Check a slightly thinner composer with comfortable input/send controls.
2. Focus the composer with short and multiline drafts; open/close the keyboard repeatedly. Rotate in both directions with keyboard open and closed. Check the whole composer stays above the keyboard/navigation area and the draft remains intact.
3. Scroll long conversations/code, switch Tasks→Chat repeatedly, use split screen if desired, and repeat keyboard/rotation checks. Verify normal text selection, pinch zoom and scrolling.
4. Test sign-out Cancel/Escape/confirm, task/conversation deletion and account-deletion Cancel. Use a disposable account for deletion; verify policy and email confirmation. Confirm no hosted-domain browser confirmation appears.
5. Check initial sign-in/signup are simpler, then use verification resend only with a suitable pending test account. Complete Google return navigation normally.
6. Pull downward at document and chat/task boundaries in the installed app: scrolling should work without refreshing. Check ordinary Chrome browser refresh still works. Verify offline/retry recovery and keyboard-safe modals on the actual device/browser.

Actual Android keyboards, OEM viewport/inset behavior and native pull gestures cannot be proven by desktop emulation; these checks remain manual.

## Files

| Area | Files |
| --- | --- |
| Composer | Added `static/app-viewport.js`, `tests/app_viewport.test.cjs`; modified `static/mobile-responsive.css`, `static/script.js`, `templates/index.html` |
| Confirmation dialogs | Added `static/app-confirmation.js`, `static/app-confirmation.css`, `tests/app_confirmation.test.cjs`; modified `static/settings.js`, `templates/_pwa_head.html`, `templates/settings.html`, `static/i18n.js` and the task/conversation callsites in `static/script.js` |
| Verification | Modified `app.py`, `templates/app_welcome.html`, `static/auth-integration.js`, `tests/test_accounts.py`, `tests/test_app_launch.py` |
| Installed scrolling | Modified `static/pwa/pwa.js`, `static/pwa/pwa.css`; added `tests/pwa_client.test.cjs` |
| Browser QA/docs | Modified `scripts/pwa_browser_smoke.py`, `docs/PWA.md`, `android/README.md`; added this report |

No native Android configuration, signing material, database schema, subscription pricing, task/AI logic or prompts changed.
