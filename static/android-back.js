/* Android Back is enabled only by the verified Ennoic TWA's native channel. */
(() => {
  "use strict";
  if (window.EnnoicAndroidBack || typeof window.CloseWatcher !== "function") return;

  const safePaths = new Set(["/", "/app", "/settings", "/pricing", "/terms", "/privacy", "/refund-policy", "/admin/users"]);
  const nativeOrigin = "android-app://ennoic.onrender.com";
  const ackTimeout = 750;
  let port = null;
  let nonce = null;
  let watcher = null;
  let pending = null;
  let helloTimer = null;
  let sequence = 0;
  let ready = false;
  let armed = false;
  let dialogDepth = 0;

  function destroyWatcher() {
    watcher?.destroy();
    watcher = null;
  }

  function disconnect() {
    destroyWatcher();
    if (pending) window.clearTimeout(pending.timer);
    if (helloTimer !== null) window.clearTimeout(helloTimer);
    helloTimer = null;
    pending = null;
    ready = false;
    armed = false;
    if (port) {
      port.onmessage = null;
      port.onmessageerror = null;
      port.close();
    }
    port = null;
    nonce = null;
  }

  function send(action) {
    if (!port || !nonce) return false;
    const requestId = sequence = sequence % 2147483647 + 1;
    // A dead wrapper must never leave the browser's real Back consumed forever.
    // Keep the oldest unacknowledged deadline even if Back is pressed rapidly.
    // Repeated requests must not keep an unresponsive wrapper alive indefinitely.
    pending = { requestId, action, timer: pending?.timer ?? window.setTimeout(disconnect, ackTimeout) };
    try {
      port.postMessage(JSON.stringify({ type: "ennoic.back", version: 1, nonce, requestId, action }));
      return true;
    } catch {
      disconnect();
      return false;
    }
  }

  function resetExit() {
    if ((armed || pending?.action === "root") && port) send("reset");
    armed = false;
  }

  function modalOpen() {
    return dialogDepth > 0 || Boolean(document.querySelector("dialog[open]"));
  }

  function previousAppEntry() {
    const navigation = window.navigation;
    if (!navigation?.entries || !navigation.currentEntry) return false;
    const entries = navigation.entries();
    const index = entries.findIndex((entry) => entry.key === navigation.currentEntry.key);
    if (index < 1) return false;
    try {
      const destination = new URL(entries[index - 1].url);
      return destination.origin === window.location.origin && safePaths.has(destination.pathname);
    } catch { return false; }
  }

  function back() {
    watcher = null; // A real close request consumes this watcher.
    if (!ready || document.visibilityState === "hidden" || modalOpen()) return;
    if (window.EnnoicAppBack?.closePanel()) {
      resetExit();
    } else if (document.getElementById("app") && ["/", "/app"].includes(window.location.pathname)) {
      // Native owns the two-second clock, toast, and this application's task exit.
      send("root");
    } else if (document.querySelector("[data-app-welcome]") && window.location.pathname === "/app") {
      send("exit");
    } else {
      resetExit();
      destroyWatcher();
      // Never traverse a completed auth, provider, or checkout predecessor.
      if (safePaths.has(window.location.pathname) && previousAppEntry()) window.navigation.back();
      else window.location.replace("/app");
      return;
    }
    refresh();
  }

  function refresh() {
    if (!ready || !port || document.visibilityState === "hidden" || modalOpen()) {
      destroyWatcher();
      return;
    }
    if (watcher) return;
    try {
      watcher = new window.CloseWatcher();
      // No cancel/preventDefault, history sentinels, or synthetic Back entries.
      watcher.addEventListener("close", back);
    } catch { disconnect(); }
  }

  function suspendDialog() {
    dialogDepth++;
    destroyWatcher();
    resetExit();
    let resumed = false;
    return () => {
      if (resumed) return;
      resumed = true;
      dialogDepth--;
      refresh();
    };
  }

  function receive(event) {
    // Chrome first transfers a port with an empty window message. Its verified
    // native origin is android-app://<host>; the app's HELLO arrives on the port.
    if (!event.isTrusted || event.source !== null || event.origin !== nativeOrigin
      || event.data !== "" || event.ports?.length !== 1) return;
    disconnect();
    port = event.ports[0];
    helloTimer = window.setTimeout(disconnect, ackTimeout);
    port.onmessageerror = disconnect;
    port.onmessage = (reply) => {
      let data;
      try { data = JSON.parse(reply.data); } catch { return; }
      if (nonce === null) {
        if (data?.type !== "ennoic.native" || data.version !== 1 || data.capability !== "back-v1" || !/^[a-f0-9]{32}$/.test(data.nonce)) return;
        window.clearTimeout(helloTimer);
        helloTimer = null;
        nonce = data.nonce;
        // A round trip confirms this channel is live before it can consume Back.
        send("reset");
        return;
      }
      if (!pending || data?.type !== "ennoic.native.back" || data.version !== 1 || data.nonce !== nonce || data.requestId !== pending.requestId) return;
      if (!["armed", "exiting", "reset", "unavailable"].includes(data.state)) return;
      window.clearTimeout(pending.timer);
      pending = null;
      if (data.state === "unavailable" || data.state === "exiting") {
        disconnect();
        return;
      }
      armed = data.state === "armed";
      ready = true;
      refresh();
    };
    port.start?.();
  }

  window.EnnoicAndroidBack = { suspendDialog, reset: resetExit };
  window.addEventListener("message", receive);
  window.addEventListener("pagehide", () => { if (port) send("reset"); disconnect(); });
  document.addEventListener("visibilitychange", () => {
    destroyWatcher();
    ready = false;
    armed = false;
    if (port) send("reset");
  });
  // Interacting with the app cancels an armed exit without affecting scrolling.
  document.addEventListener("pointerdown", resetExit, { passive: true });
  document.addEventListener("input", resetExit, { passive: true });
  window.navigation?.addEventListener("navigate", () => {
    destroyWatcher();
    ready = false;
    resetExit();
  });
  new MutationObserver(() => {
    if (modalOpen()) resetExit();
    refresh();
  }).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["open"] });
})();
