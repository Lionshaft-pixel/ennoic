(() => {
  "use strict";
  if (window.__ennoicPwaStarted || !window.isSecureContext) return;
  window.__ennoicPwaStarted = true;
  let installPrompt = null;
  let iosHelpExpanded = false;
  const displayMode = window.matchMedia("(display-mode: standalone)");
  const isInstalled = () => displayMode.matches || navigator.standalone === true;
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent)
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

  function renderInstallOption() {
    const host = document.querySelector("[data-pwa-install-slot]");
    if (!host) return;
    host.replaceChildren();
    host.hidden = isInstalled() || (!installPrompt && !isIOS);
    if (host.hidden) return;
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.ennoicPwaAction = isIOS ? "ios-help" : "install";
    button.textContent = isIOS ? "Add Ennoic to Home Screen" : "Install Ennoic";
    host.append(button);
    if (isIOS) {
      const instructions = document.createElement("p");
      instructions.id = "ennoic-ios-install-help";
      instructions.hidden = !iosHelpExpanded;
      instructions.textContent = "In Safari, tap Share, then Add to Home Screen. Keep Open as Web App enabled if shown, then tap Add. If you're using another browser, open this page in Safari first.";
      button.setAttribute("aria-controls", instructions.id);
      button.setAttribute("aria-expanded", String(iosHelpExpanded));
      host.append(instructions);
    }
  }

  // The landing renderer can recreate nodes while mounting. Delegation survives
  // both its rendering and the saved-page document replacement.
  document.addEventListener("click", async (event) => {
    const button = event.target instanceof Element
      ? event.target.closest("button[data-ennoic-pwa-action]") : null;
    if (!button || !button.closest("[data-pwa-install-slot]")) return;
    if (button.dataset.ennoicPwaAction === "ios-help") {
      iosHelpExpanded = !iosHelpExpanded;
      const instructions = document.getElementById("ennoic-ios-install-help");
      if (instructions) instructions.hidden = !iosHelpExpanded;
      button.setAttribute("aria-expanded", String(iosHelpExpanded));
      return;
    }
    const prompt = installPrompt;
    installPrompt = null;
    button.disabled = true;
    try {
      if (prompt) {
        await prompt.prompt();
        await prompt.userChoice;
      }
    } catch (error) {
      console.info("Ennoic installation was not completed.");
    } finally {
      renderInstallOption();
    }
  }, true);

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    installPrompt = event;
    renderInstallOption();
  });
  window.addEventListener("appinstalled", () => {
    installPrompt = null;
    renderInstallOption();
  });
  displayMode.addEventListener?.("change", renderInstallOption);
  // The saved landing/auth bundles replace the DOM, but window listeners survive.
  window.addEventListener("ennoic:page-ready", renderInstallOption);
  renderInstallOption();

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/service-worker.js", {scope: "/", updateViaCache: "none"})
      .then((registration) => {
        const checkForUpdate = () => registration.update().catch(() => {});
        checkForUpdate();
        document.addEventListener("visibilitychange", () => {
          if (document.visibilityState === "visible") checkForUpdate();
        });
        window.addEventListener("online", checkForUpdate);
      })
      .catch(() => console.info("Ennoic offline support is unavailable in this browser session."));
  }
})();
