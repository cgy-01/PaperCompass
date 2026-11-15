import { state, sources, topicCatalog } from "../state.js";
import { $, $$, esc, icon, empty, refreshIcons } from "../components/ui.js";
import { commitRoute } from "../actions.js";
import { groupSources, discoverHref } from "../catalog-data.js";
const openGroups = new Set();
function sourceRow(item, label = item.name) {
  return `<a class="venue-row" href="${esc(discoverHref({ type: item.type, source: item.name }))}" data-source="${esc(item.name)}" data-source-type="${esc(item.type)}" aria-label="浏览 ${esc(item.name)} 的论文"><h3>${esc(label)}</h3><span class="venue-kind">${esc(item.type)}</span><span class="venue-count">${item.count.toLocaleString()} 篇</span><span class="venue-arrow">${icon("arrow-right", true)}</span></a>`;
}
export function renderVenues() {
  const selected = state.venueType,
    list = selected ? sources.filter((x) => x.type === selected) : sources;
  $("#venueTabs").innerHTML = ["全部", "会议", "期刊", "预印本"]
    .map(
      (t) =>
        `<button class="icon-btn ${selected === (t === "全部" ? "" : t) ? "active" : ""}" aria-pressed="${selected === (t === "全部" ? "" : t)}" data-vtype="${t === "全部" ? "" : t}">${t}</button>`,
    )
    .join("");
  const head = `<div class="venue-directory-head"><span>来源</span><span>类型</span><span style="text-align:right">收录论文</span><span></span></div>`;
  $("#venueGrid").innerHTML = list.length
    ? head +
      groupSources(list)
        .map(group => {
          if (group.items.length === 1 && group.name === group.items[0].name) return sourceRow(group.items[0]);
          const key = group.type + ":" + group.name;
          return `<details class="venue-series" data-family="${esc(key)}" ${openGroups.has(key) ? "open" : ""}><summary class="venue-row venue-summary"><span class="venue-name">${esc(group.name)}</span><span class="venue-kind">${esc(group.type)}</span><span class="venue-count">${group.count.toLocaleString()} 篇</span><span class="venue-arrow">${icon("chevron-down", true)}</span></summary><div class="venue-editions">${group.items.map(item => sourceRow(item, group.type === "会议" ? item.name.replace(group.name, "").trim() : item.name.replace(/^arxiv\s*/i, ""))).join("")}</div></details>`;
        })
        .join("")
    : empty("当前还没有这一类型的来源");
  $$("[data-vtype]").forEach(
    (b) =>
      (b.onclick = () => {
        state.venueType = b.dataset.vtype;
        commitRoute();
      }),
  );
  $("#venueGrid").querySelectorAll("details").forEach(details => details.addEventListener("toggle", () =>
    details.open ? openGroups.add(details.dataset.family) : openGroups.delete(details.dataset.family)));
  refreshIcons($("#venues"));
}
export function renderTopics() {
  $("#topicGrid").innerHTML = topicCatalog
    .map(
      (t) =>
        `<a class="topic-row" href="${esc(discoverHref({ theme: t.name }))}" data-theme="${esc(t.name)}"><div class="topic-icon"><i data-lucide="${esc(t.icon)}"></i></div><div class="topic-copy"><h3>${esc(t.name)}</h3><p>${esc(t.description)}</p></div><div class="topic-total"><b>${(t.count || 0).toLocaleString()}</b>篇论文</div></a>`,
    )
    .join("");
  refreshIcons($("#topics"));
}
