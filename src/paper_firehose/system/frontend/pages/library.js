import { state, saved, read } from "../state.js";
import { api } from "../api.js";
import { $, empty, refreshIcons } from "../components/ui.js";
import { remember, card, bindCards } from "../components/papers.js";
let libraryRequest = 0;
export async function renderLibrary() {
  const request = ++libraryRequest,
    mode = state.libraryMode,
    ids = [
      ...(mode === "read"
        ? read
        : mode === "unread"
          ? new Set([...saved].filter((id) => !read.has(id)))
          : saved),
    ];
  if (!ids.length) {
    $("#libraryGrid").innerHTML = empty(
      mode === "saved"
        ? "还没有收藏论文，去发现页收藏感兴趣的论文吧。"
        : "这里暂时为空。",
    );
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
    const list = remember(items);
    $("#libraryGrid").innerHTML =
      list.map(card).join("") || empty("这里暂时为空。");
    bindCards($("#libraryGrid"));
    refreshIcons($("#library"));
  } catch (error) {
    if (request !== libraryRequest || error.name === "AbortError") return;
    $("#libraryGrid").innerHTML = empty("文献库读取失败：" + error.message);
  }
}
