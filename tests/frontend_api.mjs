import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
const source = await readFile(new URL("../src/paper_firehose/system/frontend/api.js", import.meta.url), "utf8");
const { api, cancelApiChannel } = await import("data:text/javascript;base64," + Buffer.from(source).toString("base64"));

test("identical successful reads reuse the bounded cache", async (t) => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => ({ ok: true, json: async () => ({ count: ++calls }) }));
  assert.deepEqual(await api("/cached"), { count: 1 });
  assert.deepEqual(await api("/cached"), { count: 1 });
  assert.equal(calls, 1);
});

test("leaving a view aborts its request without canceling other channels", async (t) => {
  t.mock.method(globalThis, "fetch", (_url, { signal }) => new Promise((resolve, reject) => {
    signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
    if (_url === "/new-view") resolve({ ok: true, json: async () => ({ ok: true }) });
  }));
  const old = api("/old-view", { channel: "discover" });
  const other = api("/new-view", { channel: "paper" });
  const rejected = assert.rejects(old, { name: "AbortError" });
  cancelApiChannel("discover");
  await rejected;
  assert.deepEqual(await other, { ok: true });
});

test("failed reads are retried instead of cached", async (t) => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => ++calls === 1
    ? { ok: false, status: 503 }
    : { ok: true, json: async () => ({ recovered: true }) });
  await assert.rejects(api("/retry"), { status: 503 });
  assert.deepEqual(await api("/retry"), { recovered: true });
});

test("cache eviction keeps memory bounded", async (t) => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => ({ ok: true, json: async () => ({ count: ++calls }) }));
  for (let i = 0; i < 45; i++) await api("/eviction/" + i);
  const previous = calls;
  await api("/eviction/0");
  assert.equal(calls, previous + 1);
});
