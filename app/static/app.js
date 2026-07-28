const state = {
  kind: "", smartCategory: "", q: "", dateStart: "", dateEnd: "", source: "",
  items: [], total: 0, offset: 0, limit: 50, hasMore: false,
  loading: false, selectedId: null, editingId: null,
  fingerprint: "", firstPageFingerprint: "",
};

const kindLabels = { text: "文本", url: "链接", image: "图片" };
const smartCategoryLabels = {
  code: "代码", todo: "待办", prompt: "提示词",
  contact: "联系方式", path: "文件路径", sensitive: "敏感内容",
};
const list = document.getElementById("list");
const searchInput = document.getElementById("search");
const summaryText = document.getElementById("summaryText");
const loadState = document.getElementById("loadState");
const transparentPixel = "data:image/gif;base64,R0lGODlhAQABAAAAACw=";
const keyboardDebug = new URLSearchParams(window.location.search).has("keyboardDebug");

function reportKeyboardDebug(eventName) {
  if (keyboardDebug) document.title = `ClipVault Debug | ${eventName} | ${state.selectedId ?? "none"}`;
}

function icon(name) {
  const paths = {
    copy: '<path d="M8 8h10v12H8z"></path><path d="M6 16H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>',
    edit: '<path d="M12 20h9"></path><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"></path>',
    star: '<path d="m12 3 2.7 5.5 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.8 1-6.1-4.4-4.3 6.1-.9z"></path>',
    trash: '<path d="M3 6h18M8 6V4h8v2M6 6l1 15h10l1-15"></path>',
    save: '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2zM17 21v-8H7v8M7 3v5h8"></path>',
    x: '<path d="M18 6 6 18M6 6l12 12"></path>',
  };
  return `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || ""}</svg>`;
}

