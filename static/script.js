/* yes, I vibecoded the frontend */

"use strict";

const API_URL = "/api/chat";
const TASKS_URL = "/api/tasks";
const USER_ID = document.getElementById("app").dataset.userId;
const STORAGE_KEY = `ennoic-mind:conversations:${USER_ID}`;
const ACTIVE_ID_KEY = `ennoic-mind:active-conversation:${USER_ID}`;
const LANGUAGE_KEY = `ennoic-mind:language:${USER_ID}`;
const PANEL_SIZE_KEY = "ennoic-mind:panel-size";
const DEMO = new URLSearchParams(location.search).has("demo");
const TASK_PANEL_KEY = "ennoic-mind:task-panel";

const $ = (id) => document.getElementById(id);
const els = {
  app: $("app"), sidebar: $("sidebar"), toggle: $("toggle-sidebar"),
  taskDashboard: $("task-dashboard"), collapseTasks: $("collapse-tasks"), restoreTasks: $("restore-tasks"),
  panelDivider: $("panel-divider"), workspaceGrid: $("workspace-grid"),
  newChat: $("new-chat"), search: $("search"), list: $("history-list"), listEmpty: $("history-empty"),
  historyStatus: $("history-status"),
  thread: $("thread"), stage: $("stage"), greeting: $("greeting"), empty: $("empty-state"), messages: $("messages"),
  form: $("composer"), input: $("composer-input"), send: $("send"),
  openSettings: $("open-settings"),
  taskFilters: $("task-filters"), taskSearch: $("task-search"), taskList: $("task-list"),
  taskStatus: $("task-status"), createTask: $("create-task"), taskEditor: $("task-editor"),
  taskEditorForm: $("task-editor-form"), taskEditorTitle: $("task-editor-title"),
  taskTitle: $("task-title-input"), taskDescription: $("task-description-input"),
  taskProject: $("task-project-input"), taskDeadline: $("task-deadline-input"),
  taskPriority: $("task-priority-input"), taskFormError: $("task-form-error"),
  cancelTask: $("cancel-task"), saveTask: $("save-task"),
};

let conversations = loadConversations();
let activeId = loadActiveConversationId(); // null means "new conversation, nothing sent yet"
let language = loadLanguagePreference();
let panelSizes = loadPanelSizes();
let loading = false;
let taskView = "today";
let editingTaskId = null;
let taskSearchTimer = null;
const mobile = window.matchMedia("(max-width: 900px)");
const stackedPanels = window.matchMedia("(max-width: 940px)");
const t = (key, values) => EnnoicI18n.translate(language, key, values);
let hasMoreConversationHistory = false;
let conversationHistoryLimit = null;

function loadConversations() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
    return Array.isArray(saved)
      ? saved.filter((conversation) => conversation && typeof conversation.id === "string"
        && typeof conversation.title === "string" && Array.isArray(conversation.messages))
      : [];
  } catch { return []; }
}
function saveConversations() {
  let previousConversations;
  let previousActiveId;
  let snapshotRead = false;
  try {
    previousConversations = localStorage.getItem(STORAGE_KEY);
    previousActiveId = localStorage.getItem(ACTIVE_ID_KEY);
    snapshotRead = true;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations));
    if (activeId && conversations.some((convo) => convo.id === activeId)) {
      localStorage.setItem(ACTIVE_ID_KEY, activeId);
    } else {
      localStorage.setItem(ACTIVE_ID_KEY, "");
    }
    return true;
  } catch {
    if (snapshotRead) {
      try {
        if (previousConversations === null) localStorage.removeItem(STORAGE_KEY);
        else localStorage.setItem(STORAGE_KEY, previousConversations);
        if (previousActiveId === null) localStorage.removeItem(ACTIVE_ID_KEY);
        else localStorage.setItem(ACTIVE_ID_KEY, previousActiveId);
      } catch { /* keep the original storage error visible to the caller */ }
    }
    return false;
  }
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

function loadLanguagePreference() {
  try {
    const saved = localStorage.getItem(LANGUAGE_KEY) ?? localStorage.getItem("ennoic-mind:language");
    return EnnoicI18n.supportedLanguages.includes(saved) ? saved : "en";
  } catch { return "en"; }
}

function loadPanelSizes() {
  try {
    const saved = JSON.parse(localStorage.getItem(PANEL_SIZE_KEY));
    return {
      desktop: Number.isFinite(saved?.desktop) ? Math.min(0.65, Math.max(0.3, saved.desktop)) : 0.42,
      mobile: Number.isFinite(saved?.mobile) ? Math.min(0.65, Math.max(0.32, saved.mobile)) : 0.42,
    };
  } catch {
    return { desktop: 0.42, mobile: 0.42 };
  }
}

function loadTaskPanelPreference() {
  try { return localStorage.getItem(TASK_PANEL_KEY) !== "closed"; } catch { return true; }
}

