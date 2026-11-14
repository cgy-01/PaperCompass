import { overview, topicCatalog } from "../state.js";
import { $, esc, icon, refreshIcons, date } from "../components/ui.js";
import { syncWelcomeMotion, showWelcomeSticky } from "./welcome-motion.js";
import { renderWelcomeDesk } from "./welcome-desk.js";
import { renderInstitutions } from "../components/institutions.js";

let chromeBound = false,
  chromeFrame = false;
function updateWelcomeChrome() {
  chromeFrame = false;
  const visible = document.body.classList.contains("is-welcome");
  document.body.classList.toggle(
    "is-welcome-scrolled",
    visible && scrollY > 56,
  );
  const sticky = $("#welcomeSticky");
  if (!visible) {
    showWelcomeSticky(sticky, false);
    return;
  }
  const research = $("#welcomeResearch");
  const threshold =
    research.getBoundingClientRect().top +
    scrollY +
    research.offsetHeight * 0.45;
  showWelcomeSticky(
    sticky,
    scrollY > threshold &&
      $(".welcome-footer").getBoundingClientRect().top >= innerHeight,
  );
}
function scheduleWelcomeChrome() {
  if (!chromeFrame) {
    chromeFrame = true;
    requestAnimationFrame(updateWelcomeChrome);
  }
}
function bindWelcomeChrome() {
  if (!chromeBound) {
    addEventListener("scroll", scheduleWelcomeChrome, { passive: true });
    addEventListener("resize", scheduleWelcomeChrome);
    addEventListener("hashchange", scheduleWelcomeChrome);
    chromeBound = true;
  }
  document.querySelectorAll("[data-welcome-scroll]").forEach((button) => {
    button.dataset.track = "welcome_section:" + button.dataset.welcomeScroll;
    button.onclick = () =>
      document.getElementById(button.dataset.welcomeScroll)?.scrollIntoView({
        behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "auto"
          : "smooth",
        block: "start",
      });
  });
  scheduleWelcomeChrome();
}
function paperPreview(paper, index) {
  const text = paper.summaryPreview || paper.summary || "";
  const length = index % 3 === 0 ? 260 : index % 3 === 2 ? 155 : 0;
  const excerpt =
    length && text
      ? text.slice(0, length) + (text.length > length ? "…" : "")
      : "";
  return `<a class="welcome-paper${index % 3 === 0 ? " is-featured" : ""}" href="#paper/${paper.publicId}" data-track="preview_paper" data-public-id="${paper.publicId}"><span class="welcome-paper-pin" aria-hidden="true"></span><div class="welcome-paper-meta"><span class="welcome-paper-source">${esc(paper.source)}</span><time>${esc(date(paper.published))}</time></div><h3>${esc(paper.title)}</h3>${excerpt ? `<p>${esc(excerpt)}</p>` : ""}<footer><span class="welcome-author">${esc(paper.authors)}</span>${icon("arrow-up-right", true)}</footer></a>`;
}

// Static copy works without the catalog. Only genuine catalog data enhances it.
export function renderWelcome() {
  renderInstitutions();
  if (topicCatalog.length) {
    $("#welcomeAreaList").innerHTML = topicCatalog
      .map((topic) => {
        const href = "#discover?" + new URLSearchParams({ theme: topic.name });
        return `<a class="welcome-area" href="${esc(href)}" data-track="${esc(("research_area:" + topic.key).slice(0, 80))}">${icon(esc(topic.icon || "circle-dot"))}<span class="welcome-area-copy"><strong>${esc(topic.name)}</strong><small title="${esc(topic.description)}">${esc(topic.description)}</small></span>${icon("arrow-up-right", true)}</a>`;
      })
      .join("");
  }
  for (const [id, value] of [
    ["welcomeTotal", overview.total],
    ["welcomeTopics", overview.topicCount],
    ["welcomeSources", overview.sourceCount],
  ]) {
    if (value !== undefined)
      $("#" + id).textContent = Number(value).toLocaleString();
  }
  const papers = (overview.latest || []).filter((paper) =>
    /^[a-f0-9]{64}$/.test(paper.publicId),
  );
  if (papers.length) {
    $("#welcomePreview").innerHTML = papers
      .slice(0, 6)
      .map(paperPreview)
      .join("");
    $("#welcomePreviewLabel").textContent = "来自当前文献库 / 按发表时间排序";
  }
  renderWelcomeDesk(overview.recent30);
  const copy = $("#welcomeCopy");
  copy.onclick = async () => {
    try {
      await navigator.clipboard.writeText("https://papercompass.cn/mcp");
      copy.setAttribute("aria-label", "已复制 MCP 地址");
      $("#welcomeCopyLabel").textContent = "已复制";
    } catch {
      $("#welcomeCopyLabel").textContent = "请复制地址";
    }
  };
  bindWelcomeChrome();
  refreshIcons($("#welcome"));
  syncWelcomeMotion();
}