function escapeHtml(value) {
  return String(value || "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}

function buildParams(append) {
  const params = new URLSearchParams();
  if (state.kind && state.kind !== "fav") params.set("kind", state.kind);
  if (state.kind === "fav") params.set("fav", "1");
  if (state.q) params.set("q", state.q);
  if (state.source) params.set("source", state.source);
  if (state.smartCategory) params.set("smart_category", state.smartCategory);
  if (state.dateStart) params.set("start_date", state.dateStart);
  if (state.dateEnd) params.set("end_date", state.dateEnd);
  params.set("limit", String(state.limit));
  params.set("offset", String(append ? state.offset : 0));
  return params;
}

function itemsFingerprint(items) {
  return items.map((item) => [
    item.id, item.favorite, item.copy_count, item.last_copied_at,
    item.content, item.source_app, item.source_title, item.smart_category,
  ].join("\u001f")).join("\u001e");
}

async function load(append = false) {
  if (state.loading || (append && !state.hasMore)) return;
  state.loading = true;
  loadState.textContent = append ? "载入更多..." : "";
  if (!append) state.offset = 0;
  try {
    const response = await fetch("/api/items?" + buildParams(append).toString());
    const payload = await response.json();
    const incoming = Array.isArray(payload.items) ? payload.items : [];
    if (append) {
      const existing = new Set(state.items.map((item) => item.id));
      state.items.push(...incoming.filter((item) => !existing.has(item.id)));
    } else {
      state.items = incoming;
    }
    state.total = Number(payload.total || 0);
    state.hasMore = Boolean(payload.has_more);
    state.offset = state.items.length;
    state.fingerprint = itemsFingerprint(state.items);
    state.firstPageFingerprint = itemsFingerprint(state.items.slice(0, state.limit));
    render();
  } catch (error) {
    if (!append) list.innerHTML = renderEmpty("加载失败");
    loadState.textContent = "加载失败";
  } finally {
    state.loading = false;
    if (!loadState.textContent.includes("失败")) loadState.textContent = "";
  }
}

async function poll() {
  if (state.loading || state.editingId != null) return;
  try {
    const params = buildParams(false);
    params.set("offset", "0");
    const response = await fetch("/api/items?" + params.toString());
    const payload = await response.json();
    const fresh = Array.isArray(payload.items) ? payload.items : [];
    const nextFingerprint = itemsFingerprint(fresh);
    if (nextFingerprint === state.fingerprint) return;
    if (nextFingerprint === state.firstPageFingerprint && Number(payload.total) === state.total) return;
    const freshIds = new Set(fresh.map((item) => item.id));
    const tail = state.items.filter((item, index) => index >= state.limit && !freshIds.has(item.id));
    state.items = [...fresh, ...tail];
    state.total = Number(payload.total || state.items.length);
    state.hasMore = state.items.length < state.total;
    state.offset = state.items.length;
    state.firstPageFingerprint = nextFingerprint;
    state.fingerprint = itemsFingerprint(state.items);
    render();
  } catch (error) {
    // Background refresh stays silent.
  }
}

function dateGroupLabel(createdAt) {
  const value = String(createdAt || "").slice(0, 10);
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  const format = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  if (value === format(today)) return "今天";
  if (value === format(yesterday)) return "昨天";
  return "更早";
}

function shortTime(createdAt) {
  const value = String(createdAt || "");
  return value.length >= 16 ? value.slice(11, 16) : value;
}

function renderActions(item) {
  const pinTitle = item.favorite ? "取消置顶" : "置顶";
  return `<div class="clip-actions">
    <button data-act="copy" title="复制" aria-label="复制">${icon("copy")}</button>
    ${(item.kind === "text" || item.kind === "url") ? `<button data-act="edit" title="编辑" aria-label="编辑">${icon("edit")}</button>` : ""}
    <button data-act="fav" class="${item.favorite ? "active-star" : ""}" title="${pinTitle}" aria-label="${pinTitle}">${icon("star")}</button>
    <button data-act="del" class="danger" title="删除" aria-label="删除">${icon("trash")}</button>
  </div>`;
}

function renderItem(item) {
  const selected = item.id === state.selectedId ? "selected" : "";
  const favorite = item.favorite ? "is-favorite" : "";
  if (state.editingId === item.id && item.kind !== "image") {
    return `<article class="clip-item ${favorite} ${selected} editing" data-id="${item.id}">
      <textarea class="edit-area" rows="4">${escapeHtml(item.content)}</textarea>
      <div class="clip-actions edit-actions"><button data-act="save" class="primary-act" title="保存" aria-label="保存">${icon("save")}</button><button data-act="cancel" title="取消" aria-label="取消">${icon("x")}</button></div>
    </article>`;
  }
  const srcActive = state.source === item.source_app ? " src-active" : "";
  return `<article class="clip-item ${favorite} ${selected}" data-id="${item.id}" title="双击粘贴">
    <div class="clip-preview">
      <div class="clip-meta">
        <span class="badge ${item.kind}">${kindLabels[item.kind] || item.kind}</span>
        ${item.smart_category ? `<span class="smart-badge ${escapeHtml(item.smart_category)}">${escapeHtml(smartCategoryLabels[item.smart_category] || item.smart_category)}</span>` : ""}
        ${item.source_app ? `<button class="src-badge${srcActive}" data-source="${escapeHtml(item.source_app)}" title="${escapeHtml(item.source_title || item.source_app)}">${escapeHtml(item.source_app)}</button>` : ""}
        <span class="clip-time">${escapeHtml(item.created_at)}</span>
        ${item.copy_count > 1 ? `<span class="copy-count">×${item.copy_count}</span>` : ""}
        ${renderActions(item)}
      </div>
      ${item.kind === "image" ? `<img class="clip-thumb zoomable" src="/api/image/${item.id}" alt="剪贴图片" data-id="${item.id}" onerror="this.src='${transparentPixel}'">` : ""}
      ${item.kind === "url" ? `<a class="clip-url" href="${escapeHtml(item.content)}" target="_blank" rel="noopener">${escapeHtml(item.content)}</a>` : ""}
      ${item.kind === "text" ? `<div class="clip-text">${escapeHtml(item.content)}</div>` : ""}
    </div>
  </article>`;
}

function renderEmpty(message = "还没有记录") {
  return `<div class="empty"><strong>${escapeHtml(message)}</strong><span>新复制的内容会显示在这里</span></div>`;
}

function ensureSelection() {
  const ids = state.items.map((item) => item.id);
  if (!state.selectedId || !ids.includes(state.selectedId)) state.selectedId = ids[0] || null;
}

function selectFirstItem() {
  state.selectedId = state.items.length ? state.items[0].id : null;
}

function render() {
  list.classList.toggle("image-grid", state.kind === "image");
  if (!state.items.length) {
    list.innerHTML = renderEmpty();
    state.selectedId = null;
    updateSummary();
    return;
  }
  ensureSelection();
  let currentGroup = "";
  const markup = [];
  for (const item of state.items) {
    const group = dateGroupLabel(item.last_copied_at || item.created_at);
    if (group !== currentGroup) {
      currentGroup = group;
      markup.push(`<div class="date-group">${group}</div>`);
    }
    markup.push(renderItem(item));
  }
  list.innerHTML = markup.join("");
  updateSummary();
}

function selectItem(id) {
  state.selectedId = id;
  list.querySelectorAll(".clip-item.selected").forEach((item) => item.classList.remove("selected"));
  const selected = list.querySelector(`.clip-item[data-id="${id}"]`);
  if (selected) selected.classList.add("selected");
}

function updateSummary() {
  const type = state.kind === "fav" ? "置顶" : (kindLabels[state.kind] || "全部");
  const smart = state.smartCategory ? ` · ${smartCategoryLabels[state.smartCategory]}` : "";
  const source = state.source ? ` · ${state.source}` : "";
  summaryText.textContent = `${type}${smart}${source} · 已显示 ${state.items.length} / 共 ${state.total} 条`;
}

async function moveSelection(delta) {
  if (!state.items.length) return;
  ensureSelection();
  const index = state.items.findIndex((item) => item.id === state.selectedId);
  const next = Math.max(0, Math.min(state.items.length - 1, index + delta));
  if (next === state.items.length - 1 && delta > 0 && state.hasMore) await load(true);
  selectItem(state.items[Math.min(next, state.items.length - 1)].id);
  const selected = list.querySelector(`.clip-item[data-id="${state.selectedId}"]`);
  if (selected) selected.scrollIntoView({ block: "nearest" });
}

async function doPaste(id) {
  state.selectedId = id;
  try {
    const response = await fetch(`/api/paste/${id}`, { method: "POST" });
    const payload = await response.json();
    if (!payload.ok) toast("粘贴失败，请重试");
  } catch (error) {
    toast("粘贴失败，请重试");
  }
}

async function saveEdit(id, content) {
  const response = await fetch(`/api/items/${id}`, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  const payload = await response.json().catch(() => ({ ok: false }));
  if (!payload.ok) return toast("保存失败");
  state.editingId = null;
  await load(false);
  toast("已保存");
}

function openImage(id) {
  document.getElementById("imgFull").src = `/api/image/${id}?full=1`;
  document.getElementById("imgModal").classList.add("show");
}

function closeImage() {
  document.getElementById("imgModal").classList.remove("show");
  document.getElementById("imgFull").src = transparentPixel;
}

list.addEventListener("scroll", () => {
  if (state.hasMore && !state.loading && list.scrollTop + list.clientHeight >= list.scrollHeight - 140) load(true);
});

list.addEventListener("click", async (event) => {
  const source = event.target.closest(".src-badge[data-source]");
  if (source) {
    state.source = state.source === source.dataset.source ? "" : source.dataset.source;
    return load(false);
  }
  const button = event.target.closest("button[data-act]");
  if (button) {
    const itemElement = button.closest(".clip-item");
    const id = Number(itemElement.dataset.id);
    const action = button.dataset.act;
    if (action === "copy") {
      const response = await fetch(`/api/copy/${id}`, { method: "POST" });
      toast(response.ok ? "已复制" : "复制失败");
    } else if (action === "del") {
      if (!(await showConfirm("删除记录", "确定删除这条记录？"))) return;
      await fetch(`/api/items/${id}`, { method: "DELETE" });
      state.items = state.items.filter((item) => item.id !== id);
      state.total = Math.max(0, state.total - 1);
      render();
    } else if (action === "fav") {
      await fetch(`/api/favorite/${id}`, { method: "POST" });
      await load(false);
    } else if (action === "edit") {
      state.editingId = id;
      render();
      const area = list.querySelector(`.clip-item[data-id="${id}"] .edit-area`);
      if (area) area.focus();
    } else if (action === "cancel") {
      state.editingId = null;
      render();
    } else if (action === "save") {
      const area = itemElement.querySelector(".edit-area");
      await saveEdit(id, area ? area.value : "");
    }
    return;
  }
  const itemElement = event.target.closest(".clip-item");
  if (!itemElement) return;
  selectItem(Number(itemElement.dataset.id));
  if (event.target.closest("img.zoomable")) openImage(Number(itemElement.dataset.id));
});

list.addEventListener("dblclick", (event) => {
  if (event.target.closest("button[data-act]")) return;
  const itemElement = event.target.closest(".clip-item");
  if (itemElement) doPaste(Number(itemElement.dataset.id));
});

document.querySelectorAll("#kindChips .filter-chip").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("#kindChips .filter-chip").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    state.kind = button.dataset.kind;
    state.source = "";
    load(false);
  });
});

