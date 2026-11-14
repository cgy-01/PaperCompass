import { saved, read, state } from "./state.js";
import { $, esc, icon, refreshIcons } from "./components/ui.js";
import { saveStores } from "./actions.js";
import {
  setAnalytics,
  analyticsOptOut,
  analyticsDisabled,
} from "./analytics.js";

export const account = {
  user: null,
  csrfToken: "",
  loginEnabled: false,
  eventsEnabled: false,
  retentionDays: 90,
  ready: false,
};
let initialization;
const inFlight = new Set();
function localSet(key) {
  try {
    const items = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(items)
      ? items.filter((x) => typeof x === "string")
      : [];
  } catch {
    return [];
  }
}
export function notify(message) {
  let node = $("#accountNotice");
  if (!node) {
    node = document.createElement("div");
    node.id = "accountNotice";
    node.className = "account-notice";
    node.setAttribute("role", "status");
    document.body.appendChild(node);
  }
  node.textContent = message;
  node.hidden = false;
  clearTimeout(node.timer);
  node.timer = setTimeout(() => (node.hidden = true), 5000);
}
async function request(path, body) {
  const controller = new AbortController(),
    timer = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(path, {
      cache: "no-store",
      signal: controller.signal,
      ...(body
        ? {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRF-Token": account.csrfToken,
            },
            body: JSON.stringify(body),
          }
        : {}),
    });
    if (!response.ok)
      throw new Error(
        response.status === 401
          ? "登录已过期，请重新登录"
          : "账号操作暂未完成，请重试",
      );
    return response.json();
  } finally {
    clearTimeout(timer);
  }
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
  const prefix = account.user ? "pc-user-" + account.user.id : "pc";
  try {
    localStorage.setItem(prefix + "-saved", JSON.stringify([...saved]));
    localStorage.setItem(prefix + "-read", JSON.stringify([...read]));
  } catch {}
}
export function renderAccountButton() {
  const root = $("#accountSlot");
  if (!root) return;
  root.innerHTML = `<a class="account-entry" href="#account">${icon(account.user ? "circle-user-round" : "user-round", true)}<span>${esc(account.user?.login || "登录")}</span></a>`;
  refreshIcons(root);
}
export function initAccount() {
  if (initialization) return initialization;
  initialization = (async () => {
    try {
      Object.assign(account, await request("/api/account"));
      if (account.user) {
        saved.clear();
        read.clear();
        localSet("pc-user-" + account.user.id + "-saved").forEach((id) =>
          saved.add(id),
        );
        localSet("pc-user-" + account.user.id + "-read").forEach((id) =>
          read.add(id),
        );
        const [favorites, reading] = await Promise.all([
          ids("favorites"),
          ids("reading"),
        ]);
        saved.clear();
        read.clear();
        favorites.forEach((id) => saved.add(id));
        reading.forEach((id) => read.add(id));
      }
      setAnalytics(account.eventsEnabled);
    } catch {
      notify("账号服务暂不可用，仍可浏览论文");
    } finally {
      account.ready = true;
      renderAccountButton();
      saveStores();
    }
  })();
  return initialization;
}
export async function toggleFavorite(id) {
  await initAccount();
  if (inFlight.has(id)) return saved.has(id);
  inFlight.add(id);
  const enabled = !saved.has(id);
  try {
    if (account.user)
      await request("/api/account/favorites", { ids: [id], enabled });
    enabled ? saved.add(id) : saved.delete(id);
    saveStores();
    return enabled;
  } catch (error) {
    notify(error.message);
    throw error;
  } finally {
    inFlight.delete(id);
  }
}
export async function markRead(id) {
  await initAccount();
  try {
    if (account.user)
      await request("/api/account/reading", { ids: [id], enabled: true });
    read.add(id);
    saveStores();
  } catch (error) {
    notify(error.message);
  }
}
async function importLocal() {
  const favorites = localSet("pc-saved"),
    reading = localSet("pc-read");
  for (const [kind, items] of [
    ["favorites", favorites],
    ["reading", reading],
  ])
    for (let offset = 0; offset < items.length; offset += 250)
      await request("/api/account/" + kind, {
        ids: items.slice(offset, offset + 250),
        enabled: true,
      });
  favorites.forEach((id) => saved.add(id));
  reading.forEach((id) => read.add(id));
  saveStores();
  notify("本机文献记录已导入");
  renderAccount();
}
export function renderAccount() {
  const root = $("#accountBody"),
    user = account.user;
  root.innerHTML = `<div class="account-profile">${icon(user ? "circle-user-round" : "user-round")}<div><h2>${esc(user?.name || "你的研究文献库")}</h2><p>${user ? "@" + esc(user.login) : "使用 GitHub 登录，在不同设备上同步收藏与阅读记录。"}</p></div></div>
    <div class="account-controls">${user ? `<p>已同步 <b>${saved.size}</b> 篇收藏 · <b>${read.size}</b> 篇已读</p><button class="icon-btn" id="importLocal">导入本机文献记录</button><button class="icon-btn" id="accountLogout">退出登录</button>` : account.loginEnabled ? `<a class="account-login" href="/auth/github/login?return_to=${encodeURIComponent("#account")}">${icon("github", true)} 使用 GitHub 登录</a>` : '<button class="icon-btn" disabled>GitHub 登录暂不可用</button>'}</div>
    <section class="account-privacy"><h3>使用情况记录</h3><p>记录页面访问、论文点击、筛选、搜索次数与停留时间，用于改进产品。搜索内容和输入框文字不会记录。行为明细保留 ${account.retentionDays} 天，更早的数据仅保留汇总统计。登录后的记录会关联你的平台账号。</p><label><input type="checkbox" id="analyticsPreference" ${!analyticsDisabled() ? "checked" : ""} ${!account.eventsEnabled ? "disabled" : ""}> 允许记录此浏览器的产品使用情况</label><p>${user ? "账号收藏与已读记录会持续保存。" : "未登录的文献记录仍保存在本机浏览器。"}</p></section>`;
  $("#importLocal")?.addEventListener("click", async (event) => {
    event.currentTarget.disabled = true;
    try {
      await importLocal();
    } catch (error) {
      notify(error.message);
      event.target.disabled = false;
    }
  });
  $("#accountLogout")?.addEventListener("click", async () => {
    try {
      await request("/api/account/logout", {});
      try {
        localStorage.removeItem("pc-user-" + account.user.id + "-saved");
        localStorage.removeItem("pc-user-" + account.user.id + "-read");
      } catch {}
      location.reload();
    } catch (error) {
      notify(error.message);
    }
  });
  $("#analyticsPreference").onchange = (event) =>
    analyticsOptOut(!event.target.checked);
  refreshIcons(root);
}
