import { saved, read, visited, state } from "./state.js";
import { workspaceHref } from "./auth-navigation.js";
import { routeHash } from "./router.js";
import { $, esc, icon, refreshIcons } from "./components/ui.js";
import { saveStores, render, commitRoute } from "./actions.js";
import { setAnalytics, analyticsOptOut, analyticsDisabled } from "./analytics.js";

export const account = {
  user: null, csrfToken: "", loginEnabled: false, eventsEnabled: false,
  retentionDays: 90, devMode: false, ready: false, loading: false, sessionKnown: false,
  syncState: "local", error: "", expired: false, syncedAt: 0,
};
let initialization, refreshing, identity, lastChecked = 0, changing = 0;
const inFlight = new Set();
function localSet(key) {
  try {
    const items = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(items) ? items.filter(x => typeof x === "string") : [];
  } catch { return []; }
}
function replaceSet(collection, items) {
  collection.clear(); items.forEach(id => collection.add(id));
}
function selectIdentity(user) {
  const next = user?.id || "";
  if (identity === next) return;
  identity = next;
  const prefix = next ? "pc-user-" + next : "pc";
  replaceSet(saved, localSet(prefix + "-saved"));
  replaceSet(read, localSet(prefix + "-read"));
  replaceSet(visited, localSet(prefix + "-visited").slice(-500));
}
function persistAnonymous(kind, collection, id, enabled) {
  const next = new Set(collection);
  enabled ? next.add(id) : next.delete(id);
  try { localStorage.setItem("pc-" + kind, JSON.stringify([...next])); }
  catch { throw new Error("浏览器未能保存记录，请检查存储权限后重试。"); }
}
export function notify(message) {
  let node = $("#accountNotice");
  if (!node) {
    node = document.createElement("div"); node.id = "accountNotice";
    node.className = "account-notice"; node.setAttribute("role", "status");
    document.body.appendChild(node);
  }
  node.textContent = message; node.hidden = false;
  clearTimeout(node.timer); node.timer = setTimeout(() => (node.hidden = true), 5000);
}
async function request(path, body) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(path, {
      cache: "no-store", signal: controller.signal,
      ...(body ? { method: "POST", headers: {
        "Content-Type": "application/json", "X-CSRF-Token": account.csrfToken,
      }, body: JSON.stringify(body) } : {}),
    });
    if (!response.ok) {
      if (response.status === 401) {
        account.expired = true; account.syncState = "error";
        account.error = "登录已过期，请重新登录后继续同步。";
        renderAccountButton();
      }
      const error = new Error(response.status === 401 ? account.error : "账号操作暂未完成，请重试");
      error.status = response.status; throw error;
    }
    return await response.json();
  } catch (error) {
    if (error.status) throw error;
    throw new Error("暂时无法连接账号服务，请检查网络后重试。");
  } finally { clearTimeout(timer); }
}
async function ids(kind) {
  const items = [];
  for (let page = 1; ; page++) {
    const data = await request("/api/account/" + kind + "?page=" + page);
    items.push(...data.ids);
    if (page >= data.pages) return items;
  }
}
export function persistCollections() {
  // An unknown session must never copy account records into the guest library.
  if (!account.sessionKnown) return;
  const prefix = account.user ? "pc-user-" + account.user.id : "pc";
  try {
    localStorage.setItem(prefix + "-saved", JSON.stringify([...saved]));
    localStorage.setItem(prefix + "-read", JSON.stringify([...read]));
    localStorage.setItem(prefix + "-visited", JSON.stringify([...visited].slice(-500)));
  } catch {}
}
export function renderAccountButton() {
  const root = $("#accountSlot");
  if (!root) return;
  const label = account.user?.login || "登录";
  root.innerHTML = `<a class="account-entry" href="${account.user ? '#account' : esc(workspaceHref(['welcome', 'login', 'account'].includes(state.view) ? '#home' : routeHash(state)))}" aria-label="${esc(account.user ? label + '，我的账号' : '登录，管理文献库')}">${icon(account.user ? "circle-user-round" : "user-round", true)}<span>${esc(label)}</span>${account.expired ? '<span class="account-session-label">需登录</span>' : ''}</a>`;
  refreshIcons(root);
}
export function initAccount({ force = false } = {}) {
  if (initialization) return initialization;
  if (account.ready && !force) return Promise.resolve(account);
  // Do not replace collection sets in the middle of a mutation.
  if (changing) return Promise.resolve(account);
  account.loading = true;
  initialization = (async () => {
    try {
      const data = await request("/api/account");
      selectIdentity(data.user);
      Object.assign(account, data, { sessionKnown: true, expired: false, error: "" });
      if (account.user) {
        account.syncState = "loading";
        const [favorites, reading] = await Promise.all([ids("favorites"), ids("reading")]);
        replaceSet(saved, favorites); replaceSet(read, reading);
        account.syncState = "synced"; account.syncedAt = Date.now();
      } else { account.syncState = "local"; account.syncedAt = 0; }
      setAnalytics(account.eventsEnabled);
      try {
        if (account.user && sessionStorage.getItem("pc-login-pending")) {
          sessionStorage.removeItem("pc-login-pending"); notify("已登录，文献库已同步。");
        }
      } catch {}
    } catch (error) {
      account.error = error.message; account.syncState = "error";
    } finally {
      account.ready = true; account.loading = false; lastChecked = Date.now();
      renderAccountButton(); saveStores(); initialization = null;
    }
    return account;
  })();
  return initialization;
}
export async function refreshAccount() {
  if (refreshing) return refreshing;
  refreshing = (async () => {
    await initAccount({ force: true });
    if (["account", "login", "library", "discover", "paper"].includes(state.view)) await render();
  })();
  try { await refreshing; } finally { refreshing = null; }
}
async function requireReady() {
  await initAccount();
  if (!account.sessionKnown || account.loading || account.syncState === "error" || account.expired)
    throw new Error(account.error || "文献库正在同步，请稍后重试。");
}
export async function toggleFavorite(id) {
  await initAccount();
  if (inFlight.has(id)) return saved.has(id);
  inFlight.add(id); changing++;
  const enabled = !saved.has(id);
  try {
    await requireReady();
    if (account.user) await request("/api/account/favorites", { ids: [id], enabled });
    else persistAnonymous("saved", saved, id, enabled);
    enabled ? saved.add(id) : saved.delete(id);
    if (account.user) account.syncedAt = Date.now();
    saveStores(); return enabled;
  } catch (error) { notify(error.message); throw error; }
  finally { inFlight.delete(id); changing--; }
}
export async function setRead(id, enabled) {
  await initAccount(); changing++;
  try {
    await requireReady();
    if (account.user) await request("/api/account/reading", { ids: [id], enabled });
    else persistAnonymous("read", read, id, enabled);
    enabled ? read.add(id) : read.delete(id);
    if (account.user) account.syncedAt = Date.now();
    saveStores(); return enabled;
  } catch (error) { notify(error.message); throw error; }
  finally { changing--; }
}
export function markRead(id) { return setRead(id, true); }
export async function recordVisit(id) {
  await initAccount();
  if (!account.sessionKnown) return;
  visited.delete(id); visited.add(id);
  while (visited.size > 500) visited.delete(visited.values().next().value);
  persistCollections();
}
function loginReturn() {
  try {
    const hash = sessionStorage.getItem("pc-login-return") || "#library";
    if (hash.startsWith("#") && !/^#\/?(?:account|login)(?:[?]|$)/.test(hash) && hash.length <= 2048 && !/[\r\n]/.test(hash)) return hash;
  } catch {}
  return "#library";
}
export function loginHref() { return workspaceHref(loginReturn()); }
function pendingLocal() {
  return {
    favorites: localSet("pc-saved").filter(id => !saved.has(id)),
    reading: localSet("pc-read").filter(id => !read.has(id)),
  };
}
function importKey() { return "pc-user-" + account.user.id + "-import-dismissed"; }
function importSignature(pending) {
  return JSON.stringify([pending.favorites.slice().sort(), pending.reading.slice().sort()]);
}
export async function importLocal() {
  changing++;
  try {
    await requireReady();
    if (!account.user) throw new Error("请先登录后再导入。");
    const pending = pendingLocal();
    for (const [kind, collection, items] of [
      ["favorites", saved, pending.favorites], ["reading", read, pending.reading],
    ]) for (let offset = 0; offset < items.length; offset += 250) {
      const batch = items.slice(offset, offset + 250);
      await request("/api/account/" + kind, { ids: batch, enabled: true });
      // Keep confirmed batches on partial failure; retry only sends missing IDs.
      batch.forEach(id => collection.add(id)); saveStores();
      account.syncedAt = Date.now();
    }
    notify("本机文献记录已导入");
  } finally { changing--; }
}
export function libraryContext(mode) {
  if (mode === "visited") return "最近浏览仅保存在当前浏览器，按浏览顺序排列。";
  if (!account.ready || account.loading) return "正在确认文献库状态…";
  if (account.expired) return "登录已过期，重新登录后可继续同步。";
  if (account.syncState === "error") return "暂时显示上次文献记录，请重新同步后继续保存。";
  if (account.devMode) return "本地开发账号 · 收藏与已读保存在当前开发环境";
  return account.user ? "账号文献库 · 收藏与已读可在不同设备间同步" : "本机文献库 · 登录后可同步到其他设备";
}
export function renderLibraryAccount(root) {
  const message = libraryContext(state.libraryMode);
  root.innerHTML = `<span>${esc(message)}</span>${state.libraryMode === 'visited' ? '' : account.expired ? '<a href="#account">重新登录</a>' : account.syncState === 'error' ? '<button type="button" class="text-btn" data-sync-account>重新同步</button>' : !account.user && account.ready ? `<a href="${esc(workspaceHref(routeHash(state)))}">登录账号</a>` : ''}`;
  root.querySelector('[data-sync-account]')?.addEventListener('click', () => void refreshAccount());
}
function privacy() {
  return `<details class="account-privacy"><summary>隐私与使用偏好</summary><p id="analyticsHelp">记录页面访问、点击与停留时间，帮助我们改进产品；不记录搜索词和输入内容。明细保留 ${account.retentionDays} 天。</p><div class="account-preference"><input type="checkbox" id="analyticsPreference" aria-describedby="analyticsHelp" ${!analyticsDisabled() ? "checked" : ""} ${!account.eventsEnabled ? "disabled" : ""}><label for="analyticsPreference">允许记录此浏览器的产品使用情况</label></div><a class="text-btn" href="#docs?doc=account">账号与文献库使用说明 ${icon("arrow-up-right", true)}</a></details>`;
}
function importPanel() {
  if (!account.user) return "";
  const pending = pendingLocal(), count = pending.favorites.length + pending.reading.length;
  if (!count) return "";
  try { if (localStorage.getItem(importKey()) === importSignature(pending)) return ""; } catch {}
  return `<section class="account-import"><h3>把本机文献带进账号</h3><p>此浏览器还有 ${pending.favorites.length} 篇收藏、${pending.reading.length} 篇已读未加入当前账号。确认属于你之后再导入。</p><div><button type="button" class="icon-btn" id="importLocal" ${account.syncState !== 'synced' ? 'disabled' : ''}>导入本机文献记录</button><button type="button" class="text-btn" id="dismissImport">暂不导入</button></div><p id="importStatus" role="status"></p></section>`;
}
function bindLoginLinks(root) {
  root.querySelectorAll('a[href^="/auth/github/login"]').forEach(link => link.addEventListener("click", () => {
    try { sessionStorage.setItem("pc-login-pending", "1"); } catch {}
  }));
}
// Compatibility bridge for old bookmarks; there is no intermediate login UI.
export function renderLogin() {
  if (state.authStatus) {
    state.view = "welcome";
    return commitRoute({ replace: true });
  }
  location.replace(loginHref());
}
export function renderAccount() {
  const root = $("#accountBody"), user = account.user;
  if (!root) return;
  root.setAttribute("aria-busy", String(account.loading || !account.ready));
  if (!account.ready) { root.innerHTML = '<p class="account-loading">正在准备你的文献库…</p>'; return; }
  if (!user) {
    return renderLogin();
  }
  const healthy = account.syncState === "synced";
  const savedLabel = account.devMode ? "已保存到本地开发账号" : "已同步到账号";
  const profileLink = account.devMode
    ? '<span class="account-provider">本地开发 · local-developer</span>'
    : `<a href="https://github.com/${encodeURIComponent(user.login)}" target="_blank" rel="noopener noreferrer">@${esc(user.login)} ${icon("arrow-up-right", true)}</a>`;
  root.innerHTML = `
    <header class="account-profile"><span class="account-emblem">${icon("circle-user-round")}</span><div><h2>${esc(user.name || user.login)}</h2>${profileLink}</div></header>
    <div class="account-sync"><p id="accountSyncStatus" role="status">${icon(healthy ? "cloud-check" : "cloud-off", true)} ${account.loading ? '正在同步文献库…' : healthy ? savedLabel + ' · ' + new Date(account.syncedAt).toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit'}) : esc(account.error || '暂时显示上次记录')}</p>${account.expired ? `<a class="account-login" href="${esc(loginHref())}">重新登录 ${icon("arrow-up-right", true)}</a>` : '<button type="button" class="text-btn" id="accountSync">重新同步</button>'}</div>
    <div class="account-library-links"><a href="#library"><strong>${saved.size}</strong><span>已收藏 ${icon("arrow-up-right", true)}</span></a><a href="#library?mode=unread"><strong>${[...saved].filter(id => !read.has(id)).length}</strong><span>待阅读 ${icon("arrow-up-right", true)}</span></a><a href="#library?mode=read"><strong>${read.size}</strong><span>已读 ${icon("arrow-up-right", true)}</span></a></div>
    <div class="account-continue"><a class="account-login" href="#library">打开我的文献库 ${icon("arrow-right", true)}</a><a class="text-btn" href="${esc(loginReturn())}">返回研究工作区</a><a class="text-btn" href="#library?mode=visited">最近浏览</a></div>
    ${importPanel()}${privacy()}<div class="account-signout"><span>退出后返回首页，本机文献记录会保留。</span><button type="button" class="text-btn" id="accountLogout">退出登录</button></div>`;
  $("#accountSync")?.addEventListener("click", async event => {
    event.currentTarget.disabled = true; event.currentTarget.textContent = "正在同步…";
    await refreshAccount();
  });
  $("#importLocal")?.addEventListener("click", async event => {
    event.currentTarget.disabled = true;
    $("#importStatus").textContent = "正在导入，请稍候…";
    try { await importLocal(); renderAccount(); }
    catch (error) {
      $("#importStatus").textContent = error.message + " 已成功导入的记录已保留，可以重试。";
      event.target.disabled = false;
    }
  });
  $("#dismissImport")?.addEventListener("click", () => {
    try { localStorage.setItem(importKey(), importSignature(pendingLocal())); } catch {}
    $(".account-import")?.remove();
  });
  $("#accountLogout")?.addEventListener("click", async event => {
    event.currentTarget.disabled = true;
    try {
      changing++;
      try { await request("/api/account/logout", {}); }
      catch (error) { if (error.status !== 401) throw error; }
      const prefix = "pc-user-" + account.user.id;
      for (const kind of ['saved', 'read', 'visited', 'import-dismissed']) {
        try { localStorage.removeItem(prefix + '-' + kind); } catch {}
      }
      account.ready = false; account.sessionKnown = false;
      location.replace("/#welcome");
    } catch (error) { notify(error.message); event.target.disabled = false; }
    finally { changing--; }
  });
  $("#analyticsPreference").onchange = event => analyticsOptOut(!event.target.checked);
  bindLoginLinks(root);
  refreshIcons(root);
}
// Recheck after returning from another tab/device interaction. No polling while
// hidden, and no refresh during writes. Keep cached records if the network fails.
export function recheckAccount() {
  if (document.visibilityState === "hidden" || Date.now() - lastChecked <= 30000 || changing || initialization)
    return Promise.resolve();
  const before = JSON.stringify([account.user?.id, account.syncState, [...saved], [...read]]);
  return initAccount({ force: true }).then(() => {
    const after = JSON.stringify([account.user?.id, account.syncState, [...saved], [...read]]);
    if (before !== after && ["account", "login", "library", "discover", "paper"].includes(state.view)) return render();
  });
}
