// Generate a local test project. Gradle, rather than this script, supplies debug signing.
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const {TwaManifest, TwaGenerator, ConsoleLog} = require("@bubblewrap/core");

async function main() {
  const config = JSON.parse(await fs.readFile(path.join(__dirname, "ennoic.config.json"), "utf8"));
  const debugKeyPath = path.join(os.homedir(), ".android", "debug.keystore");
  try {
    await fs.access(debugKeyPath);
  } catch {
    throw new Error("Restore the existing external debug.keystore before building an update; do not silently generate a different signing identity.");
  }
  const destination = path.join(__dirname, "generated");
  const manifestPath = path.join(destination, "twa-manifest.json");
  try {
    const existing = JSON.parse(await fs.readFile(manifestPath, "utf8"));
    if (existing.signingKey?.alias !== "androiddebugkey") {
      throw new Error("Keep the existing release project separately before generating a debug project.");
    }
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }

  const base = await TwaManifest.fromWebManifest(config.webManifestUrl);
  const manifest = new TwaManifest({
    ...base.toJson(),
    ...config,
    themeColorDark: config.themeColor,
    navigationColorDark: config.themeColor,
    signingKey: {path: debugKeyPath, alias: "androiddebugkey"},
    appVersion: config.appVersion || "0.2.2-test",
    appVersionCode: config.appVersionCode || 3,
    generatorApp: "bubblewrap-cli",
    enableNotifications: false,
    additionalTrustedOrigins: [],
  });
  const error = manifest.validate();
  if (error) throw new Error(error);
  await fs.mkdir(destination, {recursive: true});
  await manifest.saveToFile(manifestPath);
  await new TwaGenerator().createTwaProject(destination, manifest, new ConsoleLog("ennoic-debug"));
  // Bubblewrap overrides start_url but otherwise embeds the live site's older
  // metadata when native preparation precedes its web deployment.
  const embeddedPath = path.join(destination, "app/src/main/res/raw/web_app_manifest.json");
  const embedded = JSON.parse(await fs.readFile(embeddedPath, "utf8"));
  Object.assign(embedded, {start_url: config.startUrl, background_color: config.backgroundColor,
    theme_color: config.themeColor, display: config.display});
  await fs.writeFile(embeddedPath, JSON.stringify(embedded, null, 2) + "\n");
  // Keep native branding changes reproducible when Bubblewrap regenerates files.
  await fs.cp(path.join(__dirname, "resources"), path.join(destination, "app/src/main/res"), {recursive: true});
  await fs.cp(path.join(__dirname, "java"), path.join(destination, "app/src/main/java"), {recursive: true});
  const androidManifestPath = path.join(destination, "app/src/main/AndroidManifest.xml");
  let androidManifest = await fs.readFile(androidManifestPath, "utf8");
  if (!androidManifest.includes('<activity android:name="LauncherActivity"')
      || !androidManifest.includes('android:resource="@drawable/splash"')) {
    throw new Error("Bubblewrap's launcher template changed; review the splash integration before building.");
  }
  androidManifest = androidManifest.replace('<activity android:name="LauncherActivity"',
    '<activity android:name="LauncherActivity" android:theme="@style/Theme.Ennoic.Launch" android:configChanges="orientation|screenSize|smallestScreenSize|screenLayout|keyboardHidden"')
    .replace('android:resource="@drawable/splash"', 'android:resource="@drawable/ennoic_splash_icon"');
  androidManifest = androidManifest.replace('</application>',
    '<service android:name="androidx.browser.customtabs.PostMessageService" android:exported="true" />\n    </application>');
  await fs.writeFile(androidManifestPath, androidManifest);
  console.log(`Debug project: ${destination}`);
}

main().catch(error => {
  console.error(error.message);
  process.exitCode = 1;
});
