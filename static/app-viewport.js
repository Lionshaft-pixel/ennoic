/* Keep the workspace inside the visible viewport when a mobile keyboard opens.
   No UA detection, focus stealing, scroll resets, or touch-event interception. */
(() => {
  "use strict";
  const app = document.getElementById("app");
  if (!app) return;
  const responsive = window.matchMedia("(max-width: 940px), (pointer: coarse) and (max-width: 1440px)");
  const viewport = window.visualViewport;
  let pending = false;
  let previous = "";

  function update() {
    pending = false;
    if (!responsive.matches) {
      app.style.removeProperty("--app-visible-height");
      app.style.removeProperty("--app-visible-top");
      delete app.dataset.viewportCompact;
      previous = "";
      return;
    }
    // Pinch zoom must keep normal browser magnification and panning. Resizing the
    // layout to its zoomed visual viewport would counteract that accessibility.
    if (viewport && Math.abs(viewport.scale - 1) > 0.02) return;
    const height = viewport ? viewport.height : window.innerHeight;
    const top = viewport ? Math.max(0, viewport.offsetTop) : 0;
    if (!Number.isFinite(height) || height <= 0 || !Number.isFinite(top)) return;
    const next = `${height}:${top}`;
    if (next === previous) return;
    previous = next;
    app.style.setProperty("--app-visible-height", `${height}px`);
    app.style.setProperty("--app-visible-top", `${top}px`);
    app.dataset.viewportCompact = String(height < 420);
    window.dispatchEvent(new Event("ennoic:viewportchange"));
  }

  function schedule() {
    if (pending) return;
    pending = true;
    window.requestAnimationFrame(update);
  }

  window.addEventListener("resize", schedule, { passive: true });
  window.addEventListener("pageshow", schedule);
  responsive.addEventListener("change", schedule);
  viewport?.addEventListener("resize", schedule, { passive: true });
  // Some engines pan the visual viewport while focusing an input, without a
  // second resize. Follow that offset rather than pushing the composer below it.
  viewport?.addEventListener("scroll", schedule, { passive: true });
  update();
})();
