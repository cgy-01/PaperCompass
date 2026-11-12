import { paperIndex, saved } from "../state.js";
import { esc, date, icon } from "./ui.js";
import { saveStores, render } from "../actions.js";
import { openPaper } from "../pages/paper.js";
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
    )}</div><button class="save ${saved.has(p.id) ? "on" : ""}" title="${saved.has(p.id) ? "取消收藏" : "收藏"}" aria-label="${saved.has(p.id) ? "取消收藏" : "收藏"}">${icon("star")}</button></div><h3><a href="${paperHref(p)}">${esc(p.title)}</a></h3><div class="authors">${esc(p.authors)}</div><p class="abstract">${esc(p.summaryPreview || p.summary)}</p><div class="paper-foot"><span>${esc(p.source)} · ${date(p.published)}</span><span class="score">${p.score ? Math.round(p.score * 100) + "%" : "—"}</span></div></article>`;
}
export function bindCards(root) {
  root.querySelectorAll(".save").forEach(
    (b) =>
      (b.onclick = (e) => {
        e.stopPropagation();
        const id = b.closest(".paper").dataset.id;
        saved.has(id) ? saved.delete(id) : saved.add(id);
        saveStores();
        render();
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
