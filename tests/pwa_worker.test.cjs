const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");

const source = fs.readFileSync(require.resolve("../static/pwa/service-worker.js"), "utf8");
const origin = "https://ennoic.onrender.com";
const assets = ["/offline", "/static/pwa/offline.css", "/static/pwa/offline.js", "/static/email/ennoic-logo.png"];

function worker() {
  const listeners = {};
  const cacheData = new Map();
  const deleted = [];
  const fetched = [];
  let failNetwork = false;
  let skipWaiting = false;
  let claimed = false;
  const caches = {
    async open(name) {
      if (!cacheData.has(name)) cacheData.set(name, new Map());
      const entries = cacheData.get(name);
      return {
        async put(key, response) { entries.set(key, response.clone()); },
        async match(key) { return entries.get(key)?.clone(); },
      };
    },
    async keys() { return [...cacheData.keys()]; },
    async delete(name) { deleted.push(name); return cacheData.delete(name); },
  };
  // Resolve browser-relative Request URLs in Node's test environment.
  class BrowserRequest extends Request {
    constructor(input, options) { super(new URL(input, origin), options); }
  }
  vm.runInNewContext(source, {
    URL, Request: BrowserRequest, Response, Set, Promise, caches,
    fetch: async (request) => {
      fetched.push(request);
      if (failNetwork) throw new TypeError("offline");
      return new Response(new URL(request.url).pathname === "/offline" ? "public fallback" : "network content");
    },
    self: {
      location: {origin}, addEventListener: (name, fn) => { listeners[name] = fn; },
      skipWaiting: async () => { skipWaiting = true; },
      clients: {claim: async () => { claimed = true; }},
    },
  });
  return {
    cacheData, deleted, fetched,
    offline() { failNetwork = true; },
    online() { failNetwork = false; },
    async lifecycle(name) {
      let work;
      listeners[name]({waitUntil: (value) => { work = value; }});
      await work;
    },
    fetch(path, options = {}) {
      let response;
      const request = {
        url: new URL(path, origin).href, method: "GET", mode: "cors",
        headers: new Headers(), ...options,
      };
      listeners.fetch({request, respondWith: (value) => { response = value; }});
      return response;
    },
    status: () => ({skipWaiting, claimed}),
  };
}

test("precache contains exactly public offline resources fetched without credentials", async () => {
  const sw = worker();
  await sw.lifecycle("install");
  assert.deepEqual(sw.fetched.map((request) => new URL(request.url).pathname), assets);
  for (const request of sw.fetched) {
    assert.equal(request.credentials, "omit");
    assert.equal(request.redirect, "error");
    assert.equal(request.cache, "reload");
  }
  assert.equal(sw.status().skipWaiting, true);
});

test("private/auth/payment/API and unapproved requests bypass the worker entirely", () => {
  const sw = worker();
  for (const path of ["/api/tasks", "/api/chat", "/api/conversations", "/api/auth/me", "/api/csrf",
    "/api/entitlements", "/login", "/app/email-help", "/app?form=register", "/auth/google", "/auth/google/callback?code=secret",
    "/auth/logout", "/auth/password-reset/token", "/checkout", "/checkout/success",
    "/api/paddle/portal", "/static/script.js", "/static/workspace.css", "/static/landing-page.html",
    "/static/authentication-page.html", "/?token=secret", "https://accounts.google.com/", "https://checkout.paddle.com/"]) {
    assert.equal(sw.fetch(path, {mode: "navigate"}), undefined, path);
  }
  assert.equal(sw.fetch("/", {method: "POST", mode: "navigate"}), undefined);
  assert.equal(sw.fetch("/", {mode: "navigate", headers: new Headers({Authorization: "Bearer secret"})}), undefined);
  assert.equal(sw.fetch("/static/email/ennoic-logo.png", {headers: new Headers({Range: "bytes=0-10"})}), undefined);
  assert.equal(sw.fetched.length, 0);
  assert.equal(sw.cacheData.size, 0);
});

test("navigations stay fresh online, fallback offline, and never populate the cache", async () => {
  const sw = worker();
  await sw.lifecycle("install");
  const online = await sw.fetch("/", {mode: "navigate"});
  assert.equal(await online.text(), "network content");
  assert.equal(await (await sw.fetch("/app", {mode: "navigate"})).text(), "network content");
  sw.offline();
  const offline = await sw.fetch("/settings", {mode: "navigate"});
  assert.equal(await offline.text(), "public fallback");
  assert.equal(await (await sw.fetch("/app", {mode: "navigate"})).text(), "public fallback");
  sw.online();
  assert.equal(await (await sw.fetch("/", {mode: "navigate"})).text(), "network content");
  assert.deepEqual([...([...sw.cacheData.values()][0].keys())], assets);
});

test("activation removes only obsolete Ennoic offline caches", async () => {
  const sw = worker();
  await sw.lifecycle("install");
  sw.cacheData.set("ennoic-public-offline-obsolete", new Map());
  sw.cacheData.set("another-app-cache", new Map());
  await sw.lifecycle("activate");
  assert.deepEqual(sw.deleted, ["ennoic-public-offline-obsolete"]);
  assert.equal(sw.cacheData.has("another-app-cache"), true);
  assert.deepEqual(sw.status(), {skipWaiting: true, claimed: true});
});

test("fallback remains safe if browser storage is evicted", async () => {
  const sw = worker();
  sw.offline();
  const response = await sw.fetch("/", {mode: "navigate"});
  assert.equal(response.status, 503);
  assert.match(await response.text(), /offline/);
});
