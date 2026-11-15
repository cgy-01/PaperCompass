// Presentation only: source metadata, filter values, URLs and copied code stay intact.
const han = /\p{Script=Han}/u;
const ascii = /[A-Za-z0-9]/;
const punctuation = "，。！？；：、（）【】《》“”‘’";
const skip =
  "script,style,pre,code,textarea,input,select,option,svg,.icon-wrap,[contenteditable],.welcome-log-track";
const flow = "p,li,blockquote,td,th,dt,dd,h1,h2,h3,h4,h5,h6";

export function formatCJKSpacing(value) {
  // Avoid changing visible URLs or email addresses, including Chinese URL paths.
  return String(value)
    .split(
      /(https?:\/\/[^\s<>"'，。！？；：（）【】《》]+|[\w.+-]+@[\w.-]+\.[A-Za-z]+)/g,
    )
    .map((part, index) =>
      index % 2
        ? part
        : part
            .replace(/(\p{Script=Han})([A-Za-z0-9])/gu, "$1 $2")
            .replace(/([A-Za-z0-9])(\p{Script=Han})/gu, "$1 $2")
            .replace(new RegExp(`[ \\t]+([${punctuation}])`, "g"), "$1")
            .replace(new RegExp(`([${punctuation}])[ \\t]+`, "g"), "$1"),
    )
    .join("");
}

function formatText(root) {
  if (root.nodeType === Node.TEXT_NODE) {
    if (!root.parentElement?.closest(skip)) {
      const formatted = formatCJKSpacing(root.data);
      if (root.data !== formatted) root.data = formatted;
    }
    return;
  }
  if (root.nodeType !== Node.ELEMENT_NODE || root.closest(skip)) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let node, previous;
  while ((node = walker.nextNode())) {
    const block = node.parentElement.closest(flow);
    if (node.parentElement.closest(skip)) {
      // Keep inline code exact, adding its surrounding space to prose only.
      if (
        block &&
        node.parentElement.closest("code") &&
        !node.parentElement.closest("pre")
      ) {
        if (
          previous?.block === block &&
          !previous.protected &&
          han.test(previous.node.data.slice(-1)) &&
          ascii.test(node.data[0] || "")
        ) {
          previous.node.data += " ";
        }
        previous = { node, block, protected: true };
        continue;
      }
      previous = null;
      continue;
    }
    let formatted = formatCJKSpacing(node.data);
    // Markdown emphasis can split "中文LLM正文" across inline elements.
    if (block && previous?.block === block) {
      // Full-width punctuation can also be separated by emphasis/link markup.
      if (!previous.protected && punctuation.includes(formatted[0] || "\0")) {
        const trimmed = previous.node.data.replace(/[ \t]+$/, "");
        if (trimmed !== previous.node.data) previous.node.data = trimmed;
      }
      if (punctuation.includes(previous.node.data.slice(-1) || "\0")) {
        formatted = formatted.replace(/^[ \t]+/, "");
      }
      const left = previous.node.data.slice(-1),
        right = formatted[0] || "";
      if (
        (han.test(left) && ascii.test(right)) ||
        (ascii.test(left) && han.test(right))
      ) {
        formatted = " " + formatted;
      }
    }
    if (node.data !== formatted) node.data = formatted;
    previous = { node, block };
  }
}

export function initTypography() {
  formatText(document.body);
  const observer = new MutationObserver((records) => {
    const roots = new Set();
    for (const record of records) {
      // Expand changed inline text to its prose block to preserve boundary spacing.
      const parent =
        record.target.nodeType === Node.TEXT_NODE
          ? record.target.parentElement
          : record.target;
      if (parent?.closest(skip)) continue;
      const block = parent?.closest(flow);
      if (block || record.type === "characterData") {
        roots.add(block || parent);
      } else {
        // A list replacement only needs its newly added subtrees formatted.
        // In particular, do not rescan a whole page for an icon insertion.
        for (const node of record.addedNodes) {
          if (node.nodeType === Node.ELEMENT_NODE) roots.add(node);
          else if (node.nodeType === Node.TEXT_NODE) roots.add(parent);
        }
      }
    }
    for (const root of roots) {
      if (!root?.isConnected) continue;
      let ancestor = root.parentElement;
      while (ancestor && !roots.has(ancestor)) ancestor = ancestor.parentElement;
      if (!ancestor) formatText(root);
    }
  });
  observer.observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true,
  });
}
