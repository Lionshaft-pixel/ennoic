const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");

const source = fs.readFileSync(require.resolve("../static/app-confirmation.js"), "utf8");

function setup({ legacy = false } = {}) {
  const document = { activeElement: null };
  function element(tagName = "div") {
    const listeners = new Map();
    const classes = new Set();
    const result = {
      tagName, children: [], attributes: {}, dataset: {}, style: {}, value: "", open: false,
      textContent: "", inert: false, isConnected: true, scrollTop: 0,
      getBoundingClientRect() { return this.bounds || { top: 0, bottom: 0 }; },
      classList: { contains: name => classes.has(name), add: name => classes.add(name), remove: name => classes.delete(name) },
      append(...children) { children.forEach(child => { child.parentElement = this; this.children.push(child); }); },
      setAttribute(name, value) { this.attributes[name] = value; if (name === "open") this.open = true; },
      addEventListener(name, callback) { listeners.set(name, [...listeners.get(name) || [], callback]); },
      removeEventListener(name, callback) { listeners.set(name, (listeners.get(name) || []).filter(item => item !== callback)); },
      dispatch(name, values = {}) {
        const event = { defaultPrevented: false, propagationStopped: false,
          preventDefault() { this.defaultPrevented = true; },
          stopPropagation() { this.propagationStopped = true; }, ...values };
        for (const listener of [...listeners.get(name) || []]) listener(event);
        return event;
      },
      focus(options) { document.activeElement = this; this.focusOptions = options; },
      remove() {
        this.isConnected = false;
        if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(child => child !== this);
      },
      reportValidity() { this.validityChecked = true; return !this.required || this.value.length > 0; },
      showModal() { this.open = true; },
      close() { this.open = false; this.dispatch("close"); },
      listeners,
    };
    if (legacy && tagName === "dialog") result.showModal = undefined;
    return result;
  }
  document.createElement = element;
  document.body = element("body");
  document.documentElement = element("html");
  const viewport = element();
  Object.assign(viewport, { width: 800, height: 1280, offsetTop: 0, offsetLeft: 0 });
  const window = element();
  window.visualViewport = viewport;
  const trigger = element("button");
  document.body.append(trigger);
  trigger.focus();
  vm.runInNewContext(source, { window, document, Promise });
  const dialog = () => document.body.children.find(child => child.tagName === "dialog");
  const descend = (node) => [node, ...node.children.flatMap(descend)];
  const find = predicate => descend(dialog()).find(predicate);
  const cancel = () => find(node => Object.hasOwn(node.dataset, "confirmationCancel"));
  const submit = () => find(node => Object.hasOwn(node.dataset, "confirmationAccept"));
  const form = () => find(node => node.tagName === "form");
  const input = () => find(node => node.tagName === "input");
  return { document, window, viewport, trigger, dialog, find, cancel, submit, form, input, api: window.EnnoicConfirmation };
}

const options = {
  title: "Delete account", message: "Data is removed after 30 days.",
  confirmLabel: "Delete account", cancelLabel: "Cancel", danger: true,
};

test("confirmation is text-only, accessible, and initially focuses Cancel", async () => {
  const state = setup();
  const decision = state.api.confirm({ ...options, message: '<img src=x onerror="alert(1)">' });
  assert.equal(state.dialog().attributes["aria-labelledby"], "ennoic-confirmation-title");
  assert.equal(state.dialog().attributes["aria-describedby"], "ennoic-confirmation-message");
  assert.equal(state.find(node => node.id === "ennoic-confirmation-message").textContent, '<img src=x onerror="alert(1)">');
  assert.equal(state.document.activeElement, state.cancel());
  assert.equal(state.submit().className, "ennoic-confirmation-danger");
  state.cancel().dispatch("click");
  assert.equal(await decision, false);
  assert.equal(state.dialog(), undefined);
  assert.equal(state.document.activeElement, state.trigger);
  assert.equal(state.document.documentElement.classList.contains("ennoic-confirmation-open"), false);
});

test("Escape and external close cancel, with one pending decision and no duplicate action", async () => {
  const state = setup();
  const first = state.api.confirm(options);
  assert.equal(await state.api.confirm(options), false);
  const escape = state.dialog().dispatch("keydown", { key: "Escape" });
  assert.equal(escape.defaultPrevented, true);
  assert.equal(escape.propagationStopped, true);
  assert.equal(await first, false);
  const second = state.api.confirm(options);
  state.dialog().close();
  assert.equal(await second, false);
  const third = state.api.confirm(options);
  state.form().dispatch("submit");
  assert.equal(await third, true);
});

test("account deletion requires typed input and returns it unchanged for server validation", async () => {
  const state = setup();
  const decision = state.api.prompt({ ...options, inputLabel: "Type person@example.com to confirm account deletion." });
  const input = state.input();
  assert.equal(state.find(node => node.tagName === "label").htmlFor, input.id);
  assert.equal(input.autocomplete, "off");
  assert.equal(input.required, true);
  state.form().dispatch("submit");
  assert.equal(input.validityChecked, true);
  assert.ok(state.dialog()); // An empty submission cannot authorize deletion.
  input.value = "  PERSON@example.com  ";
  state.form().dispatch("submit");
  assert.equal(await decision, "  PERSON@example.com  ");
});

test("keyboard focus wraps inside the modal and visual viewport changes keep actions reachable", async () => {
  const state = setup();
  const decision = state.api.prompt({ ...options, inputLabel: "Type your email" });
  state.input().focus();
  assert.equal(state.dialog().dispatch("keydown", { key: "Tab", shiftKey: true }).defaultPrevented, true);
  assert.equal(state.document.activeElement, state.submit());
  assert.equal(state.dialog().dispatch("keydown", { key: "Tab", shiftKey: false }).defaultPrevented, true);
  assert.equal(state.document.activeElement, state.input());
  const body = state.find(node => node.className === "ennoic-confirmation-body");
  body.bounds = { top: 50, bottom: 300 };
  state.input().bounds = { top: 390, bottom: 436 };
  Object.assign(state.viewport, { height: 430, width: 1280, offsetTop: 12 });
  state.viewport.dispatch("resize");
  assert.equal(body.scrollTop, 144); // Focused email is scrolled above the action row.
  assert.equal(state.dialog().style.maxHeight, "398px");
  assert.equal(state.dialog().style.top, "227px");
  assert.equal(state.dialog().style.left, "640px");
  Object.assign(state.viewport, { scale: 2, offsetTop: 100, height: 215 });
  state.viewport.dispatch("scroll");
  assert.equal(state.dialog().style.top, "227px"); // Pinch zoom and panning stay browser-controlled.
  state.cancel().dispatch("click");
  assert.equal(await decision, null);
  assert.equal(state.viewport.listeners.get("resize").length, 0);
  assert.equal(state.viewport.listeners.get("scroll").length, 0);
});

test("older browsers receive a custom modal and restore existing background inert state", async () => {
  const state = setup({ legacy: true });
  const decision = state.api.confirm(options);
  assert.equal(state.dialog().attributes["aria-modal"], "true");
  assert.equal(state.trigger.inert, true);
  assert.equal(state.document.body.children.length, 3);
  state.cancel().dispatch("click");
  assert.equal(await decision, false);
  assert.equal(state.trigger.inert, false);
  assert.equal(state.document.body.children.length, 1);
});