function setSidebar(open) {
  els.app.dataset.sidebar = open ? "open" : "closed";
  els.toggle.setAttribute("aria-expanded", String(open));
  els.toggle.setAttribute("aria-label", t(open ? "collapseSidebar" : "expandSidebar"));
  els.toggle.title = t(open ? "collapseSidebar" : "expandSidebar");
}
const sidebarIsOpen = () => els.app.dataset.sidebar === "open";

els.toggle.addEventListener("click", () => setSidebar(!sidebarIsOpen()));
mobile.addEventListener("change", () => setSidebar(false));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && sidebarIsOpen()) { setSidebar(false); els.toggle.focus(); }
});

function setTaskPanelOpen(open, persist = true) {
  els.app.dataset.taskPanel = open ? "open" : "closed";
  els.collapseTasks.setAttribute("aria-expanded", String(open));
  els.collapseTasks.setAttribute("aria-label", t(open ? "hideTasks" : "showTasks"));
  els.collapseTasks.title = t(open ? "hideTasks" : "showTasks");
  els.restoreTasks.hidden = open;
  els.restoreTasks.setAttribute("aria-expanded", String(open));
  els.taskDashboard.inert = !open;
  els.panelDivider.hidden = !open;
  if (persist) {
    try { localStorage.setItem(TASK_PANEL_KEY, open ? "open" : "closed"); } catch { /* preference is optional */ }
  }
}

function currentPanelAxis() {
  return stackedPanels.matches ? "mobile" : "desktop";
}

function applyPanelSize(size, persist = false) {
  const axis = currentPanelAxis();
  const bounds = axis === "mobile" ? [0.32, 0.65] : [0.3, 0.65];
  const styles = getComputedStyle(els.workspaceGrid);
  const padding = axis === "mobile"
    ? parseFloat(styles.paddingTop) + parseFloat(styles.paddingBottom)
    : parseFloat(styles.paddingLeft) + parseFloat(styles.paddingRight);
  const extent = axis === "mobile" ? els.workspaceGrid.clientHeight : els.workspaceGrid.clientWidth;
  const available = Math.max(1, extent - padding - 30);
  const chatMinimum = axis === "mobile" ? 360 : 320;
  const maximum = Math.min(bounds[1], Math.max(bounds[0], (available - chatMinimum) / available));
  panelSizes[axis] = Math.min(maximum, Math.max(bounds[0], size));
  els.panelDivider.setAttribute("aria-valuemin", String(Math.round(bounds[0] * 100)));
  els.panelDivider.setAttribute("aria-valuemax", String(Math.round(maximum * 100)));
  els.panelDivider.setAttribute("aria-valuenow", String(Math.round(panelSizes[axis] * 100)));
  els.workspaceGrid.style.setProperty(
    axis === "mobile" ? "--task-panel-mobile-size" : "--task-panel-size",
    `${panelSizes[axis] * 100}%`,
  );
  if (persist) {
    try { localStorage.setItem(PANEL_SIZE_KEY, JSON.stringify(panelSizes)); } catch { /* preference is optional */ }
  }
}

function updateDividerOrientation() {
  const orientation = stackedPanels.matches ? "horizontal" : "vertical";
  els.panelDivider.setAttribute("aria-orientation", orientation);
  applyPanelSize(panelSizes[currentPanelAxis()]);
}

let resizePointer = null;
els.panelDivider.addEventListener("pointerdown", (event) => {
  if (els.panelDivider.hidden) return;
  resizePointer = { id: event.pointerId, axis: currentPanelAxis() };
  els.panelDivider.setPointerCapture(event.pointerId);
  event.preventDefault();
});
els.panelDivider.addEventListener("pointermove", (event) => {
  if (!resizePointer || resizePointer.id !== event.pointerId) return;
  const rect = els.workspaceGrid.getBoundingClientRect();
  const styles = getComputedStyle(els.workspaceGrid);
  const isMobile = resizePointer.axis === "mobile";
  const start = isMobile
    ? rect.top + parseFloat(styles.paddingTop)
    : rect.left + parseFloat(styles.paddingLeft);
  const extent = isMobile ? els.workspaceGrid.clientHeight : els.workspaceGrid.clientWidth;
  const padding = isMobile
    ? parseFloat(styles.paddingTop) + parseFloat(styles.paddingBottom)
    : parseFloat(styles.paddingLeft) + parseFloat(styles.paddingRight);
  const available = Math.max(1, extent - padding - 30);
  const coordinate = isMobile ? event.clientY : event.clientX;
  applyPanelSize((coordinate - start - 15) / available);
});
els.panelDivider.addEventListener("pointerup", (event) => {
  if (!resizePointer || resizePointer.id !== event.pointerId) return;
  applyPanelSize(panelSizes[resizePointer.axis], true);
  resizePointer = null;
});
els.panelDivider.addEventListener("pointercancel", () => { resizePointer = null; });
els.panelDivider.addEventListener("keydown", (event) => {
  const axis = currentPanelAxis();
  const increase = axis === "mobile" ? event.key === "ArrowDown" : event.key === "ArrowRight";
  const decrease = axis === "mobile" ? event.key === "ArrowUp" : event.key === "ArrowLeft";
  if (increase || decrease) {
    event.preventDefault();
    applyPanelSize(panelSizes[axis] + (increase ? 0.02 : -0.02), true);
  } else if (event.key === "Home" || event.key === "End") {
    event.preventDefault();
    applyPanelSize(event.key === "Home" ? 0.3 : 0.65, true);
  }
});
els.collapseTasks.addEventListener("click", () => setTaskPanelOpen(false));
els.restoreTasks.addEventListener("click", () => {
  setTaskPanelOpen(true);
  loadTasks();
});
window.addEventListener("resize", updateDividerOrientation);
stackedPanels.addEventListener("change", updateDividerOrientation);

