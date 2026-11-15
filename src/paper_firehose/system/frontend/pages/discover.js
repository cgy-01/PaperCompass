import { state } from "../state.js";
import { api } from "../api.js";
import { $, $$, esc, icon, empty, pageNumbers, refreshIcons } from "../components/ui.js";
import { remember, card, bindCards } from "../components/papers.js";
import { commitRoute, refreshCatalog } from "../actions.js";
let discoverRequest = 0;
function relax(key) {
  if (!["type", "theme", "source", "days", "query"].includes(key)) return;
  state[key] = "";
  state.page = 1;
  commitRoute();
}
function scope() {
  const labels = { type: "类型", theme: "主题", source: "来源", days: "时间", query: "关键词" };
  const time = { 30: "近 30 天", 90: "近 3 个月", 365: "近一年" };
  const root = $("#discoverScope");
  const active = Object.keys(labels).filter(key => state[key]);
  root.hidden = !active.length;
  root.innerHTML = active.length ? '<span>当前范围</span>' + active.map(key =>
    `<button type="button" class="scope-remove" data-relax="${key}" aria-label="移除${labels[key]}：${esc(state[key])}">${labels[key]}：${esc(key === "days" ? time[state[key]] : state[key])}${icon("x", true)}</button>`).join("") : "";
  root.querySelectorAll("[data-relax]").forEach(button => button.onclick = () => relax(button.dataset.relax));
  refreshIcons(root);
}
function recovery(root, message, retry = false) {
  const actions = [
    state.days ? ['days', '不限时间'] : null,
    state.theme ? ['theme', '移除主题限制'] : null,
    state.source ? ['source', '移除来源限制'] : null,
    state.query ? ['query', '清除关键词'] : null,
    state.type ? ['type', '不限论文类型'] : null,
  ].filter(Boolean);
  root.innerHTML = `<p>${esc(message)}</p><div class="recovery-actions">${retry ? '<button type="button" class="text-btn" data-retry>重试</button>' : ''}${actions.map(([key, label]) => `<button type="button" class="text-btn" data-relax="${key}">${label}</button>`).join("")}${!actions.length && !retry ? '<a href="#topics" class="text-btn">浏览研究主题</a>' : ''}</div>`;
  root.querySelector("[data-retry]")?.addEventListener("click", () => renderDiscover());
  root.querySelectorAll("[data-relax]").forEach(button => button.onclick = () => relax(button.dataset.relax));
}
export async function renderDiscover(retryVersion = true) {
  const request = ++discoverRequest,
    params = new URLSearchParams({
      page: state.page,
      page_size: 30,
      sort: state.sort,
    });
  [
    ["type", state.type],
    ["theme", state.theme],
    ["source", state.source],
    ["days", state.days],
    ["q", state.query],
  ].forEach(([k, v]) => {
    if (v) params.set(k, v);
  });
  const grid = $("#paperGrid"), feedback = $("#discoverFeedback");
  const retained = !!grid.querySelector(".paper");
  scope();
  grid.setAttribute("aria-busy", "true");
  feedback.textContent = retained ? "正在更新，暂时显示上次结果…" : "正在读取论文…";
  if (!retained) grid.innerHTML = "";
  $("#pagination").querySelectorAll("button,input").forEach(control => control.disabled = true);
  try {
    if (state.dataVersion) params.set("version", state.dataVersion);
    const data = await api("/api/v1/papers?" + params, { channel: "discover" });
    if (request !== discoverRequest || state.view !== "discover") return;
    state.page = data.page;
    const papers = remember(data.items);
    $("#resultCount").textContent =
      `找到 ${data.total.toLocaleString()} 篇论文`;
    grid.innerHTML =
      papers.map(card).join("") || empty("没有符合条件的论文");
    feedback.textContent = "";
    if (!papers.length) {
      recovery(grid, "没有找到匹配的论文，试着放宽一个筛选条件。");
      $("#pagination").innerHTML = "";
      return;
    }
    bindCards($("#paperGrid"));
    const pages = data.pages,
      numbered = pageNumbers(state.page, pages)
        .map((page) =>
          page === "…"
            ? '<button class="ellipsis" disabled>…</button>'
            : `<button class="${page === state.page ? "current" : ""}" data-page="${page}" ${page === state.page ? 'aria-current="page"' : ""}>${page}</button>`,
        )
        .join("");
    $("#pagination").innerHTML =
      `<button ${state.page === 1 ? "disabled" : ""} data-page="${state.page - 1}">上一页</button>${numbered}<button ${state.page === pages ? "disabled" : ""} data-page="${state.page + 1}">下一页</button><label class="page-jump">跳至 <input id="pageInput" type="number" min="1" max="${pages}" value="${state.page}" aria-label="输入页码"><button id="pageJump" type="button">跳转</button></label>`;
    const jump = (page) => {
      state.page = Math.max(1, Math.min(pages, Number(page) || 1));
      commitRoute();
    };
    $$("#pagination [data-page]").forEach(
      (b) => (b.onclick = () => jump(b.dataset.page)),
    );
    $("#pageJump").onclick = () => jump($("#pageInput").value);
    $("#pageInput").onkeydown = (e) => {
      if (e.key === "Enter") jump(e.currentTarget.value);
    };
    refreshIcons($("#discover"));
  } catch (error) {
    if (request !== discoverRequest || error.name === "AbortError") return;
    if (error.status === 409 && retryVersion) {
      await refreshCatalog();
      return renderDiscover(false);
    }
    recovery(feedback, retained ? "暂时未能更新，已保留上次结果。" : "论文暂时未能加载，请重试。", true);
  } finally {
    if (request === discoverRequest) grid.setAttribute("aria-busy", "false");
  }
}
