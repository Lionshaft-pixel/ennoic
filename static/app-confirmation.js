"use strict";

// Destructive actions stay in the page: no browser confirm/prompt dialogs.
(() => {
  let activeDialog = null;

  function show(options) {
    // Ignore a second activation while a decision is already pending.
    if (activeDialog) return Promise.resolve(null);

    return new Promise((resolve) => {
      const previouslyFocused = document.activeElement;
      const dialog = document.createElement("dialog");
      dialog.id = "ennoic-confirmation";
      dialog.className = "ennoic-confirmation";
      dialog.setAttribute("aria-labelledby", "ennoic-confirmation-title");
      dialog.setAttribute("aria-describedby", "ennoic-confirmation-message");

      const heading = document.createElement("h2");
      heading.id = "ennoic-confirmation-title";
      heading.textContent = options.title;
      const form = document.createElement("form");
      form.className = "ennoic-confirmation-form";
      const body = document.createElement("div");
      body.className = "ennoic-confirmation-body";
      const message = document.createElement("p");
      message.id = "ennoic-confirmation-message";
      message.textContent = options.message;
      body.append(message);

      let input = null;
      if (options.inputLabel) {
        const label = document.createElement("label");
        label.htmlFor = "ennoic-confirmation-input";
        label.textContent = options.inputLabel;
        input = document.createElement("input");
        input.id = "ennoic-confirmation-input";
        input.name = "confirmation";
        input.type = "text";
        input.inputMode = "email";
        input.autocomplete = "off";
        input.autocapitalize = "none";
        input.spellcheck = false;
        input.maxLength = 254;
        input.required = true;
        body.append(label, input);
      }

      const actions = document.createElement("div");
      actions.className = "ennoic-confirmation-actions";
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.dataset.confirmationCancel = "";
      cancel.textContent = options.cancelLabel;
      const submit = document.createElement("button");
      submit.type = "submit";
      submit.dataset.confirmationAccept = "";
      submit.className = options.danger ? "ennoic-confirmation-danger" : "ennoic-confirmation-primary";
      submit.textContent = options.confirmLabel;
      actions.append(cancel, submit);
      form.append(body, actions);
      dialog.append(heading, form);

      let fallbackBackdrop = null;
      const inertElements = [];
      const wasScrollLocked = document.documentElement.classList.contains("ennoic-confirmation-open");
      document.documentElement.classList.add("ennoic-confirmation-open");
      const viewport = window.visualViewport;
      const positionDialog = () => {
        // Pinch zoom should remain browser-controlled; do not chase zoom panning.
        if (!viewport || (viewport.scale && Math.abs(viewport.scale - 1) > 0.02)) return;
        // The keyboard can shrink the visual viewport without resizing the page.
        dialog.style.top = `${viewport.offsetTop + viewport.height / 2}px`;
        dialog.style.left = `${viewport.offsetLeft + viewport.width / 2}px`;
        dialog.style.maxHeight = `${Math.max(120, viewport.height - 32)}px`;
        if (input && document.activeElement === input) {
          // Resizing after focus can clip the email below a long policy. Scroll
          // only the modal body, leaving the page and action row undisturbed.
          const field = input.getBoundingClientRect();
          const area = body.getBoundingClientRect();
          if (field.bottom > area.bottom) body.scrollTop += field.bottom - area.bottom + 8;
          else if (field.top < area.top) body.scrollTop -= area.top - field.top + 8;
        }
      };
      const finish = (value) => {
        if (activeDialog !== dialog) return;
        activeDialog = null;
        viewport?.removeEventListener("resize", positionDialog);
        viewport?.removeEventListener("scroll", positionDialog);
        window.removeEventListener("resize", positionDialog);
        if (dialog.open && typeof dialog.close === "function") dialog.close();
        dialog.remove();
        fallbackBackdrop?.remove();
        inertElements.forEach(([element, wasInert]) => { element.inert = wasInert; });
        if (!wasScrollLocked) document.documentElement.classList.remove("ennoic-confirmation-open");
        if (previouslyFocused?.isConnected) previouslyFocused.focus({ preventScroll: true });
        resolve(value);
      };

      cancel.addEventListener("click", () => finish(null));
      dialog.addEventListener("cancel", (event) => { event.preventDefault(); finish(null); });
      dialog.addEventListener("close", () => finish(null));
      dialog.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          finish(null);
          return;
        }
        if (event.key !== "Tab") return;
        // Background sidebar/menu shortcuts must not steal focus from a modal.
        event.stopPropagation();
        const controls = input ? [input, cancel, submit] : [cancel, submit];
        const first = controls[0];
        const last = controls.at(-1);
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault(); last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault(); first.focus();
        }
      });
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        if (input && !input.reportValidity()) return;
        finish(input ? input.value : "");
      });

      activeDialog = dialog;
      document.body.append(dialog);
      if (typeof dialog.showModal === "function") {
        dialog.showModal();
      } else {
        // Older browsers still receive an in-page dialog with an inert background.
        fallbackBackdrop = document.createElement("div");
        fallbackBackdrop.className = "ennoic-confirmation-backdrop";
        document.body.append(fallbackBackdrop);
        [...document.body.children].forEach((element) => {
          if (element === dialog || element === fallbackBackdrop) return;
          inertElements.push([element, element.inert]);
          element.inert = true;
        });
        dialog.setAttribute("open", "");
        dialog.setAttribute("role", "dialog");
        dialog.setAttribute("aria-modal", "true");
      }
      positionDialog();
      viewport?.addEventListener("resize", positionDialog, { passive: true });
      viewport?.addEventListener("scroll", positionDialog, { passive: true });
      window.addEventListener("resize", positionDialog, { passive: true });
      input?.addEventListener("focus", positionDialog);
      // Make Cancel the safe default, including for Enter immediately after opening.
      cancel.focus({ preventScroll: true });
    });
  }

  window.EnnoicConfirmation = {
    confirm: (options) => show(options).then((value) => value !== null),
    prompt: (options) => show(options),
  };
})();
