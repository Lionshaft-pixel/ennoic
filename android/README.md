# Ennoic Android packaging

This directory is independent of Flask, Python dependencies, Render's build, and the PWA. It wraps `https://ennoic.onrender.com/` in a Trusted Web Activity (TWA), using the device's browser engine. Google Play is optional: a signed APK can be installed directly.

## Current mobile release ? 28 November 2026

An updated signed test APK has been generated for the mobile/tablet polish release. The same package and certificate allow it to update the code-1 build already tested on the Realme Pad X. The native splash changes require this APK update; the welcome/layout/navigation changes are served by the website. See [the mobile report](../docs/MOBILE_UX.md), [PWA results](../docs/PWA.md), and [checkout/Google review](../docs/MOBILE_CHECKOUT_REVIEW.md).

| Property | Verified result |
| --- | --- |
| Delivered file | `android/build/ennoic-test-debug.apk` (ignored local artifact) |
| Absolute Windows path | `C:\Users\anuka\Desktop\ENNOIC\android\build\ennoic-test-debug.apk` |
| Original build output | `android/generated/app/build/outputs/apk/debug/app-debug.apk` |
| Size | 5,551,545 bytes |
| APK SHA-256 | `e70278e284be52ff94e8ae698ef47fc861f93e58bfe5dc9aa160e59fb5149497` |
| Signature | `apksigner verify` passed; APK Signature Scheme v2; identical certificate to the old APK |
| Application | `com.ennoic.app`, Ennoic, version `0.2.0-test`, code `2` |
| Android versions | Minimum API 24; target/compile SDK 36 |
| Launcher / URL | `com.ennoic.app.LauncherActivity`; `https://ennoic.onrender.com/app` |
| Native splash | White launcher/window backgrounds, static original centered wordmark, zero TWA fade; compiled API-31 theme and vector checked |
| Permissions / origins | Notifications disabled; no `POST_NOTIFICATIONS`; no additional trusted origins; Custom Tabs fallback |
| Signing key | Existing external `~/.android/debug.keystore`; no new key, password collection, or release identity |
| Previous artifact | Preserved locally as `android/build/ennoic-test-debug-v1.apk` |
| Physical installation | Previous build tested by the owner on Realme Pad X; this update still requires on-device validation |

Public **certificate** SHA-256 (different from the APK file hash):

```text
60:F5:54:7E:71:0B:4A:3C:D0:6C:0F:8D:CB:8F:EB:80:17:6C:65:D7:7E:D8:21:C1:9A:4B:0E:75:72:2A:78:98
```

The package-bound website DAL default continues to trust this exact certificate. No association change or provider-console setting is required for this update. Post-push live health/DAL verification is recorded in the final PWA release results.

The build used Node 24.6.0/npm 11.5.2, Bubblewrap CLI/core 1.28.0, Gradle 8.11.1, AGP 8.9.1, Browser Helper 2.8.0, SDK platform 36/build tools 35.0.0, and portable Temurin JDK 17.0.21.1+2 under `%LOCALAPPDATA%/EnnoicAndroidToolchain`. No machine Java/environment setting changed. The final incremental build passed in 2m11s with a process-only 512 MB Gradle heap after Windows rejected the default 1.5 GB heap. Generated resources, actual APK metadata, compiled vector paths and signatures were inspected before replacing the delivered file.

### Splash implementation and constraints

`resources/values/ennoic_launch.xml` and its API-31 variant style only the launcher. White system/window backgrounds, dark status/navigation icons and a static `ennoic_splash_icon` replace the old boxed splash. The Browser Helper handoff uses that same centered vector on white, with zero fade. There is no separate splash Activity, timer or artificial wait.

`scripts/generate_android_splash.py` crops empty margins from the actual `static/email/ennoic-logo.png` and encodes its original pixel geometry and nine ink colors as compact, hole-preserving vector contours. Overlapping contours avoid scaling seams; path lengths are checked against Android resource limits. No new logo, typography or decorative artwork was introduced. Regenerate from the repo root with `python scripts/generate_android_splash.py` (optional Pillow dependency).

