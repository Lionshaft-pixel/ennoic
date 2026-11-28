const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");

const source = fs.readFileSync(require.resolve("../static/pwa/pwa.js"), "utf8");

function setup({ standalone = false, iosStandalone = false, pathname = "/app" } = {}) {
  const events = {};
  const modeEvents = {};
  const documentEvents = [];
  const redirects = [];
  const mode = { matches: standalone, addEventListener: (name, fn) => { modeEvents[name] = fn; } };
  function root() {
    const classes = new Set();
    return { classes, classList: { toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); } } };
  }
  const document = {
    documentElement: root(), referrer: "", querySelector: () => null,
    addEventListener: name => documentEvents.push(name),
  };
  const window = {
    isSecureContext: true, matchMedia: () => mode,
    addEventListener: (name, fn) => { events[name] = fn; },
    location: { pathname, search: "?source=icon", hash: "#chat", replace: url => redirects.push(url) },
  };
  vm.runInNewContext(source, { window, document, navigator: { standalone: iosStandalone }, URL });
  return { window, document, mode, events, modeEvents, documentEvents, redirects, root };
}

test("visiting /app in a browser never opts into installed-only scroll policy", () => {
  const { document, documentEvents, redirects } = setup();
  assert.equal(document.documentElement.classes.has("ennoic-installed"), false);
  assert.deepEqual(redirects, []);
  assert.equal(documentEvents.some(name => name.startsWith("touch")), false);
});

test("installed mode follows actual display state and survives saved-document replacement/BFCache", () => {
  const env = setup({ standalone: true });
  assert.equal(env.document.documentElement.classes.has("ennoic-installed"), true);
  env.document.documentElement = env.root();
  env.events["ennoic:page-ready"]();
  assert.equal(env.document.documentElement.classes.has("ennoic-installed"), true);
  env.mode.matches = false;
  env.modeEvents.change();
  assert.equal(env.document.documentElement.classes.has("ennoic-installed"), false);
  env.mode.matches = true;
  env.events.pageshow();
  assert.equal(env.document.documentElement.classes.has("ennoic-installed"), true);
});

test("iOS standalone fallback applies the same policy and legacy root bridge stays local", () => {
  const app = setup({ iosStandalone: true });
  assert.equal(app.document.documentElement.classes.has("ennoic-installed"), true);
  const legacy = setup({ iosStandalone: true, pathname: "/" });
  assert.deepEqual(legacy.redirects, ["/app?source=icon#chat"]);
  assert.deepEqual(setup({ pathname: "/" }).redirects, []);
});
