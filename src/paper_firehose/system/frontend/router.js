const views = new Set([
  "welcome",
  "overview",
  "discover",
  "venues",
  "topics",
  "library",
  "docs",
  "paper",
  "account",
]);
export function parseRoute(hash) {
  const [path, query = ""] = hash.replace(/^#\/?/, "").split("?");
  const [name, id = ""] = path.split("/"),
    params = new URLSearchParams(query);
  const view =
    name === "home" ? "overview" : views.has(name) ? name : "welcome";
  const page = Number(params.get("page"));
  return {
    view,
    paperId: view === "paper" ? id : "",
    page: Number.isSafeInteger(page) && page > 0 ? page : 1,
    type: params.get("type") || "",
    theme: params.get("theme") || "",
    source: params.get("source") || "",
    days: ["30", "90", "365"].includes(params.get("days"))
      ? params.get("days")
      : "",
    sort: ["date", "score", "title"].includes(params.get("sort"))
      ? params.get("sort")
      : "date",
    query: params.get("q") || "",
    document: params.get("doc") || "mcp",
    venueType: params.get("kind") || "",
    libraryMode: ["saved", "read", "unread"].includes(params.get("mode"))
      ? params.get("mode")
      : "saved",
  };
}
export function routeHash(state) {
  const name = state.view === "overview" ? "home" : state.view;
  if (name === "paper") return "#paper/" + state.paperId;
  const params = new URLSearchParams();
  if (name === "discover") {
    for (const key of ["type", "theme", "source", "days"])
      if (state[key]) params.set(key, state[key]);
    if (state.query) params.set("q", state.query);
    if (state.sort !== "date") params.set("sort", state.sort);
    if (state.page > 1) params.set("page", state.page);
  }
  if (name === "docs" && state.document !== "mcp")
    params.set("doc", state.document);
  if (name === "venues" && state.venueType) params.set("kind", state.venueType);
  if (name === "library" && state.libraryMode !== "saved")
    params.set("mode", state.libraryMode);
  return "#" + name + (params.size ? "?" + params : "");
}
export function createRouter(onRoute) {
  const positions = new Map();
  let active = location.hash || "#welcome",
    sequence = 0,
    initial = true,
    restoring = false;
  history.scrollRestoration = "manual";
  async function apply() {
    const request = ++sequence,
      hash = location.hash || "#welcome";
    const position = positions.get(hash) || 0;
    restoring = true;
    active = hash;
    const first = initial;
    initial = false;
    await onRoute(parseRoute(hash), { initial: first });
    if (request === sequence)
      requestAnimationFrame(() => {
        scrollTo(0, position);
        requestAnimationFrame(() => {
          if (request === sequence) restoring = false;
        });
      });
  }
  addEventListener(
    "scroll",
    () => {
      if (restoring) return;
      positions.set(active, scrollY);
      while (positions.size > 100)
        positions.delete(positions.keys().next().value);
    },
    { passive: true },
  );
  addEventListener("hashchange", apply);
  return {
    start() {
      if (!location.hash) history.replaceState(null, "", "#welcome");
      return apply();
    },
    navigate(hash, { replace = false } = {}) {
      positions.set(active, scrollY);
      if (hash === location.hash) return apply();
      if (replace) {
        history.replaceState(null, "", hash);
        return apply();
      }
      location.hash = hash;
    },
    back() {
      history.back();
    },
  };
}