/* ---------- Rendering ---------- */
function render() { renderHistory(); renderThread(); }

function showHistoryStatus(message, isError = false) {
  els.historyStatus.textContent = message;
  els.historyStatus.classList.toggle("error", isError);
  els.historyStatus.hidden = !message;
}

function showHistoryPlanNotice(limit) {
  els.historyStatus.replaceChildren();
  els.historyStatus.classList.remove("error");
  els.historyStatus.append(document.createTextNode(t("historyLimitNotice", { limit })));
  const link = document.createElement("a");
  link.href = "/pricing";
  link.textContent = t("viewPlans");
  els.historyStatus.append(document.createTextNode(" "), link);
  els.historyStatus.hidden = false;
}

function closeConversationMenus(except = null) {
  els.list.querySelectorAll(".conversation-menu").forEach((menu) => {
    if (menu === except) return;
    menu.hidden = true;
    const trigger = menu.parentElement.querySelector(".conversation-menu-trigger");
    trigger?.setAttribute("aria-expanded", "false");
  });
}

function createConversationMenu(conversation) {
  const wrapper = document.createElement("div");
  wrapper.className = "conversation-menu-wrap";

  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "conversation-menu-trigger";
  trigger.textContent = "⋯";
  trigger.setAttribute("aria-label", t("conversationMenu", { title: conversation.title }));
  trigger.setAttribute("aria-haspopup", "menu");
  trigger.setAttribute("aria-expanded", "false");
  trigger.addEventListener("click", () => {
    const open = wrapper.querySelector(".conversation-menu");
    const shouldOpen = open.hidden;
    closeConversationMenus(open);
    open.hidden = !shouldOpen;
    trigger.setAttribute("aria-expanded", String(shouldOpen));
    if (shouldOpen) {
      const triggerRect = trigger.getBoundingClientRect();
      const menuRect = open.getBoundingClientRect();
      const top = triggerRect.bottom + menuRect.height + 8 <= window.innerHeight
        ? triggerRect.bottom + 3
        : Math.max(8, triggerRect.top - menuRect.height - 3);
      const left = Math.min(
        window.innerWidth - menuRect.width - 8,
        Math.max(8, triggerRect.right - menuRect.width),
      );
      open.style.top = `${top}px`;
      open.style.left = `${left}px`;
      open.querySelector("button")?.focus();
    }
  });

  const menu = document.createElement("div");
  menu.className = "conversation-menu";
  menu.setAttribute("role", "menu");
  menu.hidden = true;
  menu.addEventListener("keydown", (event) => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const actions = [...menu.querySelectorAll('[role="menuitem"]')];
    const current = actions.indexOf(document.activeElement);
    const index = event.key === "Home" ? 0
      : event.key === "End" ? actions.length - 1
        : (current + (event.key === "ArrowDown" ? 1 : actions.length - 1)) % actions.length;
    event.preventDefault();
    actions[index]?.focus();
  });
  const menuAction = (key, action, danger = false) => {
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("role", "menuitem");
    button.textContent = t(key);
    if (danger) button.classList.add("danger");
    button.addEventListener("click", () => {
      menu.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
      action();
    });
    menu.append(button);
  };
  menuAction("rename", () => renameConversation(conversation.id));
  menuAction(conversation.pinned ? "unpin" : "pin", () => toggleConversationPin(conversation.id));
  menuAction("delete", () => deleteConversation(conversation.id), true);
  wrapper.append(trigger, menu);
  return wrapper;
}

