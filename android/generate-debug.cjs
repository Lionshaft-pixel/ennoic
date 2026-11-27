// Generate a local test project. Gradle, rather than this script, supplies debug signing.
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const {TwaManifest, TwaGenerator, ConsoleLog} = require("@bubblewrap/core");

async function main() {
  const config = JSON.parse(await fs.readFile(path.join(__dirname, "ennoic.config.json"), "utf8"));
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
    signingKey: {path: path.join(os.homedir(), ".android", "debug.keystore"), alias: "androiddebugkey"},
    appVersion: "0.1.0-test",
    appVersionCode: 1,
    generatorApp: "bubblewrap-cli",
    enableNotifications: false,
    additionalTrustedOrigins: [],
  });
  const error = manifest.validate();
  if (error) throw new Error(error);
  await fs.mkdir(destination, {recursive: true});
  await manifest.saveToFile(manifestPath);
  await new TwaGenerator().createTwaProject(destination, manifest, new ConsoleLog("ennoic-debug"));
  console.log(`Debug project: ${destination}`);
}

main().catch(error => {
  console.error(error.message);
  process.exitCode = 1;
});
