const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");

const source = fs.readFileSync(require.resolve("../static/app-navigation.js"), "utf8");

function setup() {
  const elements = [];
  const listeners = {};
  const timers = new Map();
  let nextTimer = 1;
  function element(attributes = {}) {
    return {
      attributes: { ...attributes }, children: [], hidden: false, textContent: "",
      getAttribute(name) { return this.attributes[name] ?? null; },
      setAttribute(name, value) { this.attributes[name] = value; },
      removeAttribute(name) { delete this.attributes[name]; },
      hasAttribute(name) { return Object.hasOwn(this.attributes, name); },
      append(child) { this.children.push(child); elements.push(child); },
      querySelector() { return this.children[0]; },
      closest() { return this; },
    };
  }
  const window = {
    location: { href: "https://ennoic.onrender.com/", origin: "https://ennoic.onrender.com" },
    addEventListener(name, fn) { listeners[name] = fn; },
    setTimeout(fn) { const id = nextTimer++; timers.set(id, fn); return id; },
    clearTimeout(id) { timers.delete(id); },
  };
  const document = {
    createElement: () => element(),
    body: element(),
    getElementById: id => elements.find(el => el.id === id) || null,
  };
  vm.runInNewContext(source, { window, document, URL });
  const progress = () => document.getElementById("ennoic-navigation-progress");
  function click(link, options = {}) {
    listeners.click({ button: 0, target: link, defaultPrevented: false, ...options });
  }
  return { window, listeners, timers, element, click, progress };
}

test("same-origin page links immediately acknowledge navigation without disabling them", () => {
  const { element, click, progress } = setup();
  const link = element({ href: "/pricing" });
  click(link);
  assert.equal(progress().hidden, false);
  assert.equal(progress().children[0].textContent, "Opening page…");
  assert.equal(link.getAttribute("aria-busy"), "true");
  assert.equal(link.hasAttribute("disabled"), false);
  assert.equal(link.getAttribute("href"), "/pricing");
});

test("modified, canceled, external, hash, download and new-window clicks stay untouched", () => {
  const { element, click, progress } = setup();
  for (const options of [{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }, { defaultPrevented: true }]) {
    click(element({ href: "/pricing" }), options);
  }
  for (const attributes of [
    { href: "https://accounts.google.com/" }, { href: "mailto:test@example.com" },
    { href: "#settings" }, { href: "/#settings" }, { href: "/pricing", download: "" },
    { href: "/pricing", target: "_blank" }, { href: "/pricing", target: "billing" },
    { href: "/pricing", "aria-disabled": "true" }, { href: "" },
  ]) click(element(attributes));
  click({ closest: () => null }); // Buttons and asynchronous form actions.
  assert.equal(progress(), null);
});

test("BFCache return, page exit and canceled navigation clear status and restore aria-busy", () => {
  const { window, listeners, timers, element, click, progress } = setup();
  const link = element({ href: "/settings", "aria-busy": "false" });
  click(link);
  listeners.pageshow({ persisted: true });
  assert.equal(progress().hidden, true);
  assert.equal(link.getAttribute("aria-busy"), "false");
  assert.equal(timers.size, 0);
  window.EnnoicNavigation.start(link);
  listeners.pagehide();
  assert.equal(progress().hidden, true);
  window.EnnoicNavigation.start(link);
  [...timers.values()][0]();
  assert.equal(progress().hidden, true);
  assert.equal(link.getAttribute("aria-busy"), "false");
});

test("switching pending controls restores each original accessibility state", () => {
  const { window, element, progress } = setup();
  const first = element();
  const second = element({ "aria-busy": "false" });
  window.EnnoicNavigation.start(first);
  window.EnnoicNavigation.start(second);
  assert.equal(first.getAttribute("aria-busy"), null);
  assert.equal(second.getAttribute("aria-busy"), "true");
  window.EnnoicNavigation.reset();
  assert.equal(progress().hidden, true);
  assert.equal(second.getAttribute("aria-busy"), "false");
});