function renderHistory() {
  const query = els.search.value.trim().toLowerCase();
  const shown = conversations
    .filter((c) => c.title.toLowerCase().includes(query))
    .sort((a, b) => Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)));
  els.list.replaceChildren(...shown.map((c) => {
    const li = document.createElement("li");
    li.className = "history-row";
    li.dataset.conversationId = c.id;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "history-item";
    const title = document.createElement("span");
    title.className = "history-item-title";
    title.textContent = c.title;
    btn.append(title);
    if (c.id === activeId) btn.setAttribute("aria-current", "true");
    btn.addEventListener("click", () => {
      const previousActiveId = activeId;
      activeId = c.id;
      if (!saveConversations()) {
        activeId = previousActiveId;
        showHistoryStatus(t("conversationSaveFailed"), true);
      } else {
        showHistoryStatus("");
      }
      render();
      if (conversations.length) {
        els.list.querySelector(".conversation-menu-trigger")?.focus();
      } else {
        els.newChat.focus();
      }
      if (mobile.matches) setSidebar(false);
    });
    li.append(btn);
    if (c.pinned) {
      const pin = document.createElement("span");
      pin.className = "pinned-indicator";
      pin.textContent = t("pinned");
      btn.append(pin);
    }
    li.append(createConversationMenu(c));
    return li;
  }));
  els.listEmpty.textContent = query ? t("noMatches") : t("conversationsEmpty");
  els.listEmpty.hidden = shown.length > 0;
}

function renameConversation(id) {
  const conversation = conversations.find((item) => item.id === id);
  const row = els.list.querySelector(`[data-conversation-id="${CSS.escape(id)}"]`);
  if (!conversation || !row) return;
  const oldTitle = conversation.title;
  const form = document.createElement("form");
  form.className = "conversation-rename";
  const input = document.createElement("input");
  input.type = "text";
  input.maxLength = 120;
  input.required = true;
  input.value = oldTitle;
  input.setAttribute("aria-label", t("renameConversation"));
  const save = document.createElement("button");
  save.type = "submit";
  save.textContent = t("save");
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.textContent = t("cancel");
  cancel.addEventListener("click", () => {
    renderHistory();
    els.list.querySelector(`[data-conversation-id="${CSS.escape(id)}"] .history-item`)?.focus();
  });
  form.append(input, save, cancel);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const title = input.value.trim();
    if (!title) {
      showHistoryStatus(t("conversationTitleRequired"), true);
      input.focus();
      return;
    }
    const oldTitle = conversation.title;
    conversation.title = title;
    try {
      await requestJSON(`/api/conversations/${encodeURIComponent(id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      });
      saveConversations();
      showHistoryStatus("");
    } catch (err) {
      conversation.title = oldTitle;
      showHistoryStatus(err.message || t("conversationSaveFailed"), true);
    }
    renderHistory();
    els.list.querySelector(`[data-conversation-id="${CSS.escape(id)}"] .history-item`)?.focus();
  });
  row.querySelector(".history-item").replaceWith(form);
  input.focus();
  input.select();
}

async function toggleConversationPin(id) {
  const conversation = conversations.find((item) => item.id === id);
  if (!conversation) return;
  const wasPinned = Boolean(conversation.pinned);
  conversation.pinned = !wasPinned;
  try {
    await requestJSON(`/api/conversations/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pinned: conversation.pinned }),
    });
    saveConversations();
    showHistoryStatus("");
  } catch (err) {
    conversation.pinned = wasPinned;
    showHistoryStatus(err.message || t("conversationSaveFailed"), true);
  }
  renderHistory();
  els.list.querySelector(`[data-conversation-id="${CSS.escape(id)}"] .conversation-menu-trigger`)?.focus();
}

async function deleteConversation(id) {
  const conversation = conversations.find((item) => item.id === id);
  if (!conversation || !window.confirm(t("deleteConversationConfirm", { title: conversation.title }))) return;
  const oldConversations = conversations;
  const oldActiveId = activeId;
  conversations = conversations.filter((item) => item.id !== id);
  if (activeId === id) activeId = null;
  try {
    await requestJSON(`/api/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
    saveConversations();
    showHistoryStatus("");
  } catch (err) {
    conversations = oldConversations;
    activeId = oldActiveId;
    showHistoryStatus(err.message || t("conversationSaveFailed"), true);
  }
  render();
}

document.addEventListener("click", (event) => {
  if (!event.target.closest(".conversation-menu-wrap")) closeConversationMenus();
});
window.addEventListener("resize", () => closeConversationMenus());
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  const openMenu = els.list.querySelector(".conversation-menu:not([hidden])");
  if (openMenu) {
    const trigger = openMenu.parentElement.querySelector(".conversation-menu-trigger");
    openMenu.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    trigger.focus();
    return;
  }
  if (els.list.querySelector(".conversation-rename input")) renderHistory();
});

function renderThread() {
  const convo = activeConversation();
  els.stage.dataset.chatState = convo && convo.messages.length ? "active" : "empty";
  els.empty.hidden = Boolean(convo);
  els.messages.replaceChildren(...(convo ? convo.messages.map(messageEl) : []));
  scrollLatestUserToTop();
}

function scrollLatestUserToTop() {
  const latestUser = [...els.messages.querySelectorAll(".message.user")].at(-1);
  if (!latestUser) {
    els.thread.scrollTo({ top: 0 });
    return;
  }
  const threadTop = els.thread.getBoundingClientRect().top;
  const messageTop = latestUser.getBoundingClientRect().top;
  els.thread.scrollTop += messageTop - threadTop;
}

function resolveDuplicate(conversation, confirmation, choice, candidateId = null, button = null) {
  const card = button?.closest(".duplicate-confirmation");
  card?.querySelectorAll("button").forEach((action) => { action.disabled = true; });
  if (button) {
    button.textContent = t("saving");
  }
  requestJSON(`${TASKS_URL}/confirm-duplicate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      proposal_id: confirmation.id,
      choice,
      candidate_id: candidateId,
    }),
  }).then((result) => {
    confirmation.resolution = result.reply;
    confirmation.resolved = true;
    saveConversations();
    if (conversation.id === activeId) renderThread();
    return loadTasks();
  }).catch((err) => {
    const status = card?.querySelector(".confirmation-error");
    if (status) {
      status.textContent = `${t("duplicateError")} ${err.message}`;
      status.hidden = false;
    }
    card?.querySelectorAll("button").forEach((action) => {
      action.disabled = false;
      action.textContent = action.dataset.originalLabel;
    });
  });
}

