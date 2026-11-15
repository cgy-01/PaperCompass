import {
  state,
  saved,
  read,
  topicCatalog,
  sources,
  updateCatalog,
} from "./state.js";
import { api, cancelApiChannel } from "./api.js";
import { configureActions } from "./actions.js";
import { initAccount, persistCollections, recheckAccount, renderAccountButton } from "./account.js";
import { trackPage } from "./analytics.js";
import { createRouter, routeHash } from "./router.js";
import {
  $,
  $$,
  esc,
  icon,
  refreshIcons,
  enhanceSelects,
  syncCustomSelects,
  closeCustomSelects,
  empty,
} from "./components/ui.js";
import { setWelcomeMotionActive } from "./pages/welcome-motion.js";
import { initTypography } from "./typography.js";
import { bindSearch } from "./search.js";

const navItems = [
  ["overview", "总览"],
  ["discover", "发现"],
  ["venues", "期刊 / 会议"],
  ["topics", "研究主题"],
  ["library", "我的文献库"],
  ["docs", "使用文档"],
];
const navIconNames = {
  overview: "house",
  discover: "search",
  venues: "layout-grid",
  topics: "circle-dot",
  library: "star",
  docs: "book-open-text",
};
const mobileNavLabels = {
  overview: "总览",
  discover: "发现",
  venues: "期刊<br>会议",
  topics: "研究<br>主题",
  library: "我的<br>文献库",
  docs: "使用<br>文档",
};
export function navMarkup(mobile = false) {
  return navItems
    .map(
      ([id, label]) =>
        `<button data-go="${id}" aria-label="${label}" ${state.view === id ? 'aria-current="page"' : ''} class="${state.view === id ? "active" : ""}"><span class="icon-wrap nav-icon-wrap" aria-hidden="true"><i data-lucide="${navIconNames[id]}"></i></span>${mobile ? `<span class="nav-label">${mobileNavLabels[id]}</span>` : label}${!mobile && id === "library" ? `<small>${saved.size}</small>` : ""}</button>`,
    )
    .join("");
}
export function saveStores() {
  persistCollections();
  const count = $('#nav [data-go="library"] small');
  if (count && count.textContent !== String(saved.size)) count.textContent = saved.size;
}
export function bindGo() {
  $$("[data-go]").forEach((b) => (b.onclick = () => go(b.dataset.go)));
}
export function syncFilters() {
  if ($("#typeFilter")) {
    $("#typeFilter").value = state.type;
    $("#themeFilter").value = state.theme;
    $("#sourceFilter").value = state.source;
    $("#dateFilter").value = state.days;
    $("#sortFilter").value = state.sort;
    $("#globalSearch").value = state.query;
    $("#libraryFilter").value = state.libraryMode;
    syncCustomSelects();
  }
}

let renderedView = state.view;
let appMounted = false;
const catalogViews = new Set([
  "overview",
  "discover",
  "venues",
  "topics",
  "library",
]);
let catalogPromise,
  catalogReady = false;
