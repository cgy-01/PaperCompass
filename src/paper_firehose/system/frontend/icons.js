// Both the registry and renderer come from the existing official Lucide package.
// icon-data.js is generated when publishing the dashboard, not downloaded from a CDN.
import nodes from "./icon-data.js";
import replaceElement from "./vendor/lucide/replaceElement.js";

const icons = Object.fromEntries(Object.entries(nodes).map(([name, value]) => [
  name.replace(/(^|-)(\w)/g, (_, _separator, letter) => letter.toUpperCase()), value,
]));

export function replaceIcons(root) {
  // Lucide keeps data-lucide on generated SVGs. Select placeholders only so a
  // refresh cannot replace existing SVGs or retrigger layout/typography work.
  root.querySelectorAll("i[data-lucide]").forEach((element) => {
    replaceElement(element, { nameAttr: "data-lucide", icons,
      attrs: { width: 20, height: 20, "stroke-width": 1.75 } });
  });
}
