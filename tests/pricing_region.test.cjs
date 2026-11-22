const assert = require("node:assert/strict");
const test = require("node:test");

const { isIndiaPricing } = require("../static/pricing-region.js");

test("Indian Paddle preview reveals the UPI notice even if browser language is English US", () => {
  const preview = {
    data: {
      currencyCode: "INR",
      details: { address: { countryCode: "IN" } },
    },
  };
  assert.equal(isIndiaPricing("USD", preview), true);
});

test("international localized prices keep the India UPI notice hidden", () => {
  const preview = {
    data: {
      currencyCode: "USD",
      details: { address: { countryCode: "US" } },
    },
  };
  assert.equal(isIndiaPricing("USD", preview), false);
});

test("existing INR pricing region is used when Paddle preview is unavailable", () => {
  assert.equal(isIndiaPricing("INR", null), true);
  assert.equal(isIndiaPricing("USD", null), false);
});
