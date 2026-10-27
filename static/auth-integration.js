"use strict";

const integrationScript = document.currentScript;
const authContext = JSON.parse(decodeURIComponent(integrationScript.dataset.authContext));
const card = document.querySelector('section[aria-label="Account access"]');
const csrfToken = authContext.csrfToken;

function addMessage(container, message, isError) {
  if (!message) return;
  const notice = document.createElement("p");
  notice.setAttribute("role", isError ? "alert" : "status");
  notice.dataset.authMessage = isError ? "error" : "notice";
  notice.textContent = message;
  notice.style.cssText = [
    "margin: 0 0 16px",
    "padding: 11px 14px",
    "border: 1px solid " + (isError ? "rgba(255, 128, 118, 0.5)" : "rgba(255, 206, 160, 0.4)"),
    "border-radius: 12px",
    "background: " + (isError ? "rgba(100, 22, 18, 0.45)" : "rgba(255, 206, 160, 0.1)"),
    "color: #fff",
    "font-size: 14px",
    "line-height: 1.45",
  ].join(";");
  container.insertBefore(notice, container.firstChild);
}

function postForm(action, values) {
  const form = document.createElement("form");
  form.method = "post";
  form.action = action;
  form.hidden = true;
  for (const [name, value] of Object.entries({ csrf_token: csrfToken, ...values })) {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value;
    form.append(input);
  }
  document.body.append(form);
  HTMLFormElement.prototype.submit.call(form);
}

function closeModal(overlay) {
  overlay.remove();
}

function openEmailModal({ title, action, submitLabel }) {
  const overlay = document.createElement("div");
  overlay.style.cssText = "position:fixed;inset:0;z-index:10000;display:grid;place-items:center;padding:20px;background:rgba(0,0,0,.72)";
  const dialog = document.createElement("section");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-label", title);
  dialog.style.cssText = "width:min(100%,420px);padding:28px;border:1px solid rgba(255,190,120,.35);border-radius:24px;background:#180803;color:#fff;box-shadow:0 30px 80px rgba(0,0,0,.55)";

  const heading = document.createElement("h2");
  heading.textContent = title;
  heading.style.cssText = "margin:0 0 18px;font:italic 400 36px/1.1 'Instrument Serif',Georgia,serif";

  const form = document.createElement("form");
  form.method = "post";
  form.action = action;
  form.style.cssText = "display:flex;flex-direction:column;gap:12px";
  const token = document.createElement("input");
  token.type = "hidden";
  token.name = "csrf_token";
  token.value = csrfToken;
  const label = document.createElement("label");
  label.htmlFor = "auth-email-action";
  label.textContent = "Email";
  label.style.cssText = "font-size:13px;font-weight:600;color:rgba(255,238,226,.92)";
  const email = document.createElement("input");
  email.id = "auth-email-action";
  email.name = "email";
  email.type = "email";
  email.autocomplete = "email";
  email.maxLength = 254;
  email.required = true;
  email.style.cssText = "height:48px;box-sizing:border-box;padding:0 16px;border-radius:14px;border:1px solid rgba(255,206,160,.3);background:rgba(8,3,1,.55);color:#fff;font-family:inherit;font-size:15px";
  const controls = document.createElement("div");
  controls.style.cssText = "display:flex;gap:10px;margin-top:6px";
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.textContent = submitLabel;
  submit.style.cssText = "flex:1;height:48px;border:0;border-radius:999px;background:#f6e73b;color:#1a1200;font-weight:700";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.textContent = "Cancel";
  cancel.style.cssText = "height:48px;padding:0 18px;border:1px solid rgba(255,206,160,.4);border-radius:999px;background:transparent;color:#fff;font-weight:600";
  cancel.addEventListener("click", () => closeModal(overlay));
  controls.append(submit, cancel);
  form.append(token, label, email, controls);
  dialog.append(heading, form);
  overlay.append(dialog);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) closeModal(overlay);
  });
  document.body.append(overlay);
  email.focus();
}

