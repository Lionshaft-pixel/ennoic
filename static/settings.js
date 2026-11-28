"use strict";

(() => {
  const app = document.getElementById("settings-app");
  if (!app || !window.EnnoicI18n) return;
  const $ = (id) => document.getElementById(id);
  const navItems = [...document.querySelectorAll(".settings-nav-item")];
  const panels = [...document.querySelectorAll("[data-settings-panel]")];
  const languageKey = `ennoic-mind:language:${app.dataset.userId}`;
  const fallbackLanguageKey = "ennoic-mind:language";
  let language = "en";
  try {
    const stored = localStorage.getItem(languageKey) ?? localStorage.getItem(fallbackLanguageKey);
    if (EnnoicI18n.supportedLanguages.includes(stored)) language = stored;
  } catch { /* Language preference is optional. */ }
  const t = (key, values) => EnnoicI18n.translate(language, key, values);
  const csrfToken = () => document.querySelector('meta[name="csrf-token"]').content;

  function setLanguage(next) {
    if (!EnnoicI18n.supportedLanguages.includes(next)) return;
    try {
      localStorage.setItem(languageKey, next);
      localStorage.setItem(fallbackLanguageKey, next);
    } catch {
      $("general-feedback").textContent = t("languageSaveFailed");
      $("settings-language").value = language;
      return;
    }
    language = next;
    document.documentElement.lang = language;
    EnnoicI18n.applyTranslations(language);
    $("settings-language").value = language;
    updateActiveSectionTitle();
  }

  function updateActiveSectionTitle() {
    const active = navItems.find((item) => item.getAttribute("aria-current") === "page");
    if (active) $("settings-page-title").textContent = active.querySelector("span").textContent;
  }

  function selectSection(section) {
    const item = navItems.find((entry) => entry.dataset.section === section);
    if (!item) return;
    navItems.forEach((entry) => entry.setAttribute("aria-current", String(entry === item)));
    panels.forEach((panel) => { panel.hidden = panel.dataset.settingsPanel !== section; });
    updateActiveSectionTitle();
    if (section === "account") loadPlan();
  }

  window.EnnoicAppBack = {
    closePanel() {
      const general = panels.find((panel) => panel.dataset.settingsPanel === "general");
      if (!general || !general.hidden) return false;
      selectSection("general");
      navItems.find((item) => item.dataset.section === "general")?.focus();
      return true;
    },
  };

  let planRequestId = 0;
  async function loadPlan() {
    const requestId = ++planRequestId;
    const status = $("account-plan-status");
    const loading = $("account-entitlements-loading");
    const errorMessage = $("account-entitlements-error");
    const retry = $("account-entitlements-retry");
    const upgradePrompt = $("account-upgrade-prompt");
    upgradePrompt.hidden = true;
    loading.hidden = false;
    errorMessage.hidden = true;
    retry.hidden = true;
    try {
      const response = await fetch("/api/entitlements", { credentials: "same-origin" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || t("planLoadFailed"));
      if (!data || (data.plan !== "free" && data.plan !== "pro")) throw new Error(t("planLoadFailed"));
      if (requestId !== planRequestId) return;
      const pro = data.plan === "pro";
      $("account-plan-name").dataset.i18n = pro ? "ennoicPro" : "freePlan";
      $("account-plan-name").textContent = t(pro ? "ennoicPro" : "freePlan");
      $("account-plan-separator").hidden = !pro;
      const statusKey = data.status === "active" ? "subscriptionActive"
        : data.status === "canceled" ? "subscriptionCanceled"
          : pro && data.status === "past_due" ? "subscriptionPastDue" : "subscriptionFree";
      status.dataset.i18n = statusKey;
      status.textContent = t(statusKey);
      const quota = data.usage.ai_requests;
      $("manage-paddle-subscription").hidden = data.provider !== "paddle";
      const percent = quota.limit > 0
        ? Math.min(100, (quota.used / quota.limit) * 100)
        : 100;
      const shownPercent = new Intl.NumberFormat(language, { maximumFractionDigits: 1 }).format(percent);
      $("account-ai-percent").textContent = `${shownPercent}%`;
      $("account-ai-progress").setAttribute("aria-valuenow", String(percent));
      $("account-ai-progress-fill").style.width = `${percent}%`;
      const resetDate = new Date(quota.resets_at);
      const resetLabel = Number.isNaN(resetDate.valueOf())
        ? ""
        : new Intl.DateTimeFormat(language, { dateStyle: "medium" }).format(resetDate);
      $("account-ai-reset").textContent = resetLabel ? t("usageResetsAt", { date: resetLabel }) : "";
      upgradePrompt.hidden = data.plan !== "free";
      loading.hidden = true;
    } catch (error) {
      if (requestId !== planRequestId) return;
      loading.hidden = true;
      errorMessage.textContent = error.message || t("planLoadFailed");
      errorMessage.hidden = false;
      retry.hidden = false;
    }
  }

  $("account-entitlements-retry").addEventListener("click", loadPlan);

  async function postJSON(url, body) {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      body: JSON.stringify(body),
    });
    let data;
    try { data = await response.json(); } catch { throw new Error(t("unreadableResponse")); }
    if (!response.ok) throw new Error(data.error || t("accountSaveFailed"));
    if (data.csrf_token) document.querySelector('meta[name="csrf-token"]').content = data.csrf_token;
    return data;
  }

  $("manage-paddle-subscription").addEventListener("click", async () => {
    const button = $("manage-paddle-subscription");
    button.disabled = true;
    try {
      const data = await postJSON("/api/paddle/portal", {});
      window.location.assign(data.url);
    } catch (error) {
      $("account-feedback").textContent = error.message || t("accountSaveFailed");
    } finally {
      button.disabled = false;
    }
  });

  navItems.forEach((item) => item.addEventListener("click", () => selectSection(item.dataset.section)));
  $("settings-language").value = language;
  document.documentElement.lang = language;
  EnnoicI18n.applyTranslations(language);
  $("settings-language").addEventListener("change", (event) => setLanguage(event.target.value));
  $("settings-sidebar-toggle").addEventListener("click", (event) => {
    const collapsed = app.classList.toggle("sidebar-collapsed");
    event.currentTarget.setAttribute("aria-expanded", String(!collapsed));
    event.currentTarget.setAttribute("aria-label", t(collapsed ? "expandSettingsSidebar" : "collapseSettingsSidebar"));
    event.currentTarget.title = t(collapsed ? "expandSettingsSidebar" : "collapseSettingsSidebar");
  });
  $("settings-search").addEventListener("input", (event) => {
    const query = event.target.value.trim().toLocaleLowerCase(language);
    let matches = 0;
    navItems.forEach((item) => {
      const visible = !query || item.textContent.trim().toLocaleLowerCase(language).includes(query);
      item.hidden = !visible;
      if (visible) matches += 1;
    });
    $("settings-no-matches").hidden = matches !== 0;
  });

  $("password-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = $("password-submit");
    const feedback = $("password-feedback");
    const values = new FormData(form);
    button.disabled = true;
    feedback.textContent = "";
    try {
      await postJSON("/api/account/password", {
        current_password: values.get("current_password"),
        new_password: values.get("new_password"),
      });
      form.reset();
      feedback.textContent = t("passwordChanged");
    } catch (error) {
      feedback.textContent = error.message || t("accountSaveFailed");
    } finally { button.disabled = false; }
  });

  $("delete-account").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const billingMessage = app.dataset.accountHasBilling === "true"
      ? (app.dataset.accountPlan === "free"
        ? t("deleteAccountPastSubscriptionConfirm")
        : t("deleteAccountProConfirm"))
      : "";
    const typedEmail = await window.EnnoicConfirmation.prompt({
      title: t("deleteAccount"),
      message: [t("deleteAccountConfirm"), billingMessage].filter(Boolean).join("\n\n"),
      inputLabel: t("deleteAccountPrompt", { email: app.dataset.userEmail }),
      confirmLabel: t("deleteAccount"), cancelLabel: t("cancel"), danger: true,
    });
    if (typedEmail === null) return;
    const feedback = $("account-feedback");
    button.disabled = true;
    feedback.textContent = "";
    try {
      const response = await fetch("/api/account", {
        method: "DELETE", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
        body: JSON.stringify({ email: typedEmail }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || t("deleteAccountFailed"));
      for (const key of [
        `ennoic-mind:conversations:${app.dataset.userId}`,
        `ennoic-mind:active-conversation:${app.dataset.userId}`,
        languageKey,
        fallbackLanguageKey,
      ]) {
        try { localStorage.removeItem(key); } catch { /* Server deletion is already scheduled. */ }
      }
      window.location.assign("/login?deletion=scheduled");
    } catch (error) {
      feedback.textContent = error.message || t("deleteAccountFailed");
      button.disabled = false;
    }
  });

  let signOutApproved = false;
  $("settings-sign-out-form").addEventListener("submit", async (event) => {
    if (signOutApproved) return;
    event.preventDefault();
    const form = event.currentTarget;
    const submitter = event.submitter;
    if (!await window.EnnoicConfirmation.confirm({
      title: t("signOut"), message: t("signOutConfirm"),
      confirmLabel: t("signOut"), cancelLabel: t("cancel"),
    })) return;
    signOutApproved = true;
    // Resubmit the existing POST form, retaining its CSRF field and browser validation.
    try {
      if (submitter) form.requestSubmit(submitter);
      else form.requestSubmit();
    } finally {
      // A canceled navigation or BFCache return must still ask next time.
      signOutApproved = false;
    }
  });
})();