document.querySelectorAll("#smartChips .smart-chip").forEach((button) => {
  button.addEventListener("click", () => {
    const next = state.smartCategory === button.dataset.smart ? "" : button.dataset.smart;
    state.smartCategory = next;
    document.querySelectorAll("#smartChips .smart-chip").forEach((item) => {
      item.classList.toggle("active", item.dataset.smart === next);
    });
    state.source = "";
    load(false);
  });
});

let searchTimer;
searchInput.addEventListener("input", (event) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.q = event.target.value.trim(); load(false); }, 180);
});

function isPrintableKey(event) {
  return event.key.length === 1 && !event.ctrlKey && !event.altKey && !event.metaKey;
}

document.getElementById("imgClose").addEventListener("click", closeImage);
document.getElementById("imgModal").addEventListener("click", (event) => { if (event.target.id === "imgModal") closeImage(); });

const dateModal = document.getElementById("datePopover");
function closeDateModal() { dateModal.classList.remove("show"); }
document.getElementById("openDate").addEventListener("click", () => {
  document.getElementById("dateStartInput").value = state.dateStart;
  document.getElementById("dateEndInput").value = state.dateEnd;
  dateModal.classList.add("show");
});
document.getElementById("closeDate").addEventListener("click", closeDateModal);
document.getElementById("clearDate").addEventListener("click", () => {
  state.dateStart = ""; state.dateEnd = ""; closeDateModal(); load(false);
});
document.getElementById("applyDate").addEventListener("click", () => {
  state.dateStart = document.getElementById("dateStartInput").value;
  state.dateEnd = document.getElementById("dateEndInput").value;
  closeDateModal(); load(false);
});
document.querySelectorAll(".quick-ranges button").forEach((button) => button.addEventListener("click", () => {
  const today = new Date();
  const format = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  if (button.dataset.range === "all") { state.dateStart = ""; state.dateEnd = ""; }
  else if (button.dataset.range === "today") { state.dateStart = format(today); state.dateEnd = format(today); }
  else { const start = new Date(today); start.setDate(today.getDate() - Number(button.dataset.range) + 1); state.dateStart = format(start); state.dateEnd = format(today); }
  closeDateModal(); load(false);
}));

