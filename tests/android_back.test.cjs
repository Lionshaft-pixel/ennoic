const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");
const source = fs.readFileSync(require.resolve("../static/android-back.js"), "utf8");

function setup({ path = "/app", screen = "dashboard", supported = true } = {}) {
  const handlers = new Map();
  const docHandlers = new Map();
  const timers = new Map();
  const watchers = [];
  const replacements = [];
  let now = 0;
  let nextTimer = 1;
  let mutation;
  let modal = false;
  let panel = false;
  let panelsClosed = 0;
  let traversals = 0;
  const entries = [{ key: "current", url: "https://ennoic.onrender.com" + path }];
  const add = (map, name, fn) => map.set(name, [...map.get(name) || [], fn]);
  const emit = (map, name, event = {}) => (map.get(name) || []).forEach(fn => fn(event));
  class CloseWatcher {
    constructor() { this.live = true; this.listeners = new Map(); watchers.push(this); }
    addEventListener(name, fn) { this.listeners.set(name, fn); }
    destroy() { this.live = false; }
    close() {
      assert.equal(this.live, true);
      this.live = false;
      this.listeners.get("close")?.();
    }
  }
  const window = {
    location: { origin: "https://ennoic.onrender.com", pathname: path, replace: value => replacements.push(value) },
    addEventListener: (name, fn) => add(handlers, name, fn),
    setTimeout(fn, delay) { const id = nextTimer++; timers.set(id, { fn, due: now + delay }); return id; },
    clearTimeout: id => timers.delete(id),
    navigation: {
      currentEntry: entries[0], entries: () => entries,
      back: () => { traversals++; emit(handlers, "navigate"); },
      addEventListener: (name, fn) => add(handlers, name, fn),
    },
    EnnoicAppBack: { closePanel() { if (!panel) return false; panel = false; panelsClosed++; return true; } },
  };
  if (supported) window.CloseWatcher = CloseWatcher;
  const document = {
    visibilityState: "visible", documentElement: {},
    getElementById: id => id === "app" && screen === "dashboard" ? {} : null,
    querySelector: selector => selector === "dialog[open]" ? (modal ? {} : null)
      : selector === "[data-app-welcome]" && screen === "welcome" ? {} : null,
    addEventListener: (name, fn) => add(docHandlers, name, fn),
  };
  class MutationObserver { constructor(fn) { mutation = fn; } observe() {} }
  vm.runInNewContext(source, { window, document, MutationObserver, URL, Set, JSON });
  const live = () => watchers.filter(watcher => watcher.live);
  const nonce = "0123456789abcdef0123456789abcdef";
  const port = {
    sent: [], closed: false,
    postMessage(value) { this.sent.push(JSON.parse(value)); },
    start() { this.started = true; }, close() { this.closed = true; },
  };
  function connect(overrides = {}) {
    emit(handlers, "message", {
      isTrusted: true, source: null, origin: "android-app://ennoic.onrender.com", ports: [port], data: "", ...overrides,
    });
  }
  function nativeHello(overrides = {}) {
    port.onmessage?.({ data: JSON.stringify({ type: "ennoic.native", version: 1, capability: "back-v1", nonce, ...overrides }) });
  }
  function hello() { connect(); nativeHello(); }
  function ack(state = "reset", overrides = {}) {
    const requestId = port.sent.at(-1)?.requestId;
    port.onmessage?.({ data: JSON.stringify({ type: "ennoic.native.back", version: 1, nonce, requestId, state, ...overrides }) });
  }
  function advance(amount) {
    now += amount;
    for (const [id, timer] of [...timers]) if (timer.due <= now) { timers.delete(id); timer.fn(); }
  }
  return {
    window, document, port, connect, nativeHello, hello, ack, live, advance, entries, replacements, timers,
    watchers, get traversals() { return traversals; }, get panelsClosed() { return panelsClosed; },
    close: () => live().at(-1).close(),
    event: (name, event) => emit(handlers, name, event),
    documentEvent: (name, event) => emit(docHandlers, name, event),
    modal(value) { modal = value; mutation?.(); }, panel(value) { panel = value; },
  };
}

