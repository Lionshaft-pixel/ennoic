"use strict";

// Email progresses locally without an account-existence lookup. The existing
// server flow still handles sign-in, signup, email verification, and CSRF.
const emailForm = document.querySelector("[data-welcome-email]");
if (emailForm) {
  const email = emailForm.querySelector("#email");
  const password = emailForm.querySelector("#password");
  const passwordSection = emailForm.querySelector("[data-welcome-password]");
  const submit = emailForm.querySelector("[data-email-continue]");
  if (!email.value && !document.querySelector('[role="alert"], [role="status"]')) {
    passwordSection.hidden = true;
    password.disabled = true;
    submit.textContent = "Continue with email";
  }
  emailForm.addEventListener("submit", (event) => {
    if (passwordSection.hidden) {
      event.preventDefault();
      passwordSection.hidden = false;
      password.disabled = false;
      submit.textContent = "Continue";
      password.focus();
      return;
    }
    if (emailForm.checkValidity()) {
      submit.disabled = true;
      submit.textContent = "Continuing…";
    }
  });
  const toggle = emailForm.querySelector("[data-password-toggle]");
  toggle?.addEventListener("click", () => {
    const show = password.type === "password";
    password.type = show ? "text" : "password";
    toggle.textContent = show ? "Hide" : "Show";
    toggle.setAttribute("aria-label", show ? "Hide password" : "Show password");
  });
  // Back/forward cache restores a usable form after returning from navigation.
  window.addEventListener("pageshow", () => {
    submit.disabled = false;
    submit.textContent = passwordSection.hidden ? "Continue with email" : "Continue";
  });
}
