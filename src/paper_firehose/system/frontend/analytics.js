// First-party product events: no typed queries, email, IP or arbitrary URLs.
const queue = [];
let enabled = false,
  serverEnabled = false,
  busy = false,
  mounted = false,
  active = null;
function eventId() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) crypto.getRandomValues(bytes);
  else bytes.forEach((_, i) => (bytes[i] = Math.floor(Math.random() * 256)));
  return [...bytes].map((x) => x.toString(16).padStart(2, "0")).join("");
}
let session = { id: eventId(), last: Date.now() };
try {
  const stored = JSON.parse(sessionStorage.getItem("pc-visit") || "null");
  if (
    stored &&
    /^[a-f0-9-]{32,36}$/.test(stored.id) &&
    Number.isFinite(stored.last)
  )
    session = stored;
} catch {}
function optedOut() {
  try {
    return (
      localStorage.getItem("pc-analytics-off") === "1" ||
      navigator.globalPrivacyControl === true
    );
  } catch {
    return true;
  }
}
export function analyticsDisabled() {
  return optedOut();
}
export function setAnalytics(enabledByServer) {
  serverEnabled = enabledByServer;
  enabled = enabledByServer && !optedOut();
  if (enabled && active && !active.recorded) {
    active.recorded = true;
    active.started = Date.now();
    record("page_view", {
      paper_id: active.paper_id,
      details: { page_number: active.page_number },
    });
  }
  if (!enabled) queue.length = 0;
  if (mounted) return;
  mounted = true;
  document.addEventListener("click", (event) => {
    if (!active) return;
    const control = event.target.closest(
      "button,a,input,select,.paper,.topic-row,.venue-row",
    );
    const element = control || event.target;
    const paper = element.closest("[data-public-id]");
    let target = !control
      ? "surface"
      : element.dataset.go
        ? "nav:" + element.dataset.go
        : element.id || element.dataset.track || element.tagName.toLowerCase();
    if (element.classList.contains("save")) target = "favorite_toggle";
    if (element.classList.contains("detail-primary")) target = "paper_original";
    if (element.closest(".paper h3") || element.classList.contains("paper"))
      target = "paper_open";
    const details = {};
    if (element.dataset.source) {
      target = "source_open";
      details.value = element.dataset.source.slice(0, 120);
    }
    if (element.dataset.theme) {
      target = "topic_open";
      details.value = element.dataset.theme.slice(0, 120);
    }
    if (element.dataset.page) {
      target = "pagination";
      details.page_number = Math.max(
        0,
        Math.min(3600000, Number(element.dataset.page) || 0),
      );
    }
    record("click", {
      target,
      paper_id: paper?.dataset.publicId || active.paper_id || "",
      details,
    });
  });
  document.addEventListener("change", (event) => {
    if (event.target.tagName === "SELECT")
      record("filter_change", {
        target: event.target.id,
        details: { value: event.target.value.slice(0, 120) },
      });
  });
  setInterval(() => flush(), 5000);
  addEventListener("pagehide", () => {
    leavePage();
    flush(true);
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      leavePage();
      flush(true);
    } else if (active) {
      active.started = Date.now();
      active.left = false;
    }
  });
}
export function analyticsOptOut(value) {
  try {
    localStorage.setItem("pc-analytics-off", value ? "1" : "0");
  } catch {}
  enabled = serverEnabled && !value && !navigator.globalPrivacyControl;
  queue.length = 0;
}
export function record(type, fields = {}) {
  if (!enabled || !active) return;
  const now = Date.now();
  if (now - session.last > 1800000) session.id = eventId();
  session.last = now;
  try {
    sessionStorage.setItem("pc-visit", JSON.stringify(session));
  } catch {}
  queue.push({
    id: eventId(),
    session_id: session.id,
    type,
    page: active.page,
    ...fields,
  });
  if (queue.length > 100) queue.shift();
  if (queue.length >= 20) void flush();
}
function leavePage() {
  if (active && !active.left && active.recorded) {
    record("page_leave", {
      paper_id: active.paper_id,
      details: {
        duration_ms: Math.max(
          0,
          Math.min(3600000, Date.now() - active.started),
        ),
      },
    });
    active.left = true;
  }
}
export function trackPage(state) {
  leavePage();
  active = {
    page: state.view,
    page_number: state.page,
    paper_id:
      state.view === "paper" && /^[a-f0-9]{64}$/.test(state.paperId)
        ? state.paperId
        : "",
    started: Date.now(),
    left: false,
    recorded: enabled,
  };
  record("page_view", {
    paper_id: active.paper_id,
    details: { page_number: state.page },
  });
  if (state.query)
    record("search", {
      details: { query_length: Math.min(state.query.length, 3600000) },
    });
}
export async function flush(beacon = false) {
  if (busy || !queue.length || !enabled) return;
  const batch = queue.splice(0, 20),
    body = JSON.stringify({ events: batch });
  if (
    beacon &&
    navigator.sendBeacon(
      "/api/events",
      new Blob([body], { type: "application/json" }),
    )
  )
    return;
  busy = true;
  try {
    const response = await fetch("/api/events", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: true,
    });
    if (response.status >= 500) queue.unshift(...batch);
  } catch {
    queue.unshift(...batch);
  } finally {
    while (queue.length > 100) queue.shift();
    busy = false;
  }
}
