"use strict";

(() => {
  function createSubscriptionView({ upgrade, loading, retry, badge, fetchEntitlements }) {
    let requestNumber = 0;

    async function refresh() {
      const requestId = ++requestNumber;
      upgrade.hidden = true;
      loading.hidden = false;
      retry.hidden = true;
      try {
        const account = await fetchEntitlements();
        if (requestId !== requestNumber) return;
        if (!account || (account.plan !== "free" && account.plan !== "pro")) {
          throw new Error("Invalid entitlement response");
        }
        upgrade.hidden = account.plan !== "free";
        if (badge) badge.hidden = account.plan !== "pro";
        loading.hidden = true;
      } catch (_) {
        if (requestId !== requestNumber) return;
        loading.hidden = true;
        retry.hidden = false;
      }
    }

    return { refresh };
  }

  window.EnnoicSubscriptionUI = { createSubscriptionView };
})();