function duplicateConfirmationEl(conversation, confirmation) {
  const card = document.createElement("section");
  card.className = "duplicate-confirmation";
  card.setAttribute("aria-label", t("possibleDuplicate"));

  const heading = document.createElement("h3");
  heading.textContent = t("possibleDuplicate");
  card.append(heading);

  const proposed = document.createElement("div");
  proposed.className = "confirmation-proposed";
  const proposedTitle = document.createElement("strong");
  proposedTitle.textContent = confirmation.task.title;
  proposed.append(proposedTitle);
  const proposedDetails = [
    confirmation.task.project,
    confirmation.task.due_date ? `${t("duePrefix")} ${confirmation.task.due_date}` : null,
    confirmation.task.priority && confirmation.task.priority !== "medium"
      ? `${t(confirmation.task.priority)} ${t("prioritySuffix")}`
      : null,
  ].filter(Boolean);
  if (proposedDetails.length) {
    const details = document.createElement("p");
    details.textContent = proposedDetails.join(" · ");
    proposed.append(details);
  }
  if (confirmation.task.description) {
    const description = document.createElement("p");
    description.textContent = confirmation.task.description;
    proposed.append(description);
  }
  card.append(proposed);

  for (const candidate of confirmation.candidates) {
    const existing = document.createElement("div");
    existing.className = "confirmation-existing";
    const existingLabel = document.createElement("span");
    existingLabel.className = "confirmation-label";
    existingLabel.textContent = t("possibleMatch");
    const existingTitle = document.createElement("strong");
    existingTitle.textContent = candidate.title;
    existing.append(existingLabel, existingTitle);

    const candidateDetails = [
      t(candidate.status),
      candidate.project,
      candidate.due_date ? `${t("duePrefix")} ${candidate.due_date}` : null,
      candidate.priority && candidate.priority !== "medium"
        ? `${t(candidate.priority)} ${t("prioritySuffix")}`
        : null,
    ].filter(Boolean);
    const details = document.createElement("p");
    details.textContent = [
      ...candidateDetails,
      candidate.description,
    ].filter(Boolean).join(" · ");
    existing.append(details);

    const update = document.createElement("button");
    update.type = "button";
    update.className = "btn";
    update.textContent = t("updateTask", { title: candidate.title });
    update.dataset.originalLabel = update.textContent;
    update.addEventListener("click", () => {
      resolveDuplicate(conversation, confirmation, "update_existing", candidate.id, update);
    });
    existing.append(update);
    card.append(existing);
  }

  const note = document.createElement("p");
  note.className = "confirmation-note";
  note.textContent = t("duplicatePreserveNote");
  card.append(note);

  const actions = document.createElement("div");
  actions.className = "confirmation-actions";
  const separate = document.createElement("button");
  separate.type = "button";
  separate.className = "btn primary";
  separate.textContent = t("createSeparateTask");
  separate.dataset.originalLabel = separate.textContent;
  separate.addEventListener("click", () => {
    resolveDuplicate(conversation, confirmation, "create_separate", null, separate);
  });
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "btn";
  cancel.textContent = t("cancel");
  cancel.dataset.originalLabel = cancel.textContent;
  cancel.addEventListener("click", () => {
    resolveDuplicate(conversation, confirmation, "cancel", null, cancel);
  });
  actions.append(separate, cancel);
  card.append(actions);

  const resolution = document.createElement("p");
  resolution.className = "confirmation-resolution";
  resolution.setAttribute("role", "status");
  if (confirmation.resolved) {
    resolution.textContent = confirmation.resolution || t("duplicateResolved");
    card.append(resolution);
    card.querySelectorAll("button").forEach((button) => { button.disabled = true; });
  } else {
    const error = document.createElement("p");
    error.className = "confirmation-error";
    error.setAttribute("role", "alert");
    error.hidden = true;
    card.append(error);
  }
  return card;
}