test("ordinary browser and unbridged PWA navigation have no watcher or history changes", () => {
  const state = setup();
  assert.equal(state.live().length, 0);
  state.documentEvent("pointerdown");
  state.documentEvent("input");
  state.event("pageshow", { persisted: true });
  assert.deepEqual(state.port.sent, []);
  assert.deepEqual(state.replacements, []);
  assert.equal(state.traversals, 0);
  const unsupported = setup({ supported: false });
  unsupported.hello();
  assert.equal(unsupported.window.EnnoicAndroidBack, undefined);
  assert.equal(unsupported.live().length, 0);
});

test("port transfer rejects synthetic, framed, foreign-origin and nonempty window messages", () => {
  const state = setup();
  for (const invalid of [
    { isTrusted: false }, { source: state.window }, { source: {} },
    { origin: "https://attacker.invalid" }, { origin: "https://ennoic.onrender.com" }, { origin: "null" },
    { origin: "android-app://attacker.invalid" }, { origin: "android-app://ennoic.onrender.com.attacker.invalid" },
    { ports: [] }, { ports: [state.port, state.port] },
    { data: "invalid-json" }, { data: JSON.stringify({ type: "ennoic.native", version: 1, capability: "back-v1", nonce: "short" }) },
    { data: JSON.stringify({ type: "ennoic.native", version: 2, capability: "back-v1", nonce: "0".repeat(32) }) },
  ]) state.connect(invalid);
  assert.deepEqual(state.port.sent, []);
  assert.equal(state.live().length, 0);
  state.hello();
  assert.equal(state.port.sent[0].action, "reset");
  assert.equal(state.live().length, 0); // A live round trip is required.
  state.ack("reset");
  assert.equal(state.live().length, 1);
});

test("Chrome transfers an empty window message before HELLO and acknowledgments arrive on its port", () => {
  const state = setup();
  state.connect();
  assert.equal(state.port.started, true);
  assert.deepEqual(state.port.sent, []);
  assert.equal(state.live().length, 0);
  state.nativeHello();
  assert.equal(state.port.sent.at(-1).action, "reset");
  assert.equal(state.live().length, 0);
  state.ack();
  assert.equal(state.live().length, 1);
});

test("missing or malformed HELLO cannot enable Back or extend the port handshake deadline", () => {
  const state = setup();
  state.connect();
  state.advance(300);
  state.nativeHello({ nonce: "short" });
  state.advance(300);
  state.nativeHello({ version: 2 });
  state.port.onmessage({ data: "bad-json" });
  state.advance(150);
  assert.equal(state.port.closed, true);
  assert.deepEqual(state.port.sent, []);
  assert.equal(state.live().length, 0);
});

test("root Back delegates two presses to native, without history mutation or cancel handlers", () => {
  const state = setup();
  state.hello(); state.ack();
  assert.equal(state.live()[0].listeners.has("cancel"), false);
  state.close();
  assert.equal(state.port.sent.at(-1).action, "root");
  state.ack("armed");
  state.close();
  assert.equal(state.port.sent.at(-1).action, "root");
  state.ack("exiting");
  assert.equal(state.live().length, 0);
  assert.equal(state.port.closed, true);
  assert.deepEqual(state.replacements, []);
  assert.equal(state.traversals, 0);
});

test("a stale channel fails open within 750ms despite repeated Back presses", () => {
  const state = setup();
  state.hello(); state.ack();
  state.close();
  for (let index = 0; index < 6; index++) { state.advance(100); state.close(); }
  state.advance(149);
  assert.equal(state.live().length, 1);
  state.advance(1);
  assert.equal(state.live().length, 0);
  assert.equal(state.port.closed, true);
});

test("only matching acknowledgments establish liveness; unavailable and malformed ports fail open", () => {
  const state = setup();
  state.hello();
  state.ack("reset", { nonce: "f".repeat(32) });
  state.ack("reset", { requestId: 999 });
  state.ack("unknown");
  assert.equal(state.live().length, 0);
  state.advance(750);
  assert.equal(state.port.closed, true);
  const unavailable = setup();
  unavailable.hello(); unavailable.ack("unavailable");
  assert.equal(unavailable.live().length, 0);
  assert.equal(unavailable.port.closed, true);
  const error = setup();
  error.hello(); error.ack(); error.port.onmessageerror();
  assert.equal(error.live().length, 0);
  const broken = setup();
  broken.port.postMessage = () => { throw new Error("closed"); };
  broken.hello();
  assert.equal(broken.live().length, 0);
  assert.equal(broken.port.closed, true);
});

