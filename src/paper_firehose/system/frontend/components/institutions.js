import { institutionCatalog } from "../state.js";
import { $, esc, refreshIcons } from "./ui.js";

function placeholder(name) {
  return `<span class="institution-placeholder" aria-hidden="true"><i data-lucide="landmark"></i></span><span class="sr-only">${esc(name)}</span>`;
}

export function renderInstitutions() {
  const root = $("#institutionList");
  if (!root || root.dataset.rendered) return;
  root.dataset.rendered = "true";
  root.innerHTML = institutionCatalog
    .filter((item) => item.logo)
    .map(
      (item) =>
        `<div class="institution-mark" title="${esc(item.name)}"><img src="${esc(item.logo)}" alt="${esc(item.name)}" loading="lazy"></div>`,
    )
    .join("");
  root.querySelectorAll("img").forEach((img) => {
    img.addEventListener(
      "error",
      () => {
        img.parentElement.innerHTML = placeholder(img.alt);
        refreshIcons(root);
      },
      { once: true },
    );
  });
}
