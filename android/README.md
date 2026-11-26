# Ennoic Android packaging

This directory is independent of Flask, Python dependencies, Render's build, and the PWA. It wraps `https://ennoic.onrender.com/` in a Trusted Web Activity (TWA), using the device's browser engine. Google Play is optional: a signed APK can be installed directly.

## Current status — 27 November 2026

**No APK was generated. The Android build stage is held behind the unsuccessful production deployment gate.** The user authorized safe prerequisite installation and a local test build, but required Android work to follow a successfully deployed PWA. Before any push, the existing production `/readyz` repeatedly returned HTTP 503 with `{"status":"not_ready"}`. The verified PWA changes were therefore preserved locally and not pushed. See [the release blocker](../docs/PWA.md#deployment-held-existing-production-readiness-failure).

This machine has Node 24.6.0, npm 11.5.2, ADB, Android SDK platform 36, and build tools 36.0.0/36.1.0. `java -version` reports Java **8 JRE**; `javac`, `keytool`, `sdkmanager`, and Bubblewrap are not on PATH. No `JAVA_HOME`, `ANDROID_HOME`, or `ANDROID_SDK_ROOT` was configured. SDK command-line tools and a suitable JDK were not found in the checked standard locations. `adb devices` previously listed no devices; no physical-device testing was performed. No signing certificate was supplied or generated.

The CLI is pinned to **1.28.0** in `package.json`. Its documented requirements include Node 18+ and JDK 17; it can guide installation of its Android dependencies. Existing SDK 36 files alone do not establish a working Bubblewrap environment. No global Java/SDK installation or license acceptance was performed.

The live production landing/login/pricing pages and `/healthz` returned 200, but readiness returned 503. `/manifest.webmanifest`, `/service-worker.js`, and `/.well-known/assetlinks.json` still returned 404. Production authorization has already been given; the unresolved readiness failure is the reason the push/deployment and subsequent Android build are held. No APK exists at the expected build paths below.

## Identity and configuration

`ennoic.config.json` records the intended answers to Bubblewrap's initialization prompts. It is a reference configuration, **not** a complete generated `twa-manifest.json` and is not automatically consumed by the CLI.

- Application ID: **`com.ennoic.app`**, provisional. Confirm ownership/naming before distribution. Change it in the generated manifest and `ENNOIC_ANDROID_PACKAGE_ID` together. Changing the ID after distribution creates a different Android app.
- Name/launcher name: Ennoic. Host: `ennoic.onrender.com`. Start path: `/`.
- Manifest, scope, icons, and colors match the PWA. Use the existing wordmark icons, including the maskable icon.
- Notifications disabled. Keep `additionalTrustedOrigins` empty: Google and payment providers should retain their browser security UI.
- Fallback: Custom Tabs. A toolbar is expected when origin verification fails or navigation leaves the verified origin.

## Remaining build steps

After production readiness is safely restored and the verified PWA is pushed/deployed, check that the HTTPS manifest and its icon URLs return 200 directly. Then, from this directory:

```powershell
npm install
npm run doctor
npm run init
```

Allow Bubblewrap to set up its supported JDK/SDK, or configure already installed **JDK 17** and SDK command-line tools with `npx bubblewrap updateConfig --jdkPath="C:\path\to\jdk-17" --androidSdkPath="C:\path\to\sdk"`, then run `npm run doctor`. Review required SDK licenses interactively.

The existing authorization permits a standard portable JDK/SDK setup after the deployment gate is met. Prefer official vendor archives, verify download checksums, and use a user-local toolchain directory without replacing system Java or running unknown privileged installers. For a disposable debug build, generate the Android project and use Gradle's debug signing/`assembleDebug` workflow; keep its keystore outside Git. Verify the resulting APK signature and manifest before calling it installable. Record the actual output path and SHA-256 only after a real build succeeds. The commands below instead describe Bubblewrap's signed testing/release-key workflow; never label a debug/test certificate as a production release identity.

During initialization, use `ennoic.config.json` for the app values. Set the signing-key path to a location **outside the repository**, for example `C:\Users\YOUR_USER\AppData\Local\EnnoicSigning\ennoic-test.jks`. Use a separate test key/alias for disposable test builds. Do not accept a default key location inside the source tree. Answer credential prompts locally; do not record passwords in scripts, Git, shell history, or this document.

Bubblewrap generates `generated/twa-manifest.json` and the Gradle project. Confirm the ID, origin, icons, notification setting, signing-key path, and version. Then:

```powershell
npm run build
adb devices
npm run install-apk
```

The expected CLI outputs are `generated/app-release-signed.apk` and `generated/app-release-bundle.aab`. These are expected filenames, not artifacts produced by this task. An unsigned APK is not an installable deliverable. To inspect a real build, use the SDK's `apksigner verify --verbose --print-certs` against the signed APK. Install on an authorized connected Android device with USB debugging and confirm the launcher's icon, splash, standalone window, authentication, task/chat flows, and update behavior.

All generated project/build files and signing artifacts are ignored. `npm install` can produce a lockfile; review and retain that nonsecret lockfile if adopting this packaging toolchain. Flask never imports this directory or invokes npm.

## Digital Asset Links

Flask now serves `GET /.well-known/assetlinks.json` without login, cookies, redirects, or database access. Until configured, it returns `[]`, which intentionally grants no Android app trust.

Extract the **public SHA-256 certificate fingerprint** from the actual signing key:

```powershell
keytool -list -v -keystore "C:\outside-repo\ennoic-release.jks" -alias ennoic
```

Configure these values in the deployment environment after the signing identity is finalized:

```text
ENNOIC_ANDROID_PACKAGE_ID=com.ennoic.app
ENNOIC_ANDROID_SHA256_FINGERPRINTS=<32 colon-separated SHA-256 hex bytes>
```

The fingerprint is public metadata, not the keystore password. Multiple intentionally trusted certificates may be supplied as comma-separated fingerprints. Invalid configuration grants no trust and logs a generic error. The endpoint generates the required `delegate_permission/common.handle_all_urls` statement with `namespace: android_app`, the configured package name, and fingerprints. Do not publish placeholder values.

Fetch `https://ennoic.onrender.com/.well-known/assetlinks.json` and confirm HTTP 200, `application/json`, no redirect, and an exact package/certificate match. The generated Android manifest must also associate the same origin (Bubblewrap generates this side). Reinstall/relaunch and inspect TWA verification on a real device. Android App Links diagnostics include `adb shell pm verify-app-links --re-verify com.ennoic.app` and `adb shell pm get-app-links com.ennoic.app`; also inspect browser TWA logs because OS link verification alone does not prove a toolbar-free TWA launch.

## Release signing and updates

Back up the release keystore securely outside Git, with encrypted backups and passwords managed separately. Keep the alias and certificate fingerprint recorded with release operations. Direct APK updates must use the same application ID and compatible signing identity; increase `appVersionCode` for each release. Regenerate with `npm run update`, review the generated manifest/version, then `npm run build`. Bubblewrap updates can overwrite generated Android files, so maintain intended settings in the generated TWA manifest rather than making undocumented Gradle edits.

If distributing through Play App Signing, use the **Play app signing certificate** in production Digital Asset Links. The upload certificate can differ and does not verify the APK delivered by Play. Directly distributed APKs use their actual signing certificate. Choose distribution/signing continuity deliberately; Google Play enrollment is not needed for local or direct APK distribution.

Google OAuth remains on the existing HTTPS callback `/auth/google/callback`; never replace it with an APK-specific callback without a separate integration need. Test browser handoff and return on devices. Do the same for payment-provider pages and the customer portal, without creating live charges during routine testing.

Primary references: [Bubblewrap CLI](https://github.com/GoogleChromeLabs/bubblewrap/blob/main/packages/cli/README.md), [TWA quick start](https://developer.android.com/develop/ui/views/layout/webapps/guide-trusted-web-activities-version2), [website associations](https://developer.android.com/training/app-links/configure-assetlinks), [Android signing](https://developer.android.com/studio/publish/app-signing).
