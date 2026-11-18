const assert = require("node:assert/strict");
const test = require("node:test");

global.window = {};
require("../static/subscription-ui.js");

function element(hidden = false) {
  return { hidden };
}

function setup(fetchEntitlements) {
  const view = {
    upgrade: element(false),
    loading: element(true),
    retry: element(false),
    badge: element(true),
  };
  const controller = window.EnnoicSubscriptionUI.createSubscriptionView({ ...view, fetchEntitlements });
  return { view, controller };
}

test("loading keeps Upgrade hidden until Free entitlements arrive", async () => {
  let resolve;
  const { view, controller } = setup(() => new Promise((done) => { resolve = done; }));
  const pending = controller.refresh();
  assert.equal(view.upgrade.hidden, true);
  assert.equal(view.loading.hidden, false);
  assert.equal(view.retry.hidden, true);
  resolve({ plan: "free" });
  await pending;
  assert.equal(view.upgrade.hidden, false);
  assert.equal(view.loading.hidden, true);
  assert.equal(view.retry.hidden, true);
});

test("Pro entitlements hide Upgrade and show the gold badge", async () => {
  const { view, controller } = setup(async () => ({ plan: "pro" }));
  await controller.refresh();
  assert.equal(view.upgrade.hidden, true);
  assert.equal(view.badge.hidden, false);
  assert.equal(view.loading.hidden, true);
});

test("failed entitlement requests show Retry without exposing Upgrade", async () => {
  const { view, controller } = setup(async () => { throw new Error("offline"); });
  await controller.refresh();
  assert.equal(view.upgrade.hidden, true);
  assert.equal(view.loading.hidden, true);
  assert.equal(view.retry.hidden, false);
});