let confirmResolve = null;
const confirmModal = document.getElementById("confirmModal");
function closeConfirm(value) { confirmModal.classList.remove("show"); if (confirmResolve) confirmResolve(value); confirmResolve = null; }
function showConfirm(title, message) {
  document.getElementById("confirmTitle").textContent = title;
  document.getElementById("confirmMsg").textContent = message;
  confirmModal.classList.add("show");
  return new Promise((resolve) => { confirmResolve = resolve; });
}
document.getElementById("confirmOk").addEventListener("click", () => closeConfirm(true));
document.getElementById("confirmCancel").addEventListener("click", () => closeConfirm(false));

const settingsModal = document.getElementById("settingsModal");
const hkValue = document.getElementById("hkValue");
const hkValue2 = document.getElementById("hkValue2");
const recBtn = document.getElementById("recBtn");
const recBtn2 = document.getElementById("recBtn2");
let pendingHotkey = "ctrl+e";
let pendingObsidianHotkey = "ctrl+alt+o";
let recordingTarget = null;

function renderHotkeyStatus(element, status) {
  if (!status) { element.textContent = "运行状态将在应用启动后显示"; element.className = "status-line"; return; }
  element.textContent = status.ok ? `已生效 · ${status.active}` : `未生效 · ${status.error || "注册失败"}`;
  element.className = `status-line ${status.ok ? "success" : "error"}`;
}

