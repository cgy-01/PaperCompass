import assert from "node:assert/strict";
import { formatCJKSpacing } from "../src/paper_firehose/system/frontend/typography.js";

assert.equal(
  formatCJKSpacing("使用GitHub登录，近30天新增3篇LLM论文。"),
  "使用 GitHub 登录，近 30 天新增 3 篇 LLM 论文。",
);
assert.equal(
  formatCJKSpacing("中文 ， English 。 大模型 （ LLM ）"),
  "中文，English。大模型（LLM）",
);
assert.equal(
  formatCJKSpacing("访问 https://example.test/中文LLM?a=1"),
  "访问 https://example.test/中文LLM?a=1",
);
assert.equal(
  formatCJKSpacing("联系 gavinchan041@gmail.com"),
  "联系 gavinchan041@gmail.com",
);
assert.equal(
  formatCJKSpacing("纯中文正文。\n下一段介绍。"),
  "纯中文正文。\n下一段介绍。",
);
const once = formatCJKSpacing("LLM检索API与30篇论文");
assert.equal(formatCJKSpacing(once), once);
console.log(
  "Typography spacing passed: mixed text, punctuation, URLs, email and idempotence.",
);
