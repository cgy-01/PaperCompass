import { state, paperIndex, saved, read } from "../state.js";
import { api } from "../api.js";
import { $, esc, date, icon, empty, refreshIcons } from "../components/ui.js";
import { saveStores, commitRoute } from "../actions.js";
import { toggleFavorite, markRead } from "../account.js";
let requestNumber = 0;
export function openPaper(id) {
  const identifier = paperIndex.get(id)?.publicId || id;
  if (!/^[a-f0-9]{64}$/.test(identifier)) return;
  state.view = "paper";
  state.paperId = identifier;
  commitRoute();
}
function safeURL(value) {
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}
export async function renderPaper() {
  const request = ++requestNumber,
    identifier = state.paperId,
    root = $("#paperDetail");
  root.innerHTML = empty("正在读取论文详情…");
  try {
    const { paper: p } = await api(
      "/api/v1/papers/" + encodeURIComponent(identifier),
      { channel: "paper" },
    );
    if (
      request !== requestNumber ||
      state.view !== "paper" ||
      state.paperId !== identifier
    )
      return;
    void markRead(p.id);
    const url = safeURL(p.url);
    document.title = p.title + " · Paper Compass";
    root.innerHTML = `<div class="detail-nav"><button class="text-btn" id="paperBack">${icon("arrow-left", true)} 返回</button><span>Paper Compass / 论文详情</span></div>
      <article class="paper-detail"><header class="detail-header"><div class="badges"><span class="badge type">${esc(p.venueType)}</span><span class="detail-source">${esc(p.source)}</span></div>
      <h1>${esc(p.title)}</h1><p class="detail-authors">${esc(p.authors)}</p><div class="detail-meta"><span>${icon("calendar-days", true)} ${esc(date(p.published))}</span><span>${icon("book-open", true)} ${esc(p.source)}</span></div></header>
      <div class="detail-layout"><div class="detail-reading"><section><h2>摘要 <span>Abstract</span></h2><p class="detail-abstract">${esc(p.summary)}</p></section>${p.insight ? `<section><h2>研究笔记</h2><p class="detail-abstract">${esc(p.insight)}</p></section>` : ""}</div>
      <aside class="detail-aside"><div class="detail-actions">${url ? `<a class="detail-primary" href="${esc(url)}" target="_blank" rel="noopener noreferrer">阅读原文 ${icon("arrow-up-right", true)}</a>` : ""}<button class="detail-save" id="detailSave">${icon("star", true)} <span>${saved.has(p.id) ? "已收藏" : "收藏论文"}</span></button><button class="detail-save" id="detailCopy">${icon("link", true)} <span>复制详情链接</span></button></div><h3>研究主题</h3><div class="detail-topics">${(p.topics || []).map((t) => `<span class="badge">${esc(t)}</span>`).join("") || '<span class="detail-muted">暂无主题</span>'}</div><p class="detail-read">${icon("check", true)} 已加入阅读记录</p></aside></div></article>`;
    $("#paperBack").onclick = () => {
      if (history.state?.pcDetailOrigin) history.back();
      else {
        state.view = "discover";
        commitRoute();
      }
    };
    $("#detailSave").onclick = async () => {
      const button = $("#detailSave");
      button.disabled = true;
      try {
        await toggleFavorite(p.id);
      } catch {
        return;
      } finally {
        button.disabled = false;
      }
      button.querySelector("span:last-child").textContent = saved.has(p.id)
        ? "已收藏"
        : "收藏论文";
    };
    $("#detailCopy").onclick = async () => {
      try {
        await navigator.clipboard.writeText(location.href);
        $("#detailCopy span:last-child").textContent = "链接已复制";
      } catch {
        $("#detailCopy span:last-child").textContent = "请复制浏览器地址";
      }
    };
    refreshIcons(root);
  } catch (error) {
    if (request !== requestNumber || error.name === "AbortError") return;
    root.innerHTML = empty(
      error.status === 404
        ? "没有找到这篇论文。"
        : "论文详情读取失败：" + error.message,
    );
    root.insertAdjacentHTML(
      "beforeend",
      '<a class="text-btn" href="#discover">返回发现论文</a>',
    );
  }
}
