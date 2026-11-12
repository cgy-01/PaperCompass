import {
  state,
  saved,
  read,
  topicCatalog,
  sources,
  updateCatalog,
} from "./state.js";
import { api } from "./api.js";
import { configureActions } from "./actions.js";
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
import { renderOverview } from "./pages/home.js";
import { renderDiscover } from "./pages/discover.js";
import { renderVenues, renderTopics } from "./pages/catalog.js";
import { renderLibrary } from "./pages/library.js";
import { renderDocs } from "./pages/docs.js";
import { renderPaper } from "./pages/paper.js";

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
export function navMarkup(mobile = false) {
  return navItems
    .map(
      ([id, label]) =>
        `<button data-go="${id}" class="${state.view === id ? "active" : ""}"><span class="icon-wrap nav-icon-wrap" aria-hidden="true"><i data-lucide="${navIconNames[id]}"></i></span>${label}${!mobile && id === "library" ? `<small>${saved.size}</small>` : ""}</button>`,
    )
    .join("");
}
export function saveStores() {
  localStorage.setItem("pc-saved", JSON.stringify([...saved]));
  localStorage.setItem("pc-read", JSON.stringify([...read]));
  $("#nav").innerHTML = navMarkup();
  bindGo();
  refreshIcons($("#nav"));
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
const router = createRouter(async (route, { initial }) => {
  if (!initial && renderedView !== "paper" && route.view === "paper")
    history.replaceState({ pcDetailOrigin: true }, "");
  Object.assign(state, route);
  renderedView = route.view;
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === state.view));
  $("#nav").innerHTML = navMarkup();
  $("#mobileNav").innerHTML = navMarkup(true);
  bindGo();
  syncFilters();
  document.title = "Paper Compass · AI 研究工作台";
  await render();
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
export async function render() {
  const pages = {
    overview: renderOverview,
    discover: renderDiscover,
    venues: renderVenues,
    topics: renderTopics,
    library: renderLibrary,
    docs: renderDocs,
    paper: renderPaper,
  };
  const result = pages[state.view]?.();
  refreshIcons();
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
  try {
    await refreshCatalog();
    $("#themeFilter").innerHTML += topicCatalog
      .map((t) => t.name)
      .sort()
      .map((x) => `<option>${esc(x)}</option>`)
      .join("");
    $("#sourceFilter").innerHTML += sources
      .map((x) => x.name)
      .sort()
      .map((x) => `<option>${esc(x)}</option>`)
      .join("");
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
    let searchTimer;
    $("#globalSearch").oninput = (e) => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        state.query = e.target.value.trim();
        state.page = 1;
        state.view = "discover";
        commitRoute();
      }, 250);
    };
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
    enhanceSelects();
    await router.start();
  } catch (error) {
    if (error.status === 503) {
      setTimeout(init, 2000);
      return;
    }
    $(".content").innerHTML = empty("无法连接论文数据服务：" + error.message);
  }
}
configureActions({
  commitRoute,
  saveStores,
  render,
  openDiscover,
  refreshCatalog,
});
init();