Android 12+ controls the initial launch window, sizing/masking and system entry motion. The 288 dp drawable contains a centered 180 dp-wide wordmark inside the 192 dp safe circle. This is the closest platform-compatible static full-wordmark treatment; exact OEM appearance and the system-to-browser handoff must be checked on the Realme. Older Android devices use the white starting window and Browser Helper handoff. See [Android splash guidance](https://developer.android.com/develop/ui/views/launch/splash-screen).

Only Android's directory has npm dependencies; Flask/Render do not use them. Generated projects, APKs, signing material, logs and local QA remain ignored. This is a debug test identity; choose a deliberate release identity before public distribution.

## Install and launch on your phone

1. Use Android 7.0/API 24 or later with an up-to-date TWA-capable browser, preferably Chrome. Connect to the internet for website loading and association verification.
2. Copy `android/build/ennoic-test-debug.apk` from this repository to the phone's Downloads folder, for example using a USB file-transfer connection.
3. Open the APK in the phone's Files app. If Android asks, allow that specific file manager to install unknown apps, then choose Install. The app appears as **Ennoic** in the app drawer. USB debugging is unnecessary for this method.
4. Alternatively, enable Developer options > USB debugging, connect by USB, approve the computer on the phone, then run from the repository root:

   ```powershell
   adb devices
   adb install -r ".\android\build\ennoic-test-debug.apk"
   ```

5. Launch **Ennoic from its Android app icon**. Opening the website in an ordinary browser tab does not test APK/TWA verification. Choose Update for the existing code-1 test build: its package and certificate match. Do not uninstall it for this update. Only an unrelated incompatible signing identity requires uninstalling or a separately planned migration.

Expect a white centered-wordmark splash, then the black-hole welcome if signed out or your existing dashboard if signed in. Native launch appearance still needs device confirmation. On a supported browser with successful verification, Ennoic pages should have **no address bar, browser overflow menu, or Custom Tab close button**. Android's system status/navigation bars may remain: their presence does not mean verification failed. Google sign-in and other external domains can legitimately show browser controls; returning to the Ennoic origin should restore the verified app view.

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
- Name/launcher name: Ennoic. Host: `ennoic.onrender.com`. Start path: `/app` (browser `/` remains the marketing page).
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
try { .\gradlew.bat --no-daemon --max-workers=1 '-Dorg.gradle.jvmargs=-Xmx512m -XX:MaxMetaspaceSize=256m -XX:+UseSerialGC' assembleDebug } finally { Pop-Location }
```

On another computer, replace the JDK/SDK paths with its actual installations. Obtain a portable JDK from [Adoptium](https://adoptium.net/installation/archives) and verify its published checksum before executing it. Install required Android SDK components from the [official Android tools](https://developer.android.com/studio#command-line-tools-only).

`generate-debug.cjs` uses Bubblewrap's pinned core generator and the deployed manifest, overlays the Ennoic configuration, validates it, and writes `generated/twa-manifest.json` and the Gradle project. It references the standard user-local `~/.android/debug.keystore`; Gradle supplies debug signing without collecting a password. It refuses to replace a manifest with a non-debug signing alias. Regeneration overwrites generated project files; keep release projects separately. Debug version is `0.2.0-test`, code `2`. The helper requires the existing external debug keystore to be present so Gradle cannot silently generate a different identity. It overlays the embedded web-manifest launch/background metadata and reapplies the checked-in native resources/theme after regeneration. Restore the original key securely on another machine before building an update.

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

Back up the release keystore securely outside Git, with encrypted backups and passwords managed separately. Keep the alias and certificate fingerprint recorded with release operations. Direct APK updates must use the same application ID and compatible signing identity; increase `appVersionCode` for each release. Regenerate with `npm run update`, review the generated manifest/version, then `npm run build`. Bubblewrap updates can overwrite generated Android files. The debug helper reapplies checked-in `resources/` and the launcher-theme/splash metadata integration; apply the same reviewed overlay deliberately to a separate release project. Keep identity/version settings in the TWA configuration rather than undocumented Gradle edits.

If distributing through Play App Signing, use the **Play app signing certificate** in production Digital Asset Links. The upload certificate can differ and does not verify the APK delivered by Play. Directly distributed APKs use their actual signing certificate. Choose distribution/signing continuity deliberately; Google Play enrollment is not needed for local or direct APK distribution.

Google OAuth remains on the existing HTTPS callback `/auth/google/callback`; never replace it with an APK-specific callback without a separate integration need. Test browser handoff and return on devices. Do the same for payment-provider pages and the customer portal, without creating live charges during routine testing.

Primary references: [Bubblewrap CLI](https://github.com/GoogleChromeLabs/bubblewrap/blob/main/packages/cli/README.md), [TWA quick start](https://developer.android.com/develop/ui/views/layout/webapps/guide-trusted-web-activities-version2), [website associations](https://developer.android.com/training/app-links/configure-assetlinks), [Android signing](https://developer.android.com/studio/publish/app-signing).
