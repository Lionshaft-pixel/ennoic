const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const { isIndiaPricing } = require("../static/pricing-region.js");

test("Indian Paddle preview reveals the UPI notice even if browser language is English US", () => {
  const preview = {
    data: {
      currencyCode: "INR",
      address: { countryCode: "IN" },
    },
  };
  assert.equal(isIndiaPricing("USD", preview), true);
});

test("international localized prices keep the India UPI notice hidden", () => {
  const preview = {
    data: {
      currencyCode: "USD",
      address: { countryCode: "US" },
    },
  };
  assert.equal(isIndiaPricing("USD", preview), false);
});

test("existing INR pricing region is used when Paddle preview is unavailable", () => {
  assert.equal(isIndiaPricing("INR", null), true);
  assert.equal(isIndiaPricing("USD", null), false);
});

test("a non-Indian preview address hides UPI notice even for an INR price", () => {
  assert.equal(isIndiaPricing("INR", {
    data: { currencyCode: "INR", address: { countryCode: "US" } },
  }), false);
});

test("an Indian address does not advertise UPI for a non-INR checkout", () => {
  assert.equal(isIndiaPricing("INR", {
    data: { currencyCode: "USD", address: { countryCode: "IN" } },
  }), false);
});

test("an INR preview without address retains the notice and normalizes codes", () => {
  assert.equal(isIndiaPricing("USD", { data: { currencyCode: "inr" } }), true);
  assert.equal(isIndiaPricing("USD", {
    data: { currencyCode: "inr", address: { countryCode: "in" } },
  }), true);
});

test("pricing and authenticated checkout work with a late-loaded Paddle SDK", async () => {
  const html = fs.readFileSync(path.join(__dirname, "../templates/pricing.html"), "utf8");
  const source = html.match(/<script nonce="\{\{ csp_nonce \}\}">([\s\S]*?)<\/script>/)[1]
    .replace("{{ authenticated|tojson }}", "true");
  const nodes = new Map();
  const listeners = new Map();
  const requests = [];
  const initializeCalls = [];
  const previewCalls = [];
  const checkoutCalls = [];
  const document = {
    getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, { hidden: false, addEventListener() {}, replaceChildren() {} });
      return nodes.get(id);
    },
    createElement() { return { appendChild() {} }; },
    querySelector() { return { content: "test_csrf_token" }; },
    addEventListener(type, listener) { listeners.set(type, listener); },
  };
  const window = {
    EnnoicPricingRegion: { isIndiaPricing }, addEventListener() {},
    location: { origin: "https://ennoic.example.test" },
  };
  const context = {
    window, document, AbortController, setTimeout, clearTimeout,
    navigator: { language: "en-US" }, localStorage: { getItem() { return null; } },
    fetch(url, options) {
      requests.push({ url, options });
      let body;
      if (url === "/api/entitlements") body = {
        plan: "free", usage: {
          ai_requests: { used: 0, limit: 10 }, memories: { used: 0, limit: 5 },
          conversations: { used: 0, limit: 5 },
        },
      };
      else if (url === "/api/paddle/checkout-intent") body = {
        email: "checkout@example.test", checkout_intent: "opaque_account_bound_intent",
      };
      else body = {
        copy: { per_month: "/month" }, features: { free: [], pro: [] },
        prices: { INR: { display: "₹179" } }, selected_currency: "INR",
        checkout_available: true,
        paddle: { environment: "production", client_token: "live_test", price_id: "pri_test" },
      };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    },
  };
  vm.runInNewContext(source, context);
  assert.deepEqual(requests, [], "API/startup must wait until the deferred SDK can execute");
  context.Paddle = window.Paddle = {
    Initialize(value) { initializeCalls.push(value); },
    PricePreview(value) {
      previewCalls.push(value);
      return Promise.resolve({ data: {
        currencyCode: "INR", address: { countryCode: "IN" },
        details: { lineItems: [{ formattedTotals: { subtotal: "₹179.00" } }] },
      } });
    },
    Checkout: { open(value) { checkoutCalls.push(value); } },
  };
  listeners.get("DOMContentLoaded")();
  await new Promise(setImmediate);

  assert.equal(requests.length, 3);
  assert.equal(initializeCalls[0].token, "live_test");
  assert.equal(previewCalls[0].items[0].priceId, "pri_test");
  assert.equal(document.getElementById("price-amount").textContent, "₹179.00");
  assert.equal(document.getElementById("india-upi-autopay-notice").hidden, false);
  assert.equal(document.getElementById("upgrade").disabled, false);
  document.getElementById("upgrade").onclick();
  await new Promise(setImmediate);
  const intent = requests.find(request => request.url === "/api/paddle/checkout-intent");
  assert.equal(intent.options.method, "POST");
  assert.equal(intent.options.headers["X-CSRFToken"], "test_csrf_token");
  assert.equal(checkoutCalls[0].items[0].priceId, "pri_test");
  assert.equal(checkoutCalls[0].items[0].quantity, 1);
  assert.equal(checkoutCalls[0].customData.ennoic_checkout_intent, "opaque_account_bound_intent");
  assert.equal(checkoutCalls[0].settings.successUrl, "https://ennoic.example.test/checkout/success");
});
