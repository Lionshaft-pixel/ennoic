/* ==========================================================
   Ennoic Mind - frontend behavior (no framework)

   Backend contract (to be built on the Python side):
     POST /api/chat
     Request:  { "conversation_id": "123", "message": "my brain dump" }
     Response: { "reply": "## Heading\n\nParagraph\n\n- item\n- item" }
   Separate blocks with blank lines. "## " starts a heading and
   lines starting with "- " make a list.

   Add ?demo to the page URL to see placeholder replies while
   the backend does not exist yet.
   ========================================================== */
"use strict";

const API_URL = "/api/chat";
const STORAGE_KEY = "ennoic-mind:conversations";
const DEMO = new URLSearchParams(location.search).has("demo");

const $ = (id) => document.getElementById(id);
const els = {
  app: $("app"), sidebar: $("sidebar"), backdrop: $("backdrop"), toggle: $("toggle-sidebar"),
  newChat: $("new-chat"), search: $("search"), list: $("history-list"), listEmpty: $("history-empty"),
  title: $("topbar-title"), thread: $("thread"), empty: $("empty-state"), messages: $("messages"),
  form: $("composer"), input: $("composer-input"), send: $("send"),
  settings: $("settings"), openSettings: $("open-settings"), clearAll: $("clear-all"),
};

// State: conversations look like { id, title, messages: [{ role, text }] }
let conversations = loadConversations();
let activeId = null; // null means "new conversation, nothing sent yet"
let loading = false;
const mobile = window.matchMedia("(max-width: 768px)");

/* ---------- Storage ---------- */
function loadConversations() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || []; } catch { return []; }
}
function saveConversations() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations)); } catch { /* storage unavailable */ }
}
const activeConversation = () => conversations.find((c) => c.id === activeId);

/* ---------- Sidebar ---------- */
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
    btn.addEventListener("click", () => { activeId = c.id; render(); if (mobile.matches) setSidebar(false); });
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

function messageEl({ role, text }) {
  const div = document.createElement("div");
  div.className = `message ${role}`;
  if (role === "user") div.textContent = text;
  else div.append(...formatReply(text));
  return div;
}

// Turns plain reply text into headings, lists and paragraphs (textContent only, so it is safe).
function formatReply(text) {
  return text.trim().split(/\n{2,}/).map((block) => {
    const lines = block.split("\n");
    if (lines.every((l) => /^[-*] /.test(l))) {
      const ul = document.createElement("ul");
      lines.forEach((l) => { const li = document.createElement("li"); li.textContent = l.slice(2); ul.append(li); });
      return ul;
    }
    const isHeading = /^#{1,3} /.test(block);
    const el = document.createElement(isHeading ? "h3" : "p");
    el.textContent = isHeading ? block.replace(/^#{1,3} /, "") : block;
    return el;
  });
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
    const reply = await requestReply(convo);
    convo.messages.push({ role: "assistant", text: reply });
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
    return "## Demo reply\n\nThe backend isn't connected. This placeholder only tests the layout.\n\n- Nothing was sent to a server\n- Your message is saved in this browser only";
  }
  const message = convo.messages[convo.messages.length - 1].text;
  const res = await fetch(API_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ conversation_id: convo.id, message }),
  });
  if (!res.ok) throw new Error(`The server returned status ${res.status}.`);
  const data = await res.json();
  if (typeof data.reply !== "string") throw new Error('The response had no "reply" text.');
  return data.reply;
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