test("native dialogs synchronously suspend the app watcher and resume exactly once", () => {
  const state = setup();
  state.hello(); state.ack();
  state.close(); state.ack("armed");
  const resume = state.window.EnnoicAndroidBack.suspendDialog();
  assert.equal(state.live().length, 0);
  assert.equal(state.port.sent.at(-1).action, "reset");
  state.ack();
  assert.equal(state.live().length, 0);
  state.modal(true);
  resume(); resume();
  assert.equal(state.live().length, 0);
  state.modal(false);
  assert.equal(state.live().length, 1);
});

test("semantic panels consume one Back and reset an armed exit before the root", () => {
  const state = setup();
  state.hello(); state.ack();
  state.close(); state.ack("armed");
  state.panel(true); state.close();
  assert.equal(state.panelsClosed, 1);
  assert.equal(state.port.sent.at(-1).action, "reset");
  state.ack(); state.close();
  assert.equal(state.port.sent.at(-1).action, "root");
});

test("welcome after explicit sign-out exits immediately rather than revisiting private history", () => {
  const state = setup({ screen: "welcome" });
  state.hello(); state.ack(); state.close();
  assert.equal(state.port.sent.at(-1).action, "exit");
  assert.deepEqual(state.replacements, []);
  assert.equal(state.traversals, 0);
});

test("internal screens traverse only a known safe predecessor; auth/checkout/external history returns to /app", () => {
  const safe = setup({ path: "/settings", screen: "settings" });
  safe.entries.unshift({ key: "dashboard", url: "https://ennoic.onrender.com/app" });
  safe.hello(); safe.ack(); safe.close();
  assert.equal(safe.traversals, 1);
  assert.deepEqual(safe.replacements, []);
  for (const previous of ["https://accounts.google.com/", "https://ennoic.onrender.com/auth/google", "https://ennoic.onrender.com/login", "https://ennoic.onrender.com/checkout/success", null]) {
    const state = setup({ path: "/settings", screen: "settings" });
    if (previous) state.entries.unshift({ key: "previous", url: previous });
    state.hello(); state.ack(); state.close();
    assert.deepEqual(state.replacements, ["/app"]);
    assert.equal(state.traversals, 0);
  }
  const checkout = setup({ path: "/checkout/success", screen: "checkout" });
  checkout.entries.unshift({ key: "previous", url: "https://ennoic.onrender.com/settings" });
  checkout.hello(); checkout.ack(); checkout.close();
  assert.deepEqual(checkout.replacements, ["/app"]);
});

test("backgrounding resets exit and needs an acknowledged visible round trip; pagehide disconnects", () => {
  const state = setup();
  state.hello(); state.ack(); state.close(); state.ack("armed");
  state.document.visibilityState = "hidden";
  state.documentEvent("visibilitychange");
  assert.equal(state.live().length, 0);
  assert.equal(state.port.sent.at(-1).action, "reset");
  state.ack();
  assert.equal(state.live().length, 0);
  state.document.visibilityState = "visible";
  state.documentEvent("visibilitychange");
  assert.equal(state.live().length, 0);
  state.ack();
  assert.equal(state.live().length, 1);
  state.event("pagehide");
  assert.equal(state.live().length, 0);
  assert.equal(state.port.closed, true);
  state.event("pageshow", { persisted: true });
  assert.equal(state.live().length, 0); // A fresh native channel is required.
});

test("app interaction resets the native exit arm without intercepting pointer scrolling or input", () => {
  const state = setup();
  state.hello(); state.ack(); state.close(); state.ack("armed");
  state.documentEvent("pointerdown");
  assert.equal(state.port.sent.at(-1).action, "reset");
  state.ack();
  state.close(); state.ack("armed");
  state.documentEvent("input");
  assert.equal(state.port.sent.at(-1).action, "reset");
});
