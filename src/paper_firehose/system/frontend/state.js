// Shared UI state; raw IDs are retained only for existing local collections.
export let topicCatalog = [],
  sources = [],
  overview = {},
  documents = [];
export const paperIndex = new Map();
export const state = {
  view: "overview",
  page: 1,
  type: "",
  theme: "",
  source: "",
  days: "",
  sort: "date",
  query: "",
  venueType: "",
  document: "mcp",
  paperId: "",
  libraryMode: "saved",
  dataVersion: "",
};
export function updateCatalog(data) {
  topicCatalog = data.topics;
  sources = data.sources;
  overview = data.overview;
  state.dataVersion = data.dataVersion || "";
}
export function setDocuments(items) {
  documents = items;
}
function storedSet(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "[]");
    return new Set(
      Array.isArray(value) ? value.filter((x) => typeof x === "string") : [],
    );
  } catch {
    return new Set();
  }
}
export const saved = storedSet("pc-saved"),
  read = storedSet("pc-read");
export const institutionCatalog = [
  { name: "中山大学", logo: "/api/docs/assets/img/institutions/sysu.png" },
  { name: "厦门大学", logo: "/api/docs/assets/img/institutions/xmu.jpg" },
  { name: "北京师范大学", logo: "/api/docs/assets/img/institutions/bnu.jpg" },
  ...Array.from({ length: 3 }, (_, index) => ({
    name: `待添加高校 ${index + 4}`,
    logo: "",
  })),
];
