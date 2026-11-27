# Ennoic Android packaging

This directory is independent of Flask, Python dependencies, Render's build, and the PWA. It wraps `https://ennoic.onrender.com/` in a Trusted Web Activity (TWA), using the device's browser engine. Google Play is optional: a signed APK can be installed directly.

## Current status — 28 November 2026

**The PWA is deployed and healthy.** PWA commit `b0a419b762ca63638a3fefaa3bf05b6b926b2c3c` and pooled-readiness fix `228da55c4d229ffa21bb0dd602bdd364cb1c7e32` were pushed normally to `vibecoded`, triggering Render. Production `/healthz` and `/readyz` now return 200 repeatedly. Manifest, worker, all five icons, offline fallback, root worker control, Chromium installability, cache privacy, network recovery, and iOS installation instructions passed live checks. See [the PWA report](../docs/PWA.md#deployment-results).

**A signed test APK was generated and verified:** `android/build/ennoic-test-debug.apk` (relative to the repository root). Bubblewrap **1.28.0** generated the project from the live production manifest; Gradle `assembleDebug` completed successfully in 3m44s after correcting the upstream minimum-SDK mismatch below. The Android testing follow-up explicitly configures its public debug certificate as the website's default Digital Asset Links association. This is a test identity; no release signing identity has been configured.

Available tooling: Node 24.6.0, npm 11.5.2, existing Android SDK platform 36 and build tools 36.0.0/36.1.0. A portable **Temurin JDK 17.0.21.1+2** was downloaded from Adoptium's official release, SHA-256 verified, and extracted to `%LOCALAPPDATA%\EnnoicAndroidToolchain\jdk-17.0.21.1+2`. Archive SHA-256: `eafba0944f777abdc74d5aad16049c9b5a1275b6ce99e2939ba9f7f3e6f08c87`. Gradle 8.11.1 automatically installed required SDK Build-Tools **35.0.0** using the existing accepted SDK license. The system Java 8 installation and machine environment were not changed. Android SDK command-line tools were absent; the successful debug workflow used the SDK directly.

Only the Android directory has local npm dependencies. Flask/Render do not install or use them. Generated projects, build outputs, signing material, and local logs remain ignored.

## Verified artifact

| Property | Actual result |
| --- | --- |
| Delivered file | `android/build/ennoic-test-debug.apk` (ignored local artifact) |
| Original build output | `android/generated/app/build/outputs/apk/debug/app-debug.apk` |
| Size | 5,531,296 bytes |
| APK SHA-256 | `49daeef2d60af06928cb7496786f6482838865e8ae07a416fddb687482a7048a` |
| Signature verification | `apksigner verify` exit 0; APK Signature Scheme v2 verified |
| Application | `com.ennoic.app`, Ennoic, version `0.1.0-test`, code `1` |
| Android versions | Minimum API 24; target/compile SDK 36 |
| Launcher / launch URL | `com.ennoic.app.LauncherActivity`; `https://ennoic.onrender.com/` |
| Permissions/configuration | Notifications disabled; no `POST_NOTIFICATIONS`; no additional trusted origins; Custom Tabs fallback |
| Signing identity | Standard user-local `~/.android/debug.keystore`; no release key generated |
| Device installation | Not performed: `adb devices` listed no connected devices |

Public **debug certificate** SHA-256 fingerprint (different from the APK file hash):

```text
60:F5:54:7E:71:0B:4A:3C:D0:6C:0F:8D:CB:8F:EB:80:17:6C:65:D7:7E:D8:21:C1:9A:4B:0E:75:72:2A:78:98
```

The website's checked-in DAL default now associates this exact certificate with `com.ennoic.app`. The existing APK was reverified directly with `apksigner`, its package was checked with `aapt`, and its file hash is unchanged; no APK rebuild is needed for this server-side change. Native installation, splash, complete OAuth/payment handoffs, tasks/chat, close/reopen, and toolbar-free TWA behavior still require a real device/account. A debug APK cannot update an APK signed with a different certificate; uninstall the test wrapper when moving to an incompatible release signing identity.

The DAL follow-up passed **123 Python tests and 11 Node tests**, including exact certificate/default response, public headers/no database access, package isolation, valid replacement, multiple-certificate normalization/deduplication, empty revocation, and invalid configuration. UI, account/task/AI behavior, and the APK binary are unchanged.

Production verification after pushing `725fff75a13284dcf40b07ec46639aa493f2e8c6` to `vibecoded`: `https://ennoic.onrender.com/.well-known/assetlinks.json` returned HTTP 200, `application/json`, no redirect/cookie, and the exact package/certificate extracted from the APK. Google's Digital Asset Links `assetlinks:check` API returned **`linked: true`**. `/healthz`, `/readyz`, the manifest, and the service worker also returned 200. No Render environment update or APK rebuild was necessary. This confirms the server association; physical phone launch remains untested.

## Install and launch on your phone

1. Use Android 7.0/API 24 or later with an up-to-date TWA-capable browser, preferably Chrome. Connect to the internet for website loading and association verification.
2. Copy `android/build/ennoic-test-debug.apk` from this repository to the phone's Downloads folder, for example using a USB file-transfer connection.
3. Open the APK in the phone's Files app. If Android asks, allow that specific file manager to install unknown apps, then choose Install. The app appears as **Ennoic** in the app drawer. USB debugging is unnecessary for this method.
4. Alternatively, enable Developer options > USB debugging, connect by USB, approve the computer on the phone, then run from the repository root:

   ```powershell
   adb devices
   adb install -r ".\android\build\ennoic-test-debug.apk"
   ```

5. Launch **Ennoic from its Android app icon**. Opening the website in an ordinary browser tab does not test APK/TWA verification. If an older wrapper uses an incompatible signing certificate, uninstall that wrapper before installing this one.

Expect the existing Ennoic branding/splash and website interface. On a supported browser with successful verification, Ennoic pages should have **no address bar, browser overflow menu, or Custom Tab close button**. Android's system status/navigation bars may remain: their presence does not mean verification failed. Google sign-in and other external domains can legitimately show browser controls; returning to the Ennoic origin should restore the verified app view.

If the Ennoic origin still shows browser controls, first check the live DAL endpoint against the package/fingerprint above, update Chrome, ensure the phone is online, close/force-stop Ennoic and relaunch. A previously failed association can be cached; allow time and retry. Do not clear browser data or bypass verification. On Android 12+, `adb shell pm verify-app-links --re-verify com.ennoic.app` and `adb shell pm get-app-links com.ennoic.app` help diagnose OS App Links, but only launching the app confirms toolbar-free TWA behavior. To identify the chosen browser while launching, use `adb logcat -v brief | Select-String TWAProviderPicker` in PowerShell. Check the actual selected browser if another default browser is installed.

## Short mobile checklist

- Launch from the APK icon and check branding, readable icons, and absence of browser controls on Ennoic pages.
- Log in; create a disposable task, edit/complete it, and confirm it remains saved after refreshing/reopening.
- Send one normal authorized AI chat request and verify the reply/history.
- Close/remove Ennoic from Recents, reopen from its icon, and check the expected session, tasks, and conversation history.
- Test Google sign-in, complete consent if needed, and confirm return to Ennoic without a redirect loop or persistent browser toolbar.
- Navigate between tasks, chat, settings, and back using Android Back; check keyboard/scrolling and external-link return.
- Test logout/login, offline fallback, and recovery when the connection returns. Complete provider/billing handoffs only in an appropriate test flow.

## Identity and configuration

`ennoic.config.json` records the intended answers to Bubblewrap's initialization prompts. It is a reference configuration, **not** a complete generated `twa-manifest.json`. The CLI does not consume it automatically; `generate-debug.cjs` does.

- Application ID: **`com.ennoic.app`**, provisional. Confirm ownership/naming before distribution. Keep `ennoic.config.json`, the generated manifest, and `ENNOIC_ANDROID_PACKAGE_ID` aligned when finalizing it. Regenerate/update the project after changing it. Changing the ID after distribution creates a different Android app.
- Name/launcher name: Ennoic. Host: `ennoic.onrender.com`. Start path: `/`.
- Manifest, scope, icons, and colors match the PWA. Use the existing wordmark icons, including the maskable icon.
- Notifications disabled. Keep `additionalTrustedOrigins` empty: Google and payment providers should retain their browser security UI.
- Minimum Android API **24 (Android 7.0)**. Bubblewrap 1.28.0 defaults to API 21, but its Android Browser Helper 2.8.0 dependency requires API 24. The first build's manifest merger exposed this mismatch; `minSdkVersion: 24` in the reference configuration resolves it without forcing incompatible library use. Target/compile SDK is 36.
- Fallback: Custom Tabs. A toolbar is expected when origin verification fails or navigation leaves the verified origin.

## Reproduce the local debug build

From this directory in a development PowerShell session, use a supported JDK 17 and the existing SDK. These environment assignments apply only to that shell and its children:

```powershell
$env:JAVA_HOME = "$env:LOCALAPPDATA\EnnoicAndroidToolchain\jdk-17.0.21.1+2"
$env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
$env:PATH = "$env:JAVA_HOME\bin;$env:PATH"
npm install
npm run generate-debug
Push-Location generated
try { .\gradlew.bat --no-daemon assembleDebug } finally { Pop-Location }
```

On another computer, replace the JDK/SDK paths with its actual installations. Obtain a portable JDK from [Adoptium](https://adoptium.net/installation/archives) and verify its published checksum before executing it. Install required Android SDK components from the [official Android tools](https://developer.android.com/studio#command-line-tools-only).

`generate-debug.cjs` uses Bubblewrap's pinned core generator and the deployed manifest, overlays the Ennoic configuration, validates it, and writes `generated/twa-manifest.json` and the Gradle project. It references the standard user-local `~/.android/debug.keystore`; Gradle supplies debug signing without collecting a password. It refuses to replace a manifest with a non-debug signing alias. Regeneration overwrites generated project files; keep release projects separately. Debug version is `0.1.0-test`, code `1`.

The expected Gradle artifact is `generated/app/build/outputs/apk/debug/app-debug.apk`. Confirm the build succeeded before using it. Verify with SDK tools:

```powershell
$apk = Join-Path $PWD 'generated\app\build\outputs\apk\debug\app-debug.apk'
& "$env:JAVA_HOME\bin\java.exe" -jar "$env:ANDROID_HOME\build-tools\36.1.0\lib\apksigner.jar" verify --verbose --print-certs $apk
& "$env:ANDROID_HOME\build-tools\36.1.0\aapt.exe" dump badging $apk
Get-FileHash -Algorithm SHA256 -LiteralPath $apk
adb devices
# Only after connecting an authorized test device:
adb install -r $apk
```

Use the actual installed build-tools version if it differs. `npm run install-apk` below belongs to the CLI release workflow and expects a different output filename. Debug signing is for local testing, not production identity or distribution continuity. This exact test certificate is now the checked-in DAL default; rebuilding on another machine may use a different local debug key and will require an intentional certificate update. Ordinary PWA installation is independent of Android verification.

## Release build workflow

Bubblewrap's interactive CLI remains available separately. Run its doctor and allow it to configure supported JDK/SDK dependencies, including command-line tools if required:

```powershell
npm run doctor
npm run init
```

For manually managed prerequisites, `npx bubblewrap updateConfig --jdkPath="C:\path\to\jdk-17" --androidSdkPath="C:\path\to\supported-sdk"` configures the CLI. Run `npm run doctor` and review required licenses. The CLI's SDK discovery differs from the direct Gradle debug workflow; do not assume an existing SDK platform alone passes its checks. Keep the generated debug project separately before initializing a release project.

The commands below describe a deliberate signed testing/release-key workflow; never label a debug/test certificate as a production release identity.

During initialization, use `ennoic.config.json` for the app values. Confirm `minSdkVersion` is **24** in the generated `twa-manifest.json` before updating/building the project; the upstream default 21 does not satisfy Browser Helper 2.8.0. Set the signing-key path to a location **outside the repository**, for example `C:\Users\YOUR_USER\AppData\Local\EnnoicSigning\ennoic-test.jks`. Use a separate test key/alias for disposable test builds. Do not accept a default key location inside the source tree. Answer credential prompts locally; do not record passwords in scripts, Git, shell history, or this document.

Bubblewrap generates `generated/twa-manifest.json` and the Gradle project. Confirm the ID, origin, icons, notification setting, signing-key path, and version. Then:

```powershell
npm run update
npm run build
adb devices
npm run install-apk
```

Run `update` after changing the generated manifest so those settings reach Gradle; declining a later regeneration prompt can leave stale project settings. The verified debug build used Gradle 8.11.1, Android Gradle Plugin 8.9.1, and Android Browser Helper 2.8.0 from Bubblewrap's pinned template. The ignored wrapper additionally records the official Gradle distribution SHA-256 `f397b287023acdba1e9f6fc5ea72d22dd63669d59ed4a289a29b1a76eee151c6`.

The expected CLI release outputs are `generated/app-release-signed.apk` and `generated/app-release-bundle.aab`. No release APK/AAB has been produced by this task. An unsigned APK is not an installable deliverable. Verify signatures before installation. On an authorized connected Android device, confirm the launcher's icon, splash, standalone window, authentication, task/chat flows, and update behavior.

All generated project/build files and signing artifacts are ignored. `npm install` can produce a lockfile; review and retain that nonsecret lockfile if adopting this packaging toolchain. Flask never imports this directory or invokes npm.

## Digital Asset Links

Flask serves `GET /.well-known/assetlinks.json` without login, cookies, redirects, or database access, with JSON MIME type and revalidation headers. `pwa.py` contains the explicitly authorized **public** test package/certificate defaults above; no private key or password is checked in. The statement grants `delegate_permission/common.handle_all_urls` to the `android_app` target `com.ennoic.app` and its verified certificate fingerprint.

Environment overrides remain available:

- If `ENNOIC_ANDROID_SHA256_FINGERPRINTS` is absent, the exact checked-in test certificate is used for `com.ennoic.app`.
- An explicitly empty value publishes `[]`, revoking trust. Invalid values also fail closed; they never restore the test default.
- Nonempty valid values **replace** the test default, allowing a release certificate or an intentional comma-separated certificate list. A different package name receives no default certificate and must supply its own fingerprint.
- When releasing, replace/remove the checked-in debug association and configure the final signing identity. Keep its keystore outside Git and backed up securely.

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

Fetch `https://ennoic.onrender.com/.well-known/assetlinks.json` and confirm HTTP 200, `application/json`, no redirect, and an exact package/certificate match. The generated Android manifest already associates this same origin. Relaunch and inspect TWA verification on a real device; use the troubleshooting steps above if browser controls remain.

## Release signing and updates

Back up the release keystore securely outside Git, with encrypted backups and passwords managed separately. Keep the alias and certificate fingerprint recorded with release operations. Direct APK updates must use the same application ID and compatible signing identity; increase `appVersionCode` for each release. Regenerate with `npm run update`, review the generated manifest/version, then `npm run build`. Bubblewrap updates can overwrite generated Android files, so maintain intended settings in the generated TWA manifest rather than making undocumented Gradle edits.

If distributing through Play App Signing, use the **Play app signing certificate** in production Digital Asset Links. The upload certificate can differ and does not verify the APK delivered by Play. Directly distributed APKs use their actual signing certificate. Choose distribution/signing continuity deliberately; Google Play enrollment is not needed for local or direct APK distribution.

Google OAuth remains on the existing HTTPS callback `/auth/google/callback`; never replace it with an APK-specific callback without a separate integration need. Test browser handoff and return on devices. Do the same for payment-provider pages and the customer portal, without creating live charges during routine testing.

Primary references: [Bubblewrap CLI](https://github.com/GoogleChromeLabs/bubblewrap/blob/main/packages/cli/README.md), [TWA quick start](https://developer.android.com/develop/ui/views/layout/webapps/guide-trusted-web-activities-version2), [website associations](https://developer.android.com/training/app-links/configure-assetlinks), [Android signing](https://developer.android.com/studio/publish/app-signing).