/* ---------- Tasks ---------- */
async function requestJSON(url, options = {}, csrfRetried = false) {
  const headers = new Headers(options.headers || {});
  if (options.method && options.method.toUpperCase() !== "GET") {
    headers.set("X-CSRFToken", document.querySelector('meta[name="csrf-token"]').content);
  }
  const response = await fetch(url, { ...options, headers });
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(t("unreadableResponse"));
  }
  if (!response.ok && response.status === 400 && data.code === "csrf_expired" && !csrfRetried) {
    const refreshResponse = await fetch("/api/csrf");
    if (refreshResponse.status === 401) {
      window.location.assign("/");
      throw new Error("Your session has expired. Please sign in again.");
    }
    let refreshed;
    try {
      refreshed = await refreshResponse.json();
    } catch {
      throw new Error(t("unreadableResponse"));
    }
    if (!refreshResponse.ok || typeof refreshed.csrf_token !== "string") {
      throw new Error(typeof refreshed.error === "string" ? refreshed.error : `The server returned status ${refreshResponse.status}.`);
    }
    document.querySelector('meta[name="csrf-token"]').content = refreshed.csrf_token;
    return requestJSON(url, options, true);
  }
  if (!response.ok) {
    if (response.status === 401) window.location.assign("/");
    const error = new Error(typeof data.error === "string" ? data.error : `The server returned status ${response.status}.`);
    Object.assign(error, data);
    throw error;
  }
  return data;
}

function setTaskStatus(message, isError = false) {
  els.taskStatus.textContent = message;
  els.taskStatus.classList.toggle("error", isError);
}

function taskChip(text, className = "") {
  const chip = document.createElement("span");
  chip.className = `task-chip ${className}`.trim();
  chip.textContent = text;
  return chip;
}

function renderTaskList(tasks) {
  els.taskList.replaceChildren();
  if (!tasks.length) {
    const empty = document.createElement("li");
    empty.className = "task-empty";
    empty.textContent = els.taskSearch.value.trim()
      ? t("noSearchMatches")
      : taskView === "today"
        ? t("nothingDueToday")
        : t("noTasksInView");
    els.taskList.append(empty);
    return;
  }

  const tasksById = new Map(tasks.map((task) => [task.id, task]));
  for (const task of tasks) {
    const item = document.createElement("li");
    item.className = `task-item${task.status === "completed" ? " completed" : ""}`;
    if (task.parent_task_id) item.classList.add("subtask");

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "task-check";
    checkbox.checked = task.status === "completed";
    checkbox.setAttribute("aria-label", `${t(checkbox.checked ? "reopenTask" : "completeTask")} ${task.title}`);
    checkbox.addEventListener("change", () => changeTaskStatus(task));

    const main = document.createElement("div");
    main.className = "task-main";
    const title = document.createElement("span");
    title.className = "task-title";
    title.textContent = task.title;
    main.append(title);
    if (task.description) {
      const description = document.createElement("p");
      description.className = "task-description";
      description.textContent = task.description;
      main.append(description);
    }

    const metadata = document.createElement("div");
    metadata.className = "task-meta";
    if (task.parent_task_id && tasksById.has(task.parent_task_id)) {
      metadata.append(taskChip(`${t("subtask")} · ${tasksById.get(task.parent_task_id).title}`));
    }
    if (task.project) metadata.append(taskChip(task.project));
    if (task.due_date) {
      const dueDate = new Date(`${task.due_date}T00:00:00`);
      const overdue = task.status === "open" && dueDate < new Date(new Date().toDateString());
      metadata.append(taskChip(`${t(overdue ? "overduePrefix" : "due")} · ${dueDate.toLocaleDateString(language)}`, overdue ? "overdue" : ""));
    }
    if (task.priority && task.priority !== "medium") {
      metadata.append(taskChip(`${t(task.priority)} ${t("prioritySuffix")}`, `priority-${task.priority}`));
    }
    if (metadata.childElementCount) main.append(metadata);

    const actions = document.createElement("div");
    actions.className = "task-actions";
    const edit = document.createElement("button");
    edit.type = "button";
    edit.textContent = t("editTaskAction");
    edit.setAttribute("aria-label", `${t("editTaskAction")} ${task.title}`);
    edit.addEventListener("click", () => openTaskEditor(task));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "delete-task";
    remove.textContent = t("delete");
    remove.setAttribute("aria-label", `${t("delete")} ${task.title}`);
    remove.addEventListener("click", () => deleteTask(task));
    actions.append(edit, remove);
    item.append(checkbox, main, actions);
    els.taskList.append(item);
  }
}

async function loadTasks() {
  setTaskStatus(t("loadingTasks"));
  const params = new URLSearchParams({ view: taskView });
  const search = els.taskSearch.value.trim();
  if (search) params.set("search", search);
  try {
    const data = await requestJSON(`${TASKS_URL}?${params}`);
    renderTaskList(data.tasks);
    const count = data.tasks.length;
    setTaskStatus(t("taskCount", { count }));
  } catch (err) {
    els.taskList.replaceChildren();
    const item = document.createElement("li");
    item.className = "task-empty";
    item.textContent = t("tasksCouldNotLoad");
    els.taskList.append(item);
    setTaskStatus(err.message, true);
  }
}

