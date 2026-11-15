// Keep displayed publication families separate from exact API source names.
export function sourceFamily(source) {
  if (source.type === "预印本" && /^arxiv\b/i.test(source.name)) return "arXiv";
  return source.type === "会议" ? source.name.replace(/\s+(?:19|20)\d{2}$/, "") : source.name;
}
export function groupSources(sources) {
  const groups = new Map();
  for (const source of sources) {
    const name = sourceFamily(source), key = source.type + ":" + name;
    if (!groups.has(key)) groups.set(key, { name, type: source.type, count: 0, items: [] });
    const group = groups.get(key);
    group.count += source.count;
    group.items.push(source);
  }
  for (const group of groups.values()) group.items.sort((a, b) => b.name.localeCompare(a.name, "zh-CN", { numeric: true }));
  return [...groups.values()].sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}
export function discoverHref(filters) {
  const params = new URLSearchParams();
  for (const key of ["type", "theme", "source", "days", "q"])
    if (filters[key]) params.set(key, filters[key]);
  return "#discover" + (params.size ? "?" + params : "");
}
