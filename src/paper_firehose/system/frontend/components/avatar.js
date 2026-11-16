import { esc, icon } from "./ui.js";

export function avatar(user, large = false) {
  const size = large ? 48 : 24;
  let source = "";
  try {
    const url = new URL(user?.avatar_url);
    // Only load GitHub's avatar CDN; account data must not enable arbitrary URLs.
    if (url.protocol === "https:" && url.hostname === "avatars.githubusercontent.com" &&
        !url.username && !url.password && !url.port) {
      url.searchParams.set("s", String(size * 2));
      url.hash = "";
      source = url.href;
    }
  } catch {}
  // The adjacent account name already labels this decorative image.
  return `<span class="account-avatar${large ? ' account-emblem' : ''}" aria-hidden="true">${icon("circle-user-round", !large)}${source ? `<img src="${esc(source)}" alt="" width="${size}" height="${size}" decoding="async" referrerpolicy="no-referrer">` : ''}</span>`;
}

export function bindAvatars(root) {
  root.querySelectorAll(".account-avatar img").forEach(image => {
    const reveal = () => image.classList.toggle("is-loaded", image.naturalWidth > 0);
    image.addEventListener("load", reveal, { once: true });
    image.addEventListener("error", () => image.remove(), { once: true });
    // Cached images may finish before the listeners are attached.
    if (image.complete) reveal();
  });
}
