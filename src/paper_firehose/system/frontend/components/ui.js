export const $ = (s) => document.querySelector(s),
  $$ = (s) => [...document.querySelectorAll(s)];
export const esc = (s) =>
  String(s || "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
export const date = (s) => {
  if (!s) return "日期未知";
  const d = new Date(s);
  return isNaN(d) ? s.slice(0, 10) : d.toLocaleDateString("zh-CN");
};
export function icon(name, small = false) {
  return `<span class="icon-wrap${small ? " small" : ""}" aria-hidden="true"><i data-lucide="${name}"></i></span>`;
}
export function refreshIcons(root = document) {
  if (window.lucide)
    lucide.createIcons({
      icons: lucide.icons,
      root,
      attrs: { width: 20, height: 20, "stroke-width": 1.75 },
    });
}
export function closeCustomSelects(except = null) {
  $$(".custom-select.open").forEach((wrap) => {
    if (wrap !== except) {
      wrap.classList.remove("open");
      wrap
        .querySelector(".custom-select-trigger")
        ?.setAttribute("aria-expanded", "false");
    }
  });
}
export function refreshCustomSelect(select, rebuild = true) {
  const wrap = select.closest(".custom-select");
  if (!wrap) return;
  if (rebuild) {
    const menu = wrap.querySelector(".custom-select-menu");
    menu.innerHTML = [...select.options]
      .map(
        (option, index) =>
          `<button type="button" class="custom-select-option${index === select.selectedIndex ? " selected" : ""}" data-option-index="${index}">${esc(option.textContent)}</button>`,
      )
      .join("");
    menu.querySelectorAll("[data-option-index]").forEach(
      (button) =>
        (button.onclick = (event) => {
          event.stopPropagation();
          select.selectedIndex = Number(button.dataset.optionIndex);
          select.dispatchEvent(new Event("change", { bubbles: true }));
          refreshCustomSelect(select, false);
          wrap.classList.remove("open");
          wrap
            .querySelector(".custom-select-trigger")
            .setAttribute("aria-expanded", "false");
        }),
    );
  }
  wrap.querySelector(".custom-select-label").textContent =
    select.options[select.selectedIndex]?.textContent || "";
  wrap
    .querySelectorAll("[data-option-index]")
    .forEach((button, index) =>
      button.classList.toggle("selected", index === select.selectedIndex),
    );
}
export function enhanceSelect(select) {
  if (select.closest(".custom-select")) {
    refreshCustomSelect(select, true);
    return;
  }
  const wrap = document.createElement("div");
  wrap.className = "custom-select";
  select.parentNode.insertBefore(wrap, select);
  wrap.appendChild(select);
  wrap.insertAdjacentHTML(
    "beforeend",
    `<button type="button" class="custom-select-trigger" aria-haspopup="listbox" aria-expanded="false"><span class="custom-select-label"></span><i data-lucide="chevron-down" aria-hidden="true"></i></button><div class="custom-select-menu" role="listbox"></div>`,
  );
  const trigger = wrap.querySelector(".custom-select-trigger");
  trigger.onclick = (event) => {
    event.stopPropagation();
    const opening = !wrap.classList.contains("open");
    closeCustomSelects(wrap);
    wrap.classList.toggle("open", opening);
    trigger.setAttribute("aria-expanded", String(opening));
  };
  trigger.onkeydown = (event) => {
    if (event.key === "Escape") {
      wrap.classList.remove("open");
      trigger.setAttribute("aria-expanded", "false");
    }
  };
  select.addEventListener("change", () => refreshCustomSelect(select, false));
  refreshCustomSelect(select, true);
  refreshIcons(wrap);
}
export function enhanceSelects(root = document) {
  root.querySelectorAll("select").forEach(enhanceSelect);
}
export function syncCustomSelects() {
  document
    .querySelectorAll(".custom-select>select")
    .forEach((select) => refreshCustomSelect(select, false));
}
export function empty(s) {
  return `<div class="library-empty"><div class="empty-icon">${icon("book-open")}</div><p>${esc(s)}</p></div>`;
}
export function pageNumbers(current, total) {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const values = new Set([1, total]);
  if (current <= 4) {
    for (let i = 2; i <= 5; i++) values.add(i);
  } else if (current >= total - 3) {
    for (let i = total - 4; i < total; i++) values.add(i);
  } else {
    for (let i = current - 2; i <= current + 2; i++) values.add(i);
  }
  const sorted = [...values].sort((a, b) => a - b),
    result = [];
  sorted.forEach((page, i) => {
    if (i && page - sorted[i - 1] > 1) result.push("…");
    result.push(page);
  });
  return result;
}