async function openSettings() {
  const response = await fetch("/api/config");
  const config = await response.json();
  pendingHotkey = config.hotkey || "ctrl+e";
  pendingObsidianHotkey = config.obsidian_hotkey || "ctrl+alt+o";
  hkValue.textContent = pendingHotkey;
  hkValue2.textContent = pendingObsidianHotkey;
  document.getElementById("obsidianDir").value = config.obsidian_dir || "";
  document.getElementById("monitorPaused").checked = Boolean(config.monitor_paused);
  document.getElementById("sensitiveFilter").checked = config.sensitive_filter !== false;
  document.getElementById("excludedApps").value = (config.excluded_apps || []).join(", ");
  document.getElementById("retentionDays").value = String(config.retention_days || 0);
  renderHotkeyStatus(document.getElementById("mainHotkeyStatus"), config.hotkeys && config.hotkeys.main);
  renderHotkeyStatus(document.getElementById("obsidianHotkeyStatus"), config.hotkeys && config.hotkeys.obsidian);
  const autostart = config.autostart || {};
  const autostartStatus = document.getElementById("autostartStatus");
  autostartStatus.textContent = autostart.enabled ? "开机启动已启用" : "开机启动未生效";
  autostartStatus.className = `status-line ${autostart.enabled ? "success" : "error"}`;
  settingsModal.classList.add("show");
}

function closeSettings() { settingsModal.classList.remove("show"); recordingTarget = null; recBtn.classList.remove("recording"); recBtn2.classList.remove("recording"); }
document.getElementById("openSettings").addEventListener("click", openSettings);
document.getElementById("closeSettings").addEventListener("click", closeSettings);
document.getElementById("cancelSettings").addEventListener("click", closeSettings);
recBtn.addEventListener("click", () => { recordingTarget = "main"; recBtn.classList.add("recording"); recBtn.textContent = "请按键"; });
recBtn2.addEventListener("click", () => { recordingTarget = "obsidian"; recBtn2.classList.add("recording"); recBtn2.textContent = "请按键"; });

function normalizeKey(key) {
  const aliases = { " ": "space", control: "ctrl", meta: "win", escape: "esc", arrowup: "up", arrowdown: "down" };
  return aliases[key.toLowerCase()] || key.toLowerCase();
}

document.addEventListener("keydown", (event) => {
  if (!recordingTarget) return;
  event.preventDefault(); event.stopPropagation();
  if (event.key === "Escape") { recordingTarget = null; recBtn.textContent = "录制"; recBtn2.textContent = "录制"; return; }
  const modifiers = [];
  if (event.ctrlKey) modifiers.push("ctrl");
  if (event.altKey) modifiers.push("alt");
  if (event.shiftKey) modifiers.push("shift");
  if (event.metaKey) modifiers.push("win");
  const key = normalizeKey(event.key);
  if (["ctrl", "alt", "shift", "win"].includes(key) || !modifiers.length) return;
  const combo = [...modifiers, key].join("+");
  if (recordingTarget === "main") { pendingHotkey = combo; hkValue.textContent = combo; }
  else { pendingObsidianHotkey = combo; hkValue2.textContent = combo; }
  recordingTarget = null;
  recBtn.textContent = "录制"; recBtn2.textContent = "录制";
  recBtn.classList.remove("recording"); recBtn2.classList.remove("recording");
}, true);

