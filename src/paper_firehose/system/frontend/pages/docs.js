import { state, documents, setDocuments } from "../state.js";
import { api } from "../api.js";
import { $, $$, esc, empty, refreshIcons } from "../components/ui.js";
import { commitRoute } from "../actions.js";
let documentRequest = 0;
export async function renderDocs() {
  const request = ++documentRequest;
  $("#docArticle").innerHTML = empty("正在读取文档…");
  try {
    if (!documents.length) {
      const index = await api("/api/docs");
      setDocuments(index.documents || []);
      if (!documents.some((item) => item.slug === state.document))
        state.document = documents[0]?.slug || "";
    }
    $("#docsDirectory").innerHTML = documents
      .map(
        (item) =>
          `<button class="${item.slug === state.document ? "active" : ""}" data-document="${esc(item.slug)}"><span class="icon-wrap" aria-hidden="true"><i data-lucide="${esc(item.icon || "file-text")}"></i></span><span><b>${esc(item.title)}</b><small>${esc(item.description)}</small></span></button>`,
      )
      .join("");
    $$("[data-document]").forEach(
      (button) =>
        (button.onclick = () => {
          state.document = button.dataset.document;
          commitRoute();
        }),
    );
    if (!state.document) {
      $("#docArticle").innerHTML = empty("暂时没有可用文档");
      return;
    }
    const documentData = await api(
      "/api/docs/" + encodeURIComponent(state.document),
    );
    if (request !== documentRequest) return;
    $("#docArticle").innerHTML = documentData.html;
    $("#docArticle")
      .querySelectorAll('a[href^="http"]')
      .forEach((link) => {
        link.target = "_blank";
        link.rel = "noopener";
      });
    $("#docArticle")
      .querySelectorAll("[data-doc-slug]")
      .forEach(
        (link) =>
          (link.onclick = (event) => {
            event.preventDefault();
            state.document = link.dataset.docSlug;
            commitRoute();
          }),
      );
    $("#docArticle")
      .querySelectorAll("pre")
      .forEach((pre) => {
        const button = document.createElement("button");
        button.className = "copy-code";
        button.type = "button";
        button.title = "复制代码";
        button.setAttribute("aria-label", "复制代码");
        button.innerHTML = '<i data-lucide="copy"></i>';
        button.onclick = async () => {
          await navigator.clipboard.writeText(
            pre.querySelector("code")?.textContent || pre.textContent,
          );
          button.innerHTML = '<i data-lucide="check"></i>';
          refreshIcons(button);
          setTimeout(() => {
            button.innerHTML = '<i data-lucide="copy"></i>';
            refreshIcons(button);
          }, 1200);
        };
        pre.appendChild(button);
      });
    refreshIcons($("#docs"));
  } catch (error) {
    if (request === documentRequest)
      $("#docArticle").innerHTML = empty("文档读取失败：" + error.message);
  }
}
