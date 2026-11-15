import { paperIndex, saved, read, visited, state } from "../state.js";
import { esc, date, icon } from "./ui.js";
import { render, openPaper } from "../actions.js";
import { toggleFavorite } from "../account.js";
export function paperHref(p) {
  return "#paper/" + p.publicId;
}
export function remember(items) {
  items.forEach((p) => {
    paperIndex.delete(p.id);
    paperIndex.set(p.id, p);
  });
  while (paperIndex.size > 180)
    paperIndex.delete(paperIndex.keys().next().value);
  return items;
}
export function card(p) {
  return `<article class="paper" data-id="${esc(p.id)}" data-public-id="${p.publicId}"><div class="paper-top"><div class="badges"><span class="badge type">${esc(p.venueType)}</span>${(
    p.topics || []
  )
    .slice(0, 2)
    .map((t) => `<span class="badge">${esc(t)}</span>`)
    .join(
      "",
    )}</div><button class="save ${saved.has(p.id) ? "on" : ""}" title="${saved.has(p.id) ? "取消收藏" : "收藏"}" aria-label="${saved.has(p.id) ? "取消收藏" : "收藏"}" aria-pressed="${saved.has(p.id)}">${icon("star")}</button></div><h3><a href="${paperHref(p)}">${esc(p.title)}</a></h3><div class="authors">${esc(p.authors)}</div><p class="abstract">${esc(p.summaryPreview || p.summary)}</p><div class="paper-foot"><span>${esc(p.source)} · ${date(p.published)}</span><span class="paper-states">${read.has(p.id) ? '<span class="read-state">已读</span>' : visited.has(p.id) ? '<span class="read-state">已浏览</span>' : ''}<span class="score" title="根据研究主题关键词计算">主题匹配度 ${p.score ? Math.round(p.score * 100) + "%" : "—"}</span></span></div></article>`;
}
export function bindCards(root) {
  root.querySelectorAll(".save").forEach(
    (b) =>
      (b.onclick = async (e) => {
        e.stopPropagation();
        const id = b.closest(".paper").dataset.id;
        b.disabled = true;
        try {
          await toggleFavorite(id);
          if (state.view === "library") {
            render();
          } else {
            // A star toggle does not invalidate a discovery query or its cards.
            const enabled = saved.has(id), label = enabled ? "取消收藏" : "收藏";
            b.classList.toggle("on", enabled);
            b.title = label;
            b.setAttribute("aria-label", label);
            b.setAttribute("aria-pressed", String(enabled));
          }
        } catch {
        } finally {
          b.disabled = false;
        }
      }),
  );
  root.querySelectorAll(".paper").forEach(
    (c) =>
      (c.onclick = (e) => {
        if (e.target.closest("a,button")) return;
        openPaper(c.dataset.publicId);
      }),
  );
}
