import { state } from "../state.js";
import { api } from "../api.js";
import { $, $$, empty, pageNumbers, refreshIcons } from "../components/ui.js";
import { remember, card, bindCards } from "../components/papers.js";
import { commitRoute, refreshCatalog } from "../actions.js";
let discoverRequest = 0;
export async function renderDiscover() {
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
  $("#paperGrid").innerHTML = empty("正在读取论文…");
  try {
    if (state.dataVersion) params.set("version", state.dataVersion);
    const data = await api("/api/v1/papers?" + params, { channel: "discover" });
    if (request !== discoverRequest) return;
    state.page = data.page;
    const papers = remember(data.items);
    $("#resultCount").textContent =
      `找到 ${data.total.toLocaleString()} 篇论文`;
    $("#paperGrid").innerHTML =
      papers.map(card).join("") || empty("没有符合条件的论文");
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
    if (error.status === 409) {
      await refreshCatalog();
      return renderDiscover();
    }
    $("#paperGrid").innerHTML = empty("论文读取失败：" + error.message);
  }
}
