/* Acknowledge full page navigation without delaying it or changing its behavior. */
(() => {
  "use strict";
  if (window.EnnoicNavigation) return;

  let pendingSource = null;
  let previousBusy = null;
  let resetTimer = null;

  function reset() {
    if (resetTimer !== null) window.clearTimeout(resetTimer);
    resetTimer = null;
    const progress = document.getElementById("ennoic-navigation-progress");
    if (progress) {
      progress.hidden = true;
      progress.querySelector("[role=status]").textContent = "";
    }
    if (pendingSource) {
      if (previousBusy === null) pendingSource.removeAttribute("aria-busy");
      else pendingSource.setAttribute("aria-busy", previousBusy);
    }
    pendingSource = null;
    previousBusy = null;
  }

  function start(source) {
    reset();
    let progress = document.getElementById("ennoic-navigation-progress");
    if (!progress) {
      progress = document.createElement("div");
      progress.id = "ennoic-navigation-progress";
      progress.className = "ennoic-navigation-progress";
      const status = document.createElement("span");
      status.className = "ennoic-navigation-status";
      status.setAttribute("role", "status");
      status.setAttribute("aria-live", "polite");
      progress.append(status);
      document.body.append(progress);
    }
    progress.hidden = false;
    progress.querySelector("[role=status]").textContent = "Opening page…";
    if (source) {
      pendingSource = source;
      previousBusy = source.getAttribute("aria-busy");
      source.setAttribute("aria-busy", "true");
    }
    // Canceled navigation and failed downloads must never leave a control stuck.
    resetTimer = window.setTimeout(reset, 15000);
  }

  function onClick(event) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest?.("a[href]");
    if (!link || link.hasAttribute("download") || link.getAttribute("aria-disabled") === "true") return;
    const target = link.getAttribute("target");
    if (target && target.toLowerCase() !== "_self") return;
    const href = link.getAttribute("href");
    if (!href || href.startsWith("#")) return;
    let destination;
    try { destination = new URL(href, window.location.href); } catch { return; }
    if (destination.origin !== window.location.origin || !["https:", "http:"].includes(destination.protocol)) return;
    const current = new URL(window.location.href);
    if (destination.pathname === current.pathname && destination.search === current.search && destination.hash) return;
    start(link);
  }

  window.EnnoicNavigation = { start, reset };
  // Window bubbling observes defaultPrevented from application link handlers.
  window.addEventListener("click", onClick);
  window.addEventListener("pageshow", reset);
  window.addEventListener("pagehide", reset);
})();
