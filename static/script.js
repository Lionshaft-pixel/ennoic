/* yes, I vibecoded the frontend */

"use strict";

const API_URL = "/api/chat";
const STORAGE_KEY = "ennoic-mind:conversations";
const ACTIVE_ID_KEY = "ennoic-mind:active-conversation";
const DEMO = new URLSearchParams(location.search).has("demo");
const MAX_HISTORY_MESSAGES = 12;
const MAX_HISTORY_MESSAGE_CHARS = 4000;

const $ = (id) => document.getElementById(id);
const els = {
  app: $("app"), sidebar: $("sidebar"), backdrop: $("backdrop"), toggle: $("toggle-sidebar"),
  newChat: $("new-chat"), search: $("search"), list: $("history-list"), listEmpty: $("history-empty"),
  title: $("topbar-title"), thread: $("thread"), empty: $("empty-state"), messages: $("messages"),
  form: $("composer"), input: $("composer-input"), send: $("send"),
  settings: $("settings"), openSettings: $("open-settings"), clearAll: $("clear-all"),
};

let conversations = loadConversations();
let activeId = loadActiveConversationId(); // null means "new conversation, nothing sent yet"
let loading = false;
const mobile = window.matchMedia("(max-width: 768px)");

function loadConversations() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || []; } catch { return []; }
}
function saveConversations() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations));
    if (activeId && conversations.some((convo) => convo.id === activeId)) {
      localStorage.setItem(ACTIVE_ID_KEY, activeId);
    } else {
      localStorage.setItem(ACTIVE_ID_KEY, "");
    }
  } catch { /* storage unavailable */ }
}
function loadActiveConversationId() {
  try {
    const savedId = localStorage.getItem(ACTIVE_ID_KEY);
    // Older installs saved conversations but not the selected conversation.
    if (savedId === null) return conversations.at(-1)?.id ?? null;
    return conversations.some((convo) => convo.id === savedId) ? savedId : null;
  } catch { return null; }
}
const activeConversation = () => conversations.find((c) => c.id === activeId);

function setSidebar(open) {
  els.app.dataset.sidebar = open ? "open" : "closed";
  els.toggle.setAttribute("aria-expanded", String(open));
  els.sidebar.inert = !open; // hidden sidebar can't be tabbed into
}
const sidebarIsOpen = () => els.app.dataset.sidebar === "open";

els.toggle.addEventListener("click", () => setSidebar(!sidebarIsOpen()));
els.backdrop.addEventListener("click", () => setSidebar(false));
mobile.addEventListener("change", (e) => setSidebar(!e.matches));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && mobile.matches && sidebarIsOpen()) { setSidebar(false); els.toggle.focus(); }
});

/* ---------- Rendering ---------- */
function render() { renderHistory(); renderThread(); }

function renderHistory() {
  const query = els.search.value.trim().toLowerCase();
  const shown = conversations.filter((c) => c.title.toLowerCase().includes(query));
  els.list.replaceChildren(...shown.map((c) => {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "history-item";
    btn.textContent = c.title;
    if (c.id === activeId) btn.setAttribute("aria-current", "true");
    btn.addEventListener("click", () => { activeId = c.id; saveConversations(); render(); if (mobile.matches) setSidebar(false); });
    li.append(btn);
    return li;
  }));
  els.listEmpty.textContent = conversations.length ? "No matches." : "Your conversations will appear here.";
  els.listEmpty.hidden = shown.length > 0;
}

function renderThread() {
  const convo = activeConversation();
  els.empty.hidden = Boolean(convo);
  els.title.textContent = convo ? convo.title : "";
  els.messages.replaceChildren(...(convo ? convo.messages.map(messageEl) : []));
  els.thread.scrollTo({ top: els.thread.scrollHeight });
}

function messageEl({ role, text, truncated = false }) {
  const div = document.createElement("div");
  div.className = `message ${role}`;
  if (role === "user") div.textContent = text;
  else {
    div.append(formatReply(text));
    if (truncated) {
      const notice = document.createElement("p");
      notice.className = "truncation-notice";
      notice.setAttribute("role", "status");
      notice.textContent = "This reply reached the model's output limit and may be incomplete. Ask for a shorter answer or request one section at a time.";
      div.append(notice);
    }
  }
  return div;
}