async function changeTaskStatus(task) {
  const status = task.status === "completed" ? "open" : "completed";
  try {
    await requestJSON(`${TASKS_URL}/${encodeURIComponent(task.id)}/status`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    await loadTasks();
  } catch (err) {
    setTaskStatus(err.message, true);
    await loadTasks();
  }
}

async function deleteTask(task) {
  if (!window.confirm(t("deleteTaskConfirm", { title: task.title }))) return;
  try {
    await requestJSON(`${TASKS_URL}/${encodeURIComponent(task.id)}`, { method: "DELETE" });
    await loadTasks();
  } catch (err) {
    setTaskStatus(err.message, true);
  }
}

function openTaskEditor(task = null) {
  editingTaskId = task?.id ?? null;
  els.taskEditorForm.reset();
  els.taskEditorTitle.textContent = task ? t("editTask") : t("newTask");
  els.saveTask.textContent = task ? t("saveChanges") : t("saveTask");
  els.taskTitle.value = task?.title ?? "";
  els.taskDescription.value = task?.description ?? "";
  els.taskProject.value = task?.project ?? "";
  els.taskDeadline.value = task?.due_date ?? "";
  els.taskPriority.value = task?.priority ?? "medium";
  els.taskFormError.hidden = true;
  els.taskEditor.showModal();
  els.taskTitle.focus();
}

async function saveTask(event) {
  event.preventDefault();
  els.saveTask.disabled = true;
  els.taskFormError.hidden = true;
  const task = {
    title: els.taskTitle.value.trim(),
    description: els.taskDescription.value.trim() || null,
    project: els.taskProject.value.trim() || null,
    due_date: els.taskDeadline.value || null,
    priority: els.taskPriority.value,
  };
  try {
    await requestJSON(
      editingTaskId ? `${TASKS_URL}/${encodeURIComponent(editingTaskId)}` : TASKS_URL,
      {
        method: editingTaskId ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(task),
      },
    );
    els.taskEditor.close();
    await loadTasks();
  } catch (err) {
    els.taskFormError.textContent = err.message;
    els.taskFormError.hidden = false;
  } finally {
    els.saveTask.disabled = false;
  }
}

els.taskFilters.addEventListener("click", (event) => {
  const button = event.target.closest("[data-task-view]");
  if (!button) return;
  taskView = button.dataset.taskView;
  els.taskFilters.querySelectorAll("[data-task-view]").forEach((item) => {
    item.setAttribute("aria-pressed", String(item === button));
  });
  loadTasks();
});
els.taskSearch.addEventListener("input", () => {
  window.clearTimeout(taskSearchTimer);
  taskSearchTimer = window.setTimeout(loadTasks, 180);
});
els.createTask.addEventListener("click", () => openTaskEditor());
els.cancelTask.addEventListener("click", () => els.taskEditor.close());
els.taskEditorForm.addEventListener("submit", saveTask);

function messageEl({ role, text, truncated = false, task_confirmations = [] }) {
  const div = document.createElement("div");
  div.className = `message ${role}`;
  if (role === "user") div.textContent = text;
  else {
    div.append(formatReply(text));
    if (truncated) {
      const notice = document.createElement("p");
      notice.className = "truncation-notice";
      notice.setAttribute("role", "status");
      notice.textContent = t("truncationNotice");
      div.append(notice);
    }
    if (Array.isArray(task_confirmations)) {
      const convo = activeConversation();
      for (const confirmation of task_confirmations) {
        if (convo && confirmation && typeof confirmation.id === "string") {
          div.append(duplicateConfirmationEl(convo, confirmation));
        }
      }
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
    const language = code.className.match(/(?:^|\s)language-([\w.+#-]+)/)?.[1] || t("code");
    const label = document.createElement("span");
    label.className = "code-language";
    label.textContent = language;

    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "copy-code";
    copy.textContent = t("copy");
    copy.setAttribute("aria-label", t("copyCode", { language }));
    copy.addEventListener("click", async () => {
      try {
        if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
        await navigator.clipboard.writeText(code.textContent);
        copy.textContent = t("copied");
      } catch {
        copy.textContent = t("copyFailed");
      }
      window.setTimeout(() => { copy.textContent = t("copy"); }, 1800);
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
  const dots = document.createElement("span");
  dots.className = "dots";
  dots.setAttribute("aria-hidden", "true");
  dots.append(document.createElement("i"), document.createElement("i"), document.createElement("i"));
  const label = document.createElement("span");
  label.className = "sr-only";
  label.textContent = t("loading");
  div.append(dots, label);
  return div;
}

function errorEl(convo, err) {
  const div = document.createElement("div");
  div.className = "message error";
  div.setAttribute("role", "alert");
  const p = document.createElement("p");
  const limitMessages = {
    ai_usage_limit: t("aiLimitReached", {
      limit: err.limit,
      date: typeof err.resets_at === "string" ? err.resets_at.slice(0, 10) : "",
    }),
    memory_capacity_limit: t("memoryLimitReached"),
    conversation_history_limit: t("historyLimitReached"),
  };
  const limitMessage = limitMessages[err.code];
  const detail = err instanceof TypeError ? t("cannotReachServer") : err.message;
  p.textContent = limitMessage || t("responseError", { detail });
  div.append(p);
  if (limitMessage) {
    const latestUser = [...convo.messages].reverse().find((message) => message.role === "user");
    if (latestUser && !els.input.value) {
      els.input.value = latestUser.text;
      autoGrow();
      updateSendButton();
    }
    const upgrade = document.createElement("a");
    upgrade.href = typeof err.upgrade_url === "string" && err.upgrade_url.startsWith("/")
      ? err.upgrade_url
      : "/pricing";
    upgrade.textContent = t("upgradeForMore");
    div.append(upgrade);
  } else {
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "retry";
    retry.textContent = t("retry");
    retry.addEventListener("click", () => { div.remove(); getReply(convo); });
    div.append(retry);
  }
  return div;
}

/* ---------- Sending ---------- */
async function submitThought(text) {
  let convo = activeConversation();
  if (!convo) {
    const title = text.length > 40 ? text.slice(0, 40).trim() + "…" : text;
    convo = {
      id: `draft-${crypto.randomUUID()}`,
      title,
      messages: [],
      pinned: false,
      serverPersisted: false,
    };
    conversations.unshift(convo);
    activeId = convo.id;
  }
  convo.messages.push({ role: "user", text, request_id: crypto.randomUUID() });
  saveConversations();
  render();
  scrollLatestUserToTop();
  await getReply(convo);
}

async function getReply(convo) {
  setLoading(true);
  const pending = loadingEl();
  els.messages.append(pending);
  scrollLatestUserToTop();
  try {
    const result = await requestReply(convo);
    if (result.resolved_proposal_id) {
      for (const message of convo.messages) {
        for (const confirmation of message.task_confirmations || []) {
          if (confirmation.id === result.resolved_proposal_id) {
            confirmation.resolved = true;
            confirmation.resolution = result.reply;
          }
        }
      }
    }
    convo.messages.push({
      role: "assistant",
      text: result.reply,
      truncated: result.truncated,
      task_confirmations: result.task_confirmations,
    });
    saveConversations();
    if (convo.id === activeId) renderThread();
    // Render the answer immediately; refreshing the task sidebar is independent
    // and shouldn't keep the user waiting after the chat API has replied.
    void loadTasks();
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
  if (!convo.serverPersisted) {
    const created = await requestJSON("/api/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: convo.title }),
    });
    const previousId = convo.id;
    convo.id = created.conversation.id;
    convo.serverPersisted = true;
    if (activeId === previousId) activeId = convo.id;
    saveConversations();
    renderHistory();
  }
  const userMessage = convo.messages[convo.messages.length - 1];
  const message = userMessage.text;
  const data = await requestJSON(API_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ conversation_id: convo.id, message, request_id: userMessage.request_id }),
  });
  if (typeof data.reply !== "string") throw new Error(t("responseMissingReply"));
  return {
    reply: data.reply,
    truncated: data.truncated === true,
    task_confirmations: Array.isArray(data.task_confirmations) ? data.task_confirmations : [],
    resolved_proposal_id: data.resolved_proposal_id,
  };
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
function startNewChat() {
  activeId = null;
  saveConversations();
  render();
  els.input.focus();
  setSidebar(false);
}
els.newChat.addEventListener("click", startNewChat);
els.search.addEventListener("input", renderHistory);
els.openSettings.addEventListener("click", () => window.location.assign("/settings"));

/* ---------- Start ---------- */
EnnoicI18n.applyTranslations(language);
const greetingHour = new Date().getHours();
const greetingKey = greetingHour < 12 ? "greetingMorning"
  : greetingHour < 18 ? "greetingAfternoon" : "greetingEvening";
els.greeting.dataset.i18n = greetingKey;
EnnoicI18n.applyTranslations(language);
setSidebar(!mobile.matches);
setTaskPanelOpen(loadTaskPanelPreference(), false);
updateDividerOrientation();
render();
updateSendButton();
loadTasks();
requestJSON("/api/conversations")
  .then(({ conversations: saved, has_more: hasMore, history_limit: historyLimit }) => {
    hasMoreConversationHistory = hasMore === true;
    conversationHistoryLimit = historyLimit;
    conversations = saved.map((conversation) => ({ ...conversation, serverPersisted: true }));
    if (!conversations.some((conversation) => conversation.id === activeId)) {
      activeId = null;
    }
    saveConversations();
    render();
    if (hasMore) showHistoryPlanNotice(historyLimit);
  })
  .catch((err) => showHistoryStatus(err.message || t("conversationSaveFailed"), true));
if (!mobile.matches) els.input.focus();