document.getElementById("pickObsidianDir").addEventListener("click", async () => {
  const response = await fetch("/api/obsidian/folder", { method: "POST" });
  const payload = await response.json();
  if (payload.ok) document.getElementById("obsidianDir").value = payload.path;
  else toast(payload.error || "未选择文件夹");
});

document.getElementById("saveSettings").addEventListener("click", async () => {
  const excludedApps = document.getElementById("excludedApps").value.split(/[,，\n]/).map((value) => value.trim()).filter(Boolean);
  const response = await fetch("/api/config", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      hotkey: pendingHotkey,
      obsidian_hotkey: pendingObsidianHotkey,
      obsidian_dir: document.getElementById("obsidianDir").value.trim(),
      monitor_paused: document.getElementById("monitorPaused").checked,
      sensitive_filter: document.getElementById("sensitiveFilter").checked,
      excluded_apps: excludedApps,
      retention_days: Number(document.getElementById("retentionDays").value),
    }),
  });
  const payload = await response.json().catch(() => ({ ok: false }));
  if (!payload.ok) return toast(payload.error || "保存失败");
  renderHotkeyStatus(document.getElementById("mainHotkeyStatus"), payload.hotkeys && payload.hotkeys.main);
  renderHotkeyStatus(document.getElementById("obsidianHotkeyStatus"), payload.hotkeys && payload.hotkeys.obsidian);
  toast("设置已保存");
});

document.getElementById("cleanupDuplicates").addEventListener("click", async () => {
  const preview = await (await fetch("/api/storage/duplicates")).json();
  if (!preview.records) return toast("没有可清理的重复项");
  if (!(await showConfirm("清理重复项", `将删除 ${preview.records} 条重复记录，并先备份数据库。`))) return;
  const result = await (await fetch("/api/storage/duplicates", { method: "POST" })).json();
  toast(`已清理 ${result.deleted || 0} 条重复记录`);
  load(false);
});

document.getElementById("clearHistory").addEventListener("click", async () => {
  if (!(await showConfirm("清空全部历史", "此操作会删除全部剪贴记录和图片，且无法撤销。"))) return;
  const result = await (await fetch("/api/items", { method: "DELETE" })).json();
  toast(`已清空 ${result.deleted || 0} 条记录`);
  closeSettings(); load(false);
});

function hideWindow() { fetch("/api/hide", { method: "POST" }).catch(() => {}); }
document.getElementById("closePopup").addEventListener("click", hideWindow);

window.focusSelection = function focusSelection() {
  selectFirstItem();
  list.focus({ preventScroll: true });
  if (state.selectedId != null) selectItem(state.selectedId);
  reportKeyboardDebug("focus");
};

let toastTimer;
function toast(message) {
  const element = document.getElementById("toast");
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.classList.remove("show"), 2200);
}

document.addEventListener("keydown", async (event) => {
  if (recordingTarget || settingsModal.classList.contains("show")) return;
  if (event.target.closest("input, textarea, select")) {
    if (event.key === "Escape") { event.target.blur(); list.focus(); }
    return;
  }
  if (event.target.closest("button") && !list.contains(event.target)) return;
  if (event.key === "ArrowDown") { event.preventDefault(); await moveSelection(1); reportKeyboardDebug("ArrowDown"); }
  else if (event.key === "ArrowUp") { event.preventDefault(); await moveSelection(-1); reportKeyboardDebug("ArrowUp"); }
  else if (event.key === "Enter" && state.selectedId != null) { event.preventDefault(); reportKeyboardDebug("Enter"); doPaste(state.selectedId); }
  else if (event.key === "Escape") {
    if (document.getElementById("imgModal").classList.contains("show")) closeImage();
    else if (dateModal.classList.contains("show")) closeDateModal();
    else hideWindow();
  } else if (isPrintableKey(event)) {
    searchInput.focus();
    searchInput.value += event.key;
    searchInput.dispatchEvent(new Event("input", { bubbles: true }));
  }
});

load(false);
setInterval(poll, 2500);
