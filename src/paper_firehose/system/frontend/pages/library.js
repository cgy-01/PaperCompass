import { state, saved, read, visited } from "../state.js";
import { api } from "../api.js";
import { $, empty, refreshIcons } from "../components/ui.js";
import { remember, card, bindCards } from "../components/papers.js";
import { account, initAccount, renderLibraryAccount } from "../account.js";
let libraryRequest = 0;
export async function renderLibrary() {
  const request = ++libraryRequest;
  renderLibraryAccount($("#libraryScope"));
  if (!account.ready || account.loading) {
    $("#libraryGrid").innerHTML = empty("正在同步文献库…");
    await initAccount();
    if (request !== libraryRequest || state.view !== "library") return;
  }
  const mode = state.libraryMode,
    ids = [
      ...(mode === "visited" ? [...visited].reverse() : mode === "read"
        ? read
        : mode === "unread"
          ? new Set([...saved].filter((id) => !read.has(id)))
          : saved),
    ];
  renderLibraryAccount($("#libraryScope"));
  if (!ids.length) {
    $("#libraryGrid").innerHTML = empty(
      mode === "saved"
        ? "还没有收藏论文，去发现页收藏感兴趣的论文吧。"
        : mode === "visited" ? "还没有浏览记录，打开一篇论文即可开始。"
        : mode === "read" ? "还没有标记为已读的论文，可在论文详情页标记。"
        : "暂时没有未读的收藏论文。",
    ) + '<a class="text-btn" href="#discover">发现论文</a>';
    return;
  }
  $("#libraryGrid").innerHTML = empty("正在读取文献库…");
  try {
    const items = [];
    // Keep the backend batch cap while preserving collections larger than 250.
    for (let offset = 0; offset < ids.length; offset += 250) {
      const data = await api(
        "/api/v1/papers?ids=" +
          encodeURIComponent(ids.slice(offset, offset + 250).join(",")),
        { channel: "library" },
      );
      if (request !== libraryRequest) return;
      items.push(...data.items);
    }
    if (request !== libraryRequest) return;
    if (mode === "visited") {
      const positions = new Map(ids.map((id, index) => [id, index]));
      items.sort((a, b) => positions.get(a.id) - positions.get(b.id));
    }
    const list = remember(items);
    $("#libraryGrid").innerHTML =
      list.map(card).join("") || empty("这里暂时为空。");
    bindCards($("#libraryGrid"));
    refreshIcons($("#library"));
  } catch (error) {
    if (request !== libraryRequest || error.name === "AbortError") return;
    $("#libraryGrid").innerHTML = empty("文献库暂时未能加载，请重试。") + '<button type="button" class="text-btn" id="libraryRetry">重试</button>';
    $("#libraryRetry").onclick = () => renderLibrary();
    refreshIcons($("#libraryGrid"));
  }
}
