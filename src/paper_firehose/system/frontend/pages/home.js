import { overview, institutionCatalog } from "../state.js";
import { $, esc, date, refreshIcons } from "../components/ui.js";
import { remember, paperHref } from "../components/papers.js";
export function drawYearTrend() {
  const canvas = $("#yearTrend"),
    yearly = overview.yearly || [];
  if (!canvas || !yearly.length) return;
  const years = yearly.map((x) => x.year),
    counts = Object.fromEntries(yearly.map((x) => [x.year, x.count])),
    rect = canvas.getBoundingClientRect(),
    width = Math.max(280, Math.round(rect.width)),
    height = Math.max(150, Math.round(rect.height)),
    dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, width, height);
  const pad = { l: 18, r: 14, t: 25, b: 24 },
    chartW = width - pad.l - pad.r,
    chartH = height - pad.t - pad.b,
    max = Math.max(...years.map((y) => counts[y]), 1),
    x = (i) =>
      pad.l +
      (years.length === 1 ? chartW / 2 : (i * chartW) / (years.length - 1)),
    y = (value) => pad.t + chartH - (value / max) * chartH,
    compact = (value) =>
      new Intl.NumberFormat("zh-CN", {
        notation: "compact",
        maximumFractionDigits: 1,
      }).format(value);
  ctx.strokeStyle = "#e3e9e5";
  ctx.lineWidth = 1;
  [0, 0.5, 1].forEach((ratio) => {
    const gy = pad.t + chartH * ratio;
    ctx.beginPath();
    ctx.moveTo(pad.l, gy);
    ctx.lineTo(width - pad.r, gy);
    ctx.stroke();
  });
  const points = years.map((year, index) => [x(index), y(counts[year])]);
  ctx.beginPath();
  ctx.moveTo(points[0][0], pad.t + chartH);
  points.forEach((point) => ctx.lineTo(point[0], point[1]));
  ctx.lineTo(points.at(-1)[0], pad.t + chartH);
  ctx.closePath();
  const fill = ctx.createLinearGradient(0, pad.t, 0, pad.t + chartH);
  fill.addColorStop(0, "rgba(18,102,79,.22)");
  fill.addColorStop(1, "rgba(18,102,79,.02)");
  ctx.fillStyle = fill;
  ctx.fill();
  ctx.beginPath();
  points.forEach((point, index) =>
    index ? ctx.lineTo(point[0], point[1]) : ctx.moveTo(point[0], point[1]),
  );
  ctx.strokeStyle = "#12664f";
  ctx.lineWidth = 2;
  ctx.stroke();
  ctx.font = "10px system-ui";
  ctx.textAlign = "center";
  points.forEach((point, index) => {
    ctx.beginPath();
    ctx.arc(point[0], point[1], 3.5, 0, Math.PI * 2);
    ctx.fillStyle = "#fff";
    ctx.fill();
    ctx.strokeStyle = "#12664f";
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.fillStyle = "#52645e";
    ctx.fillText(
      compact(counts[years[index]]),
      point[0],
      Math.max(11, point[1] - 9),
    );
    ctx.fillStyle = "#7a8983";
    ctx.fillText(String(years[index]), point[0], height - 6);
  });
}
export function institutionPlaceholder(name) {
  return `<span class="institution-placeholder" aria-hidden="true"><i data-lucide="landmark"></i></span><span class="sr-only">${esc(name)}</span>`;
}
export function renderInstitutions() {
  const root = $("#institutionList");
  if (!root) return;
  root.innerHTML = institutionCatalog
    .map(
      (item) =>
        `<div class="institution-mark" title="${esc(item.name)}">${item.logo ? `<img src="${esc(item.logo)}" alt="${esc(item.name)}" loading="lazy">` : institutionPlaceholder(item.name)}</div>`,
    )
    .join("");
  root.querySelectorAll("img").forEach((img) =>
    img.addEventListener(
      "error",
      () => {
        img.parentElement.innerHTML = institutionPlaceholder(img.alt);
        refreshIcons(root);
      },
      { once: true },
    ),
  );
}
export function renderOverview() {
  const total = Math.max(overview.total || 0, 1),
    percentage = (n) => ((n / total) * 100).toFixed(2) + "%",
    stats = [
      ["收录论文", (overview.total || 0).toLocaleString(), "本地论文库总量"],
      [
        "近 30 天新增",
        (overview.recent30 || 0).toLocaleString(),
        "按公开日期统计",
      ],
      [
        "主题覆盖",
        percentage(overview.classified || 0),
        (overview.classified || 0).toLocaleString() + " 篇已归类",
      ],
      [
        "论文信息完整度",
        percentage(overview.informationComplete || 0),
        (overview.informationComplete || 0).toLocaleString() +
          " 篇作者、摘要等信息齐全",
      ],
    ];
  $("#heroTopics").textContent = overview.topicCount || 0;
  $("#heroSources").textContent = overview.sourceCount || 0;
  $("#stats").innerHTML = stats
    .map(
      ([label, value, detail]) =>
        `<div class="stat"><b>${value}</b><span>${label}</span><small>${detail}</small></div>`,
    )
    .join("");
  remember(overview.latest || []);
  $("#topPapers").innerHTML = (overview.latest || [])
    .map(
      (p) =>
        `<div class="mini-paper"><a href="${paperHref(p)}">${esc(p.title)}</a><p>${esc(p.source)} · ${date(p.published || p.matched)} · 相关度 ${p.score ? Math.round(p.score * 100) + "%" : "—"}</p></div>`,
    )
    .join("");
  const palette = [
      "#12664f",
      "#ee8c49",
      "#4e7fa8",
      "#8b6ca8",
      "#c39a36",
      "#4f9b8f",
      "#78965d",
      "#9a8f7a",
    ],
    segments = overview.themeSegments || [],
    classified = Math.max(overview.classified || 0, 1);
  let cursor = 0;
  const gradient = segments
    .map((item, i) => {
      const start = cursor;
      cursor += (item.count / classified) * 100;
      return `${palette[i]} ${start}% ${cursor}%`;
    })
    .join(",");
  $("#sourceDonut").style.background = gradient
    ? `conic-gradient(${gradient})`
    : "var(--line)";
  $("#donutTotal").textContent = (overview.classified || 0).toLocaleString();
  $("#sourceLegend").innerHTML = segments
    .map(
      (item, i) =>
        `<div class="legend-row"><i class="legend-dot" style="background:${palette[i]}"></i><span>${esc(item.name)}</span><b>${item.count.toLocaleString()} · ${Math.round((item.count / classified) * 100)}%</b></div>`,
    )
    .join("");
  renderInstitutions();
  requestAnimationFrame(drawYearTrend);
}
addEventListener("resize", () => {
  if (document.querySelector("#overview.active"))
    requestAnimationFrame(drawYearTrend);
});