// Parse Markdown, then sanitize the resulting HTML before it enters the document.
function formatReply(text) {
  if (!window.marked || !window.DOMPurify) {
    const fallback = document.createElement("p");
    fallback.className = "plain-reply";
    fallback.textContent = text;
    return fallback;
  }

  const html = window.marked.parse(text, { gfm: true });
  const fragment = window.DOMPurify.sanitize(html, {
    USE_PROFILES: { html: true },
    ALLOW_DATA_ATTR: false,
    FORBID_TAGS: ["style"],
    FORBID_ATTR: ["style"],
    RETURN_DOM_FRAGMENT: true,
  });

  fragment.querySelectorAll("pre > code").forEach((code) => {
    const pre = code.parentElement;
    const block = document.createElement("div");
    block.className = "code-block";

    const header = document.createElement("div");
    header.className = "code-header";
    const language = code.className.match(/(?:^|\s)language-([\w.+#-]+)/)?.[1] || "Code";
    const label = document.createElement("span");
    label.className = "code-language";
    label.textContent = language;

    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "copy-code";
    copy.textContent = "Copy";
    copy.setAttribute("aria-label", `Copy ${language} code`);
    copy.addEventListener("click", async () => {
      try {
        if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
        await navigator.clipboard.writeText(code.textContent);
        copy.textContent = "Copied";
      } catch {
        copy.textContent = "Copy failed";
      }
      window.setTimeout(() => { copy.textContent = "Copy"; }, 1800);
    });

    header.append(label, copy);
    pre.replaceWith(block);
    block.append(header, pre);
  });

  return fragment;
}

function loadingEl() {
  const div = document.createElement("div");
  div.className = "message assistant";
  div.innerHTML = '<span class="dots" aria-hidden="true"><i></i><i></i><i></i></span><span class="sr-only">Ennoic Mind is thinking</span>';
  return div;
}

function errorEl(convo, err) {
  const detail = err instanceof TypeError ? "Couldn't reach the server. Check that the backend is running." : err.message;
  const div = document.createElement("div");
  div.className = "message error";
  div.setAttribute("role", "alert");
  const p = document.createElement("p");
  p.textContent = `Couldn't get a response. ${detail}`;
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "retry";
  retry.textContent = "Try again";
  retry.addEventListener("click", () => { div.remove(); getReply(convo); });
  div.append(p, retry);
  return div;
}

/* ---------- Sending ---------- */
async function submitThought(text) {
  let convo = activeConversation();
  if (!convo) {
    const title = text.length > 40 ? text.slice(0, 40).trim() + "…" : text;
    convo = { id: String(Date.now()), title, messages: [] };
    conversations.unshift(convo);
    activeId = convo.id;
  }
  convo.messages.push({ role: "user", text });
  saveConversations();
  render();
  await getReply(convo);
}

async function getReply(convo) {
  setLoading(true);
  const pending = loadingEl();
  els.messages.append(pending);
  els.thread.scrollTo({ top: els.thread.scrollHeight });
  try {
    const result = await requestReply(convo);
    convo.messages.push({ role: "assistant", text: result.reply, truncated: result.truncated });
    saveConversations();
    if (convo.id === activeId) renderThread();
  } catch (err) {
    pending.replaceWith(errorEl(convo, err));
  } finally {
    setLoading(false);
  }
}

async function requestReply(convo) {
  if (DEMO) {
    await new Promise((resolve) => setTimeout(resolve, 900));
    return {
      reply: "## Demo reply\n\nThe backend isn't connected. This placeholder only tests the layout.\n\n- Nothing was sent to a server\n- Your message is saved in this browser only",
      truncated: false,
    };
  }
  const message = convo.messages[convo.messages.length - 1].text;
  const history = convo.messages
    .slice(0, -1)
    .filter((item) => (item.role === "user" || item.role === "assistant") && typeof item.text === "string")
    .slice(-MAX_HISTORY_MESSAGES)
    .map(({ role, text }) => ({
      role,
      text: role === "assistant"
        ? text.slice(-MAX_HISTORY_MESSAGE_CHARS)
        : text.slice(0, MAX_HISTORY_MESSAGE_CHARS),
    }));
  const res = await fetch(API_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ conversation_id: convo.id, message, history }),
  });
  let data;
  try {
    data = await res.json();
  } catch {
    throw new Error("The server returned an unreadable response.");
  }
  if (!res.ok) {
    throw new Error(typeof data.error === "string" ? data.error : `The server returned status ${res.status}.`);
  }
  if (typeof data.reply !== "string") throw new Error('The response had no "reply" text.');
  return { reply: data.reply, truncated: data.truncated === true };
}

function setLoading(value) {
  loading = value;
  els.form.setAttribute("aria-busy", String(value));
  updateSendButton();
}
function updateSendButton() { els.send.disabled = loading || !els.input.value.trim(); }

/* ---------- Composer ---------- */
function autoGrow() {
  els.input.style.height = "auto";
  els.input.style.height = Math.min(els.input.scrollHeight, 200) + "px";
}

els.input.addEventListener("input", () => { autoGrow(); updateSendButton(); });
els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); els.form.requestSubmit(); }
});
els.form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = els.input.value.trim();
  if (!text || loading) return;
  els.input.value = "";
  autoGrow();
  updateSendButton();
  submitThought(text);
});

/* ---------- Other controls ---------- */
els.newChat.addEventListener("click", () => {
  activeId = null;
  saveConversations();
  render();
  els.input.focus();
  if (mobile.matches) setSidebar(false);
});
els.search.addEventListener("input", renderHistory);
els.openSettings.addEventListener("click", () => els.settings.showModal());
els.clearAll.addEventListener("click", () => {
  if (!confirm("Delete all conversations from this browser?")) return;
  conversations = [];
  activeId = null;
  saveConversations();
  render();
  els.settings.close();
});

/* ---------- Start ---------- */
setSidebar(!mobile.matches);
render();
updateSendButton();
if (!mobile.matches) els.input.focus();