function showResetForm() {
  if (!card || !authContext.resetToken) return;
  card.replaceChildren();
  const heading = document.createElement("h2");
  heading.textContent = "Choose a new password";
  heading.style.cssText = "margin:0 0 20px;font:italic 400 38px/1.08 'Instrument Serif',Georgia,serif;color:#fff";
  const form = document.createElement("form");
  form.method = "post";
  form.action = "/auth/password-reset/" + encodeURIComponent(authContext.resetToken);
  form.style.cssText = "display:flex;flex-direction:column;gap:12px";
  const token = document.createElement("input");
  token.type = "hidden";
  token.name = "csrf_token";
  token.value = csrfToken;
  const label = document.createElement("label");
  label.htmlFor = "new-auth-password";
  label.textContent = "New password";
  label.style.cssText = "font-size:13px;font-weight:600;color:rgba(255,238,226,.92)";
  const password = document.createElement("input");
  password.id = "new-auth-password";
  password.name = "password";
  password.type = "password";
  password.autocomplete = "new-password";
  password.minLength = 12;
  password.maxLength = 128;
  password.required = true;
  password.placeholder = "Use 12 to 128 characters";
  password.style.cssText = "height:48px;box-sizing:border-box;padding:0 16px;border-radius:14px;border:1px solid rgba(255,206,160,.3);background:rgba(8,3,1,.55);color:#fff;font-family:inherit;font-size:15px";
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.textContent = "Change password";
  submit.style.cssText = "height:52px;margin-top:8px;border:0;border-radius:999px;background:#f6e73b;color:#1a1200;font-size:16px;font-weight:700";
  form.append(token, label, password, submit);
  card.append(heading, form);
  addMessage(card, authContext.error, true);
  addMessage(card, authContext.notice, false);
  password.focus();
}

if (card && authContext.resetToken) {
  showResetForm();
} else if (card) {
  addMessage(card, authContext.error, true);
  addMessage(card, authContext.notice, false);

  function ensureAuthMessages() {
    if (authContext.error && !card.querySelector('[data-auth-message="error"]')) {
      addMessage(card, authContext.error, true);
    }
    if (authContext.notice && !card.querySelector('[data-auth-message="notice"]')) {
      addMessage(card, authContext.notice, false);
    }
  }

  const linkObserver = new MutationObserver(() => {
    ensureAuthMessages();
  });
  linkObserver.observe(card, { childList: true, subtree: true });
  ensureAuthMessages();

  document.addEventListener("click", (event) => {
    const link = event.target instanceof Element ? event.target.closest("a") : null;
    if (!link || !card.contains(link)) return;
    const label = link.textContent.trim();
    if (label === "Forgot password?") {
      event.preventDefault();
      event.stopImmediatePropagation();
      openEmailModal({
        title: "Reset your password",
        action: "/auth/password-reset",
        submitLabel: "Send reset link",
      });
    }
  }, true);

  document.addEventListener("submit", (event) => {
    const visibleForm = event.target;
    if (
      !(visibleForm instanceof HTMLFormElement) ||
      !card.contains(visibleForm) ||
      !visibleForm.querySelector("#email") ||
      !visibleForm.querySelector("#password")
    ) return;
    event.preventDefault();
    event.stopImmediatePropagation();

    const email = visibleForm.querySelector("#email");
    const password = visibleForm.querySelector("#password");
    if (!email || !password || !email.value || !email.reportValidity()) return;
    if (!password.value || password.value.length > 128) {
      password.setCustomValidity("Enter a password using no more than 128 characters.");
      password.reportValidity();
      password.addEventListener("input", () => password.setCustomValidity(""), { once: true });
      return;
    }
    postForm("/auth/continue", {
      email: email.value,
      password: password.value,
    });
  }, true);
}
