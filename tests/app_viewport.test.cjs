const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");

const source = fs.readFileSync(require.resolve("../static/app-viewport.js"), "utf8");

function setup({ responsive = true, visualViewport = true } = {}) {
  const values = new Map();
  const listeners = {};
  const frames = [];
  const events = [];
  const app = { dataset: {}, style: {
    setProperty: (name, value) => values.set(name, value),
    removeProperty: name => values.delete(name),
  } };
  const viewport = { height: 844, offsetTop: 0, scale: 1,
    addEventListener: (name, listener) => { listeners[`visual:${name}`] = listener; } };
  const media = { matches: responsive,
    addEventListener: (name, listener) => { listeners[`media:${name}`] = listener; } };
  const window = { visualViewport: visualViewport ? viewport : undefined, innerHeight: 844,
    matchMedia: () => media,
    addEventListener: (name, listener) => { listeners[name] = listener; },
    requestAnimationFrame: listener => frames.push(listener),
    dispatchEvent: event => events.push(event.type),
  };
  vm.runInNewContext(source, { window, document: { getElementById: () => app }, Event: class Event {
    constructor(type) { this.type = type; }
  } });
  return { app, window, viewport, media, values, listeners, events, frames,
    flush() { while (frames.length) frames.shift()(); } };
}

test("keyboard shrink, viewport pan, recovery and rotation update visible bounds together", () => {
  const state = setup();
  assert.equal(state.values.get("--app-visible-height"), "844px");
  state.viewport.height = 330;
  state.viewport.offsetTop = 24;
  state.listeners["visual:resize"]();
  state.listeners["visual:scroll"]();
  assert.equal(state.frames.length, 1, "a burst of keyboard changes uses one layout update");
  state.flush();
  assert.equal(state.values.get("--app-visible-height"), "330px");
  assert.equal(state.values.get("--app-visible-top"), "24px");
  assert.equal(state.app.dataset.viewportCompact, "true");
  state.viewport.height = 800;
  state.viewport.offsetTop = 0;
  state.listeners.resize();
  state.flush();
  assert.equal(state.values.get("--app-visible-height"), "800px");
  assert.equal(state.app.dataset.viewportCompact, "false");
  assert.equal(state.events.length, 3);
});

test("pinch zoom stays browser-controlled and resumes normal bounds after zoom ends", () => {
  const state = setup();
  state.viewport.height = 320;
  state.viewport.offsetTop = 120;
  state.viewport.scale = 2;
  state.listeners["visual:resize"](); state.flush();
  assert.equal(state.values.get("--app-visible-height"), "844px");
  assert.equal(state.values.get("--app-visible-top"), "0px");
  state.viewport.scale = 1;
  state.viewport.height = 620;
  state.viewport.offsetTop = 0;
  state.listeners["visual:resize"](); state.flush();
  assert.equal(state.values.get("--app-visible-height"), "620px");
});

test("desktop layout remains CSS-controlled, including responsive transitions and BFCache", () => {
  const state = setup({ responsive: false });
  assert.equal(state.values.size, 0);
  state.media.matches = true;
  state.listeners["media:change"](); state.flush();
  assert.equal(state.values.get("--app-visible-height"), "844px");
  state.media.matches = false;
  state.listeners["media:change"](); state.flush();
  assert.equal(state.values.size, 0);
  assert.equal(state.app.dataset.viewportCompact, undefined);
  state.media.matches = true;
  state.viewport.height = 768;
  state.listeners.pageshow(); state.flush();
  assert.equal(state.values.get("--app-visible-height"), "768px");
});

test("older engines fall back to innerHeight and transient invalid metrics are ignored", () => {
  const state = setup({ visualViewport: false });
  state.window.innerHeight = 410;
  state.listeners.resize(); state.flush();
  assert.equal(state.values.get("--app-visible-height"), "410px");
  assert.equal(state.values.get("--app-visible-top"), "0px");
  state.window.innerHeight = 0;
  state.listeners.resize(); state.flush();
  assert.equal(state.values.get("--app-visible-height"), "410px");
});
