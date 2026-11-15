import { replaceIcons } from "../icons.js";

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
  replaceIcons(root);
}
const selectControllers = new WeakMap();
let selectSequence = 0;
export function closeCustomSelects(except = null) {
  $$(".custom-select.open").forEach(wrap => {
    if (wrap !== except) selectControllers.get(wrap.querySelector("select"))?.close(false);
  });
}
export function refreshCustomSelect(select, rebuild = true) {
  selectControllers.get(select)?.render(rebuild);
}
export function enhanceSelect(select) {
  if (selectControllers.has(select)) { refreshCustomSelect(select); return; }
  const wrap = document.createElement("div");
  wrap.className = "custom-select";
  select.parentNode.insertBefore(wrap, select);
  wrap.appendChild(select);
  const id = (select.id || "select-" + ++selectSequence) + "-picker";
  select.tabIndex = -1;
  select.setAttribute("aria-hidden", "true");
  wrap.insertAdjacentHTML("beforeend", `<button type="button" id="${id}-trigger" class="custom-select-trigger" role="combobox" aria-haspopup="listbox" aria-expanded="false" aria-controls="${id}"><span class="custom-select-label"></span><i data-lucide="chevron-down" aria-hidden="true"></i></button><div id="${id}" class="custom-select-menu" role="listbox"></div>`);
  const trigger = wrap.querySelector(".custom-select-trigger"), menu = wrap.querySelector(".custom-select-menu");
  const label = select.getAttribute("aria-label") || select.labels?.[0]?.textContent || "选择条件";
  trigger.setAttribute("aria-label", label);
  menu.setAttribute("aria-label", label);
  let signature, active = select.selectedIndex, typed = "", typedAt = 0;
  const available = () => [...select.options].map((option, index) =>
    option.disabled || option.parentElement.disabled ? -1 : index).filter(index => index >= 0);
  function render(rebuild = true) {
    const next = rebuild ? JSON.stringify([...select.options].map(option => [option.value, option.textContent, option.disabled])) : signature;
    if (next !== signature) {
      signature = next;
      menu.innerHTML = [...select.options].map((option, index) => `<div id="${id}-${index}" class="custom-select-option" role="option" aria-selected="${index === select.selectedIndex}" aria-disabled="${option.disabled}" data-option-index="${index}">${esc(option.textContent)}</div>`).join("");
    }
    if (!wrap.classList.contains("open")) active = select.selectedIndex;
    const text = select.options[select.selectedIndex]?.textContent || "";
    const value = trigger.querySelector(".custom-select-label");
    if (value.textContent !== text) value.textContent = text;
    trigger.disabled = select.disabled || !select.options.length;
    menu.querySelectorAll("[data-option-index]").forEach(option => {
      const index = Number(option.dataset.optionIndex), selected = index === select.selectedIndex;
      option.classList.toggle("selected", selected);
      option.classList.toggle("is-active", wrap.classList.contains("open") && index === active);
      option.setAttribute("aria-selected", String(selected));
    });
    if (wrap.classList.contains("open") && active >= 0) trigger.setAttribute("aria-activedescendant", id + "-" + active);
    else trigger.removeAttribute("aria-activedescendant");
  }
  function close(focus = false) {
    wrap.classList.remove("open");
    trigger.setAttribute("aria-expanded", "false");
    render(false);
    typed = "";
    if (focus) trigger.focus({ preventScroll: true });
  }
  function reveal() {
    menu.querySelector(`[data-option-index="${active}"]`)?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
  function open() {
    if (trigger.disabled) return;
    closeCustomSelects(wrap);
    const rect = trigger.getBoundingClientRect(), below = innerHeight - rect.bottom - 12, above = rect.top - 12;
    const up = below < 180 && above > below;
    menu.classList.toggle("opens-up", up);
    menu.style.maxHeight = Math.max(80, Math.min(300, up ? above : below)) + "px";
    wrap.classList.add("open");
    trigger.setAttribute("aria-expanded", "true");
    const indexes = available();
    active = indexes.includes(select.selectedIndex) ? select.selectedIndex : indexes[0] ?? -1;
    render(false);
    reveal();
  }
  function choose(index) {
    if (!available().includes(index)) return;
    const changed = index !== select.selectedIndex;
    select.selectedIndex = index;
    close(true);
    if (changed) select.dispatchEvent(new Event("change", { bubbles: true }));
  }
  selectControllers.set(select, { render, close });
  trigger.onclick = event => {
    event.stopPropagation();
    wrap.classList.contains("open") ? close() : open();
  };
  trigger.onkeydown = event => {
    if (event.isComposing || event.keyCode === 229) return;
    const isOpen = wrap.classList.contains("open");
    if (event.key === "Escape") { event.preventDefault(); close(true); return; }
    if (event.key === "Tab") { close(); return; }
    if (["Enter", " "].includes(event.key)) {
      event.preventDefault(); isOpen ? choose(active) : open(); return;
    }
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      if (!isOpen) open();
      const indexes = available(), current = indexes.indexOf(active);
      if (event.key === "Home") active = indexes[0] ?? -1;
      else if (event.key === "End") active = indexes.at(-1) ?? -1;
      else if (isOpen) active = indexes[Math.max(0, Math.min(indexes.length - 1, current + (event.key === "ArrowDown" ? 1 : -1)))] ?? -1;
      render(false); reveal(); return;
    }
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      if (!isOpen) open();
      const now = Date.now(); typed = now - typedAt < 700 ? typed + event.key : event.key; typedAt = now;
      const prefix = [...typed].every(char => char === typed[0]) ? typed[0] : typed;
      const indexes = available(), after = indexes.filter(index => index > active), before = indexes.filter(index => index <= active);
      const match = [...after, ...before].find(index => select.options[index].textContent.toLocaleLowerCase().startsWith(prefix.toLocaleLowerCase()));
      if (match !== undefined) { active = match; render(false); reveal(); }
    }
  };
  trigger.onblur = () => close();
  menu.onpointerdown = event => event.preventDefault();
  menu.onclick = event => {
    const option = event.target.closest("[data-option-index]");
    if (option) { event.stopPropagation(); choose(Number(option.dataset.optionIndex)); }
  };
  select.addEventListener("change", () => render(false));
  render();
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
