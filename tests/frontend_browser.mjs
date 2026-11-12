// Optional real-browser regression test using Chrome DevTools Protocol only.
import { spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import assert from "node:assert/strict";
const profile = await mkdtemp(join(tmpdir(), "pc-browser-"));
const chrome = spawn(process.env.PAPER_COMPASS_BROWSER, [
  "--headless",
  "--no-sandbox",
  "--disable-gpu",
  "--no-first-run",
  "--remote-debugging-port=0",
  "--user-data-dir=" + profile,
  "about:blank",
]);
let socket;
try {
  const endpoint = await new Promise((resolve, reject) => {
    let output = "";
    const timer = setTimeout(
      () => reject(new Error("Browser startup timed out: " + output)),
      15000,
    );
    chrome.stderr.on("data", (chunk) => {
      output += chunk;
      const match = output.match(/DevTools listening on (ws:\/\/\S+)/);
      if (match) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    });
    chrome.on("exit", (code) => {
      clearTimeout(timer);
      reject(new Error("Browser exited " + code + ": " + output));
    });
    chrome.on("error", reject);
  });
  socket = new WebSocket(endpoint);
  await new Promise((resolve) =>
    socket.addEventListener("open", resolve, { once: true }),
  );
  let serial = 0,
    session;
  const pending = new Map(),
    errors = [];
  socket.addEventListener("message", ({ data }) => {
    const msg = JSON.parse(data);
    if (msg.id) {
      const pair = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? pair.reject(msg.error) : pair.resolve(msg.result);
    }
    if (msg.method === "Runtime.exceptionThrown")
      errors.push(msg.params.exceptionDetails);
  });
  function call(method, params = {}, sessionId = session) {
    return new Promise((resolve, reject) => {
      const id = ++serial;
      pending.set(id, { resolve, reject });
      socket.send(JSON.stringify({ id, method, params, sessionId }));
    });
  }
  const { targetId } = await call("Target.createTarget", {
    url: "about:blank",
  });
  session = (await call("Target.attachToTarget", { targetId, flatten: true }))
    .sessionId;
  await call("Runtime.enable");
  await call("Emulation.setDeviceMetricsOverride", {
    width: 1440,
    height: 1000,
    deviceScaleFactor: 1,
    mobile: false,
  });
  const evaluate = async (expression) => {
    const result = await call("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (result.exceptionDetails)
      throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  async function until(expression) {
    for (let i = 0; i < 100; i++) {
      if (await evaluate(expression)) return;
      await new Promise((r) => setTimeout(r, 100));
    }
    throw new Error("Timed out: " + expression);
  }
  const base = process.argv[2];
  await call("Page.navigate", { url: base });
  await until(
    "location.hash==='#home' && document.querySelector('#stats b')?.textContent==='37'",
  );
  await evaluate("localStorage.setItem('pc-saved',JSON.stringify(['old']))");
  await call("Page.reload");
  await until("document.querySelector('#stats b')?.textContent==='37'");
  await call("Page.navigate", {
    url: base + "/#discover?page=2&theme=AI+Infra",
  });
  await until("document.querySelectorAll('#paperGrid .paper').length===7");
  const href = await evaluate(
    "document.querySelector('#paperGrid h3 a').getAttribute('href')",
  );
  assert.match(href, /^#paper\/[0-9a-f]{64}$/);
  await evaluate("scrollTo(0,200)");
  await evaluate("document.querySelector('#paperGrid h3 a').click()");
  await until(
    "document.querySelector('#paperDetail h1') && location.hash.startsWith('#paper/')",
  );
  assert.equal(
    await evaluate(
      "document.querySelector('#paperDetail .detail-abstract').textContent",
    ),
    "An abstract about infrastructure",
  );
  assert.match(
    await evaluate("document.querySelector('.detail-primary').href"),
    /^https:/,
  );
  await evaluate("document.querySelector('#paperBack').click()");
  await until(
    "location.hash.includes('page=2') && document.querySelectorAll('#paperGrid .paper').length===7",
  );
  assert.equal(
    await evaluate("document.querySelector('#themeFilter').value"),
    "AI Infra",
  );
  await evaluate("document.querySelector('#paperGrid .paper').click()");
  await until(
    "location.hash.startsWith('#paper/') && document.querySelector('#detailSave')",
  );
  await evaluate("document.querySelector('#detailSave').click()");
  assert.equal(
    await evaluate(
      "document.querySelector('#detailSave span:last-child').textContent",
    ),
    "已收藏",
  );
  const screenshot = await call("Page.captureScreenshot", { format: "png" });
  await writeFile(
    "/tmp/paper-compass-detail-desktop.png",
    Buffer.from(screenshot.data, "base64"),
  );
  await call("Emulation.setDeviceMetricsOverride", {
    width: 390,
    height: 844,
    deviceScaleFactor: 1,
    mobile: true,
  });
  await evaluate("window.dispatchEvent(new Event('resize'))");
  assert.ok(await evaluate("document.documentElement.scrollWidth<=innerWidth"));
  const mobile = await call("Page.captureScreenshot", { format: "png" });
  await writeFile(
    "/tmp/paper-compass-detail-mobile.png",
    Buffer.from(mobile.data, "base64"),
  );
  await call("Page.navigate", { url: base + "/?direct=1" + href });
  await until("document.querySelector('#paperDetail h1')");
  await evaluate("document.querySelector('#paperBack').click()");
  await until("location.hash==='#discover'");
  await call("Page.navigate", { url: base + "/#library" });
  await until("document.querySelector('#libraryGrid .paper[data-id=old]')");
  await call("Page.navigate", { url: base + "/#docs" });
  await until("document.querySelector('#docArticle h1')");
  assert.deepEqual(errors, []);
  console.log(
    "Browser passed: routing, details, back, blank click, save, old collections, mobile, docs.",
  );
} finally {
  socket?.close();
  chrome.kill();
  await new Promise((resolve) => chrome.once("close", resolve));
  await rm(profile, { recursive: true, force: true });
}
