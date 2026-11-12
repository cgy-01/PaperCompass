// No browser framework dependency: run with node --test.
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
const source = await readFile(
  new URL("../src/paper_firehose/system/frontend/router.js", import.meta.url),
  "utf8",
);
const { parseRoute, routeHash } = await import(
  "data:text/javascript;base64," + Buffer.from(source).toString("base64")
);
test("home default and unknown routes", () => {
  assert.equal(parseRoute("").view, "overview");
  assert.equal(parseRoute("#missing").view, "overview");
  assert.equal(routeHash(parseRoute("")), "#home");
});
test("filters, page and Unicode survive a round trip", () => {
  const route = parseRoute(
    "#discover?theme=大模型&page=4&sort=score&q=KV+cache",
  );
  assert.equal(route.page, 4);
  assert.equal(route.query, "KV cache");
  assert.deepEqual(parseRoute(routeHash(route)), route);
});
test("invalid pages and sort fall back safely", () => {
  for (const page of ["-1", "NaN", "Infinity", "1.5"])
    assert.equal(parseRoute("#discover?page=" + page).page, 1);
  assert.equal(parseRoute("#discover?sort=unknown").sort, "date");
});
test("detail routes contain only the public identifier", () => {
  const id = "a".repeat(64),
    route = parseRoute("#paper/" + id);
  assert.equal(route.paperId, id);
  assert.equal(routeHash(route), "#paper/" + id);
});