let filterTopics, filterSources;
function prepareWorkspaceFilters() {
  if (filterTopics !== topicCatalog || filterSources !== sources) {
    filterTopics = topicCatalog;
    filterSources = sources;
    $("#themeFilter").innerHTML = '<option value="">全部主题</option>' + topicCatalog
      .map(t => t.name).sort().map(x => `<option>${esc(x)}</option>`).join("");
    $("#sourceFilter").innerHTML = '<option value="">全部来源</option>' + sources
      .map(x => x.name).sort().map(x => `<option>${esc(x)}</option>`).join("");
    enhanceSelects();
  }
}
function ensureCatalog() {
  if (!catalogPromise)
    catalogPromise = refreshCatalog()
      .then(() => {
        catalogReady = true;
      })
      .catch((error) => {
        catalogPromise = null;
        throw error;
      });
  return catalogPromise;
}
const router = createRouter(async (route, { initial }) => {
  const focusOrigin = document.activeElement;
  const moveFocus = !initial && renderedView !== route.view && focusOrigin?.matches(":focus-visible") &&
    focusOrigin.closest("nav,.topic-row,.venue-row,.detail-nav,.brand");
  if (!initial && renderedView !== "paper" && route.view === "paper")
    history.replaceState({ pcDetailOrigin: true }, "");
  if (renderedView !== route.view) cancelApiChannel(renderedView);
  Object.assign(state, route);
  if (!["account", "login"].includes(route.view)) {
    try { sessionStorage.setItem("pc-login-return", routeHash(route)); } catch {}
  }
  renderAccountButton();
  renderedView = route.view;
  document.body.classList.toggle("is-welcome", route.view === "welcome");
  setWelcomeMotionActive(route.view === "welcome");
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === state.view));
  $("#nav").innerHTML = navMarkup();
  $("#mobileNav").innerHTML = navMarkup(true);
  bindGo();
  refreshIcons($("#nav"));
  refreshIcons($("#mobileNav"));
  syncFilters();
  document.title =
    route.view === "welcome"
      ? "Paper Compass · 找到你的研究方向"
      : `${{ overview: "研究总览", discover: route.theme ? route.theme + " · 发现论文" : "发现论文", venues: "期刊与会议", topics: "研究主题", library: "我的文献库", docs: "使用文档", account: "我的账号", login: "登录", paper: "论文详情" }[route.view]} · Paper Compass`;
  const notice = $("#workspaceNotice");
  notice.hidden = true;
  try {
    if (catalogViews.has(route.view) && !catalogReady) {
      notice.textContent = "正在准备文献工作区…";
      notice.hidden = false;
      await ensureCatalog();
      if (routeHash(route) !== routeHash(state)) return;
      notice.hidden = true;
    }
    if (catalogViews.has(route.view)) {
      prepareWorkspaceFilters();
      syncFilters();
    }
    await render();
    if (routeHash(route) !== routeHash(state)) return;
    if (moveFocus || (!initial && route.view === "login")) {
      const heading = document.getElementById(route.view)?.querySelector("h1");
      if (heading) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); }
    }
  } catch (error) {
    if (routeHash(route) !== routeHash(state)) return;
    notice.textContent =
      error.status === 503
        ? "文献服务正在准备，请稍后重试。"
        : "文献服务暂时无法连接，请重试。";
    const retry = document.createElement("button");
    retry.className = "text-btn";
    retry.textContent = "重试";
    retry.onclick = () => commitRoute({ replace: true });
    notice.appendChild(retry);
    notice.hidden = false;
  }
  appMounted = true;
  trackPage(state.view === "login" ? { ...state, view: "account" } : state);
});
export function commitRoute(options) {
  return router.navigate(routeHash(state), options);
}
export function go(id) {
  state.view = id;
  state.page = 1;
  commitRoute();
}
export function openDiscover(filters) {
  Object.assign(
    state,
    {
      view: "discover",
      page: 1,
      type: "",
      theme: "",
      source: "",
      days: "",
      sort: "date",
      query: "",
    },
    filters,
  );
  commitRoute();
}
const pageLoaders = {
  welcome: () => import("./pages/welcome.js"),
  overview: () => import("./pages/home.js"),
  discover: () => import("./pages/discover.js"),
  venues: () => import("./pages/catalog.js"),
  topics: () => import("./pages/catalog.js"),
  library: () => import("./pages/library.js"),
  docs: () => import("./pages/docs.js"),
  paper: () => import("./pages/paper.js"),
  account: () => import("./account.js"),
  login: () => import("./account.js"),
};
const pageExports = {
  welcome: "renderWelcome", overview: "renderOverview", discover: "renderDiscover",
  venues: "renderVenues", topics: "renderTopics", library: "renderLibrary",
  docs: "renderDocs", paper: "renderPaper", account: "renderAccount", login: "renderLogin",
};
const loadedPages = new Map();
export async function render() {
  if (catalogViews.has(state.view) && !catalogReady) return;
  const view = state.view;
  let page = loadedPages.get(view);
  if (!page) {
    page = await pageLoaders[view]?.();
    loadedPages.set(view, page);
  }
  if (view !== state.view) return;
  const result = page?.[pageExports[view]]();
  refreshIcons(document.getElementById(view === "overview" ? "overview" : view));
  await result;
}
export async function refreshCatalog() {
  const data = await api("/api/v1/bootstrap", { ttl: 0 });
  updateCatalog(data);
  if (data.dataUpdatedAt) {
    const stamp = $("#generated");
    stamp.dateTime = data.dataUpdatedAt;
    stamp.textContent =
      new Intl.DateTimeFormat("sv-SE", {
        timeZone: "Asia/Shanghai",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      }).format(new Date(data.dataUpdatedAt)) + "（UTC+8）";
  }
}
async function init() {
  $("#skipContent").onclick = event => {
    event.preventDefault();
    const heading = document.getElementById(state.view)?.querySelector("h1");
    if (heading) { heading.tabIndex = -1; heading.focus(); }
  };
  for (const [id, key] of [
    ["typeFilter", "type"],
    ["themeFilter", "theme"],
    ["sourceFilter", "source"],
    ["dateFilter", "days"],
    ["sortFilter", "sort"],
  ])
    $("#" + id).onchange = (e) => {
      state[key] = e.target.value;
      state.page = 1;
      commitRoute();
    };
  bindSearch($("#globalSearch"), query => {
      state.query = query;
      state.page = 1;
      state.view = "discover";
      commitRoute();
  });
  document.addEventListener("keydown", event => {
    if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey &&
        state.view !== "login" &&
        !event.target.closest("input,textarea,select,[contenteditable],[role=combobox]")) {
      event.preventDefault(); $("#globalSearch").focus();
    }
  });
  $("#clearBtn").onclick = () => {
    Object.assign(state, {
      page: 1,
      type: "",
      theme: "",
      source: "",
      days: "",
      sort: "date",
      query: "",
    });
    syncFilters();
    commitRoute();
  };
  $("#libraryFilter").onchange = (e) => {
    state.libraryMode = e.target.value;
    commitRoute();
  };
  document.addEventListener("click", () => closeCustomSelects());
  // Warm the next screen on explicit intent, without competing with first paint.
  const warmWorkspace = () => {
    if (navigator.connection?.saveData || loadedPages.has("overview")) return;
    void pageLoaders.overview().then(page => loadedPages.set("overview", page)).catch(() => {});
  };
  document.querySelectorAll('a[data-track="enter_workspace"]').forEach(link => {
    for (const event of ["pointerenter", "focusin", "pointerdown"])
      link.addEventListener(event, warmWorkspace, { once: true, passive: true });
  });
  await router.start();
  if (state.view === "welcome")
    void ensureCatalog()
      .then(() => {
        if (state.view === "welcome") return render();
      })
      .catch(() => {});
}
configureActions({
  commitRoute,
  saveStores,
  render,
  openDiscover,
  refreshCatalog,
  openPaper(identifier) {
    if (!/^[a-f0-9]{64}$/.test(identifier)) return;
    state.view = "paper";
    state.paperId = identifier;
    return commitRoute();
  },
});
// Render static shell icons once. Later renders only touch their own subtree.
refreshIcons(document);
initTypography();
init();
initAccount().then(() => {
  // Account chrome updates itself. Do not rebuild an unrelated landing page.
  if (appMounted && ["account", "login", "library", "discover", "paper"].includes(state.view)) render();
});
document.addEventListener("visibilitychange", () => void recheckAccount());
window.addEventListener("focus", () => void recheckAccount());
