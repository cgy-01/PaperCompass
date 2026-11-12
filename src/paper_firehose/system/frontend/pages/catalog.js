import { state, sources, topicCatalog } from "../state.js";
import { $, $$, esc, icon, empty, refreshIcons } from "../components/ui.js";
import { openDiscover, commitRoute } from "../actions.js";
export function renderVenues() {
  const selected = state.venueType,
    list = selected ? sources.filter((x) => x.type === selected) : sources;
  $("#venueTabs").innerHTML = ["全部", "期刊", "会议", "预印本"]
    .map(
      (t) =>
        `<button class="icon-btn ${selected === (t === "全部" ? "" : t) ? "active" : ""}" data-vtype="${t === "全部" ? "" : t}">${t}</button>`,
    )
    .join("");
  const head = `<div class="venue-directory-head"><span>来源</span><span>类型</span><span style="text-align:right">收录论文</span><span></span></div>`;
  $("#venueGrid").innerHTML = list.length
    ? head +
      list
        .map(
          (item) =>
            `<div class="venue-row" data-source="${esc(item.name)}" data-source-type="${esc(item.type)}"><h3>${esc(item.name)}</h3><span class="venue-kind">${esc(item.type)}</span><span class="venue-count">${item.count.toLocaleString()} 篇</span><span class="venue-arrow">${icon("arrow-right", true)}</span></div>`,
        )
        .join("")
    : empty("当前还没有这一类型的来源");
  $$("[data-vtype]").forEach(
    (b) =>
      (b.onclick = () => {
        state.venueType = b.dataset.vtype;
        commitRoute();
      }),
  );
  $$("[data-source]").forEach(
    (c) =>
      (c.onclick = () =>
        openDiscover({ source: c.dataset.source, type: c.dataset.sourceType })),
  );
  refreshIcons($("#venues"));
}
export function renderTopics() {
  $("#topicGrid").innerHTML = topicCatalog
    .map(
      (t) =>
        `<div class="topic-row" data-theme="${esc(t.name)}"><div class="topic-icon"><i data-lucide="${esc(t.icon)}"></i></div><div class="topic-copy"><h3>${esc(t.name)}</h3><p>${esc(t.description)}</p></div><div class="topic-total"><b>${(t.count || 0).toLocaleString()}</b>篇论文</div></div>`,
    )
    .join("");
  $$("[data-theme]").forEach(
    (c) => (c.onclick = () => openDiscover({ theme: c.dataset.theme })),
  );
  refreshIcons($("#topics"));
}
