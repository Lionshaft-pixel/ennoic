"use strict";

let language = "en";
try {
  const savedLanguage = localStorage.getItem("ennoic-mind:language");
  if (EnnoicI18n.supportedLanguages.includes(savedLanguage)) language = savedLanguage;
} catch { /* The default interface language remains available without storage. */ }
EnnoicI18n.applyTranslations(language);

document.querySelectorAll("[data-auth-tab]").forEach((tab) => {
  tab.addEventListener("click", () => {
    const selected = tab.dataset.authTab;
    document.querySelectorAll("[data-auth-tab]").forEach((item) => {
      item.setAttribute("aria-selected", String(item === tab));
    });
    document.querySelectorAll("[data-auth-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.authPanel !== selected;
    });
    document.querySelector(`[data-auth-panel="${selected}"] input:not([type="hidden"])`)?.focus();
  });
});
