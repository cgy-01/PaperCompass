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
    if (msg.method === "Fetch.requestPaused")
      void call("Fetch.fulfillRequest", {
        requestId: msg.params.requestId,
        responseCode: 503,
        responseHeaders: [{ name: "Content-Type", value: "application/json" }],
        body: Buffer.from(
          JSON.stringify({ error: "test catalog temporarily unavailable" }),
        ).toString("base64"),
      });
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
  // Cover HTTP/IP browsers where crypto.randomUUID is unavailable.
  await call("Page.addScriptToEvaluateOnNewDocument", {
    source:
      "Object.defineProperty(globalThis.crypto,'randomUUID',{value:undefined,configurable:true})",
  });
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
  // The first screen must work even before the workspace and full icon library
  // arrive. Fail those downloads to model a slow/unavailable dependency chain.
  await call("Fetch.enable", {
    patterns: [{ urlPattern: "*/assets/app.js" }, { urlPattern: "*/lucide.min.js" }],
  });
  await call("Page.navigate", { url: base });
  await until("document.querySelector('.welcome-log-track') && document.querySelectorAll('#welcomeNav svg.lucide-arrow-up-right').length===2");
  assert.equal(await evaluate("getComputedStyle(document.querySelector('.welcome-log-track')).animationDelay"), "0s");
  assert.equal(await evaluate("getComputedStyle(document.querySelector('.welcome-log-track')).animationPlayState"), "running");
  const initialLogTransform = await evaluate("getComputedStyle(document.querySelector('.welcome-log-track')).transform");
  await new Promise((resolve) => setTimeout(resolve, 150));
  assert.notEqual(await evaluate("getComputedStyle(document.querySelector('.welcome-log-track')).transform"), initialLogTransform);
  await call("Fetch.disable");
  await call("Fetch.enable", {
    patterns: [{ urlPattern: "*/api/v1/bootstrap*" }],
  });
  await call("Page.navigate", { url: base });
  await until(
    "location.hash==='#welcome' && document.body.classList.contains('is-welcome')",
  );
  assert.equal(
    await evaluate("getComputedStyle(document.querySelector('.app')).display"),
    "none",
  );
  await evaluate("document.querySelector('.welcome-primary').click()");
  await until(
    "!document.querySelector('#workspaceNotice').hidden && document.querySelector('#workspaceNotice button')",
  );
  await call("Fetch.disable");
  await evaluate("document.querySelector('#workspaceNotice button').click()");
  await until(
    "location.hash==='#home' && document.querySelector('#stats b')?.textContent==='37'",
  );
  await evaluate("history.back()");
  await until(
    "location.hash==='#welcome' && document.querySelector('#welcomeTotal').textContent==='37'",
  );
  await until(
    "document.querySelectorAll('#welcomeAreaList a[data-track]').length===14",
  );
  assert.equal(
    await evaluate(
      "document.querySelectorAll('#overview .institution-showcase').length",
    ),
    0,
  );
  assert.equal(
    await evaluate(
      "document.querySelectorAll('#welcomeCommunity #institutionList .institution-mark').length",
    ),
    3,
  );
  assert.deepEqual(
    await evaluate(
      "[...document.querySelectorAll('#welcomeCommunity .institution-mark')].map(el=>el.title)",
    ),
    ["中山大学", "厦门大学", "北京师范大学"],
  );
  assert.equal(
    await evaluate("document.querySelectorAll('#institutionList').length"),
    1,
  );
  assert.equal(await evaluate("getComputedStyle(document.querySelector('#welcomeCommunity')).display"), 'none');
  assert.ok(
    await evaluate("document.body.classList.contains('welcome-motion-active')"),
  );
  assert.deepEqual(
    await evaluate(`['.welcome-description','.welcome-paper p','.welcome-area-copy small'].map(selector=>{
      const s=getComputedStyle(document.querySelector(selector));return [s.fontSize,s.lineHeight,s.fontWeight];
    })`),
    [
      ["16px", "27.2px", "400"],
      ["15px", "24.75px", "400"],
      ["13px", "19.5px", "400"],
    ],
  );
  assert.ok(
    await evaluate(`getComputedStyle(document.body).fontFamily.includes('PingFang SC') &&
    getComputedStyle(document.querySelector('.welcome-intro h1')).fontFamily.includes('Noto Serif SC')`),
  );
  await evaluate(`(()=>{
    const fixture=document.createElement('div');fixture.id='typographyFixture';
    fixture.innerHTML='<p>中文<strong>LLM</strong>正文，近30天。</p><p class="punctuation-test">中文 <strong>，</strong> English <em>。</em></p><p class="inline-code-test">使用<code>search</code>检索</p><pre>中文API</pre><input value="中文API"><a href="https://example.test/中文API">https://example.test/中文API</a><em>中文强调</em>';
    document.body.appendChild(fixture);
  })()`);
  await until(
    "document.querySelector('#typographyFixture p').textContent==='中文 LLM 正文，近 30 天。'",
  );
  await until(
    "document.querySelector('.punctuation-test').textContent==='中文，English。'",
  );
  await until(
    "document.querySelector('.inline-code-test').textContent==='使用 search 检索'",
  );
  assert.deepEqual(
    await evaluate(`(()=>{
    const f=document.querySelector('#typographyFixture');
    return [f.querySelector('pre').textContent,f.querySelector('input').value,
      f.querySelector('a').getAttribute('href'),f.querySelector('a').textContent,
      getComputedStyle(f.querySelector('strong')).fontWeight,getComputedStyle(f.querySelector('em')).fontStyle];
  })()`),
    [
      "中文API",
      "中文API",
      "https://example.test/中文API",
      "https://example.test/中文API",
      "600",
      "normal",
    ],
  );
  await evaluate("document.querySelector('#typographyFixture').remove()");
  assert.equal(
    await evaluate(
      "document.querySelectorAll('.welcome-log-track pre').length",
    ),
    2,
  );
  assert.ok(
    await evaluate(
      "(()=>{const p=document.querySelectorAll('.welcome-log-track pre');return p[0].textContent===p[1].textContent && p[0].querySelectorAll('code > span').length===20})()",
    ),
  );
  assert.equal(
    await evaluate(
      "getComputedStyle(document.querySelector('.welcome-log-track')).animationDuration",
    ),
    "12s",
  );
  assert.deepEqual(
    await evaluate(`(()=>{
    const terminal=getComputedStyle(document.querySelector('.welcome-terminal'));
    const code=getComputedStyle(document.querySelector('.welcome-terminal pre'));
    const color=selector=>getComputedStyle(document.querySelector(selector)).color;
    const git=[...document.querySelectorAll('.welcome-log-success')].find(el=>el.textContent.startsWith('[main'));
    return [code.fontSize,code.fontWeight,code.lineHeight,code.fontFamily.includes('JetBrains Mono'),
      terminal.borderRadius,terminal.paddingTop,terminal.borderTopWidth,terminal.boxShadow!=='none',
      color('.welcome-log-command'),getComputedStyle(git).color,color('.welcome-log-output')];
  })()`),
    [
      "12px",
      "500",
      "20.4px",
      true,
      "12px",
      "20px",
      "0px",
      true,
      "rgb(217, 133, 80)",
      "rgb(105, 182, 156)",
      "rgb(166, 153, 142)",
    ],
  );
  assert.ok(
    await evaluate(`(()=>{
    const copies=[...document.querySelectorAll('.welcome-log-track pre')];
    return copies.every(el=>Math.abs(el.offsetHeight-408)<1);
  })()`),
  );
  assert.ok(
    await evaluate(`(()=>{
    const shell=getComputedStyle(document.querySelector('.welcome-desk'),'::before');
    return shell.borderRadius==='30px' && shell.backdropFilter==='blur(12px)' &&
      shell.pointerEvents==='none' && shell.zIndex==='-1';
  })()`),
  );
  assert.ok(
    await evaluate(`(()=>{
    const terminal=document.querySelector('.welcome-terminal');
    const bar=terminal.querySelector('.welcome-window-bar');
    const viewport=terminal.querySelector('.welcome-log-viewport');
    const outer=terminal.getBoundingClientRect(),inner=viewport.getBoundingClientRect();
    return !terminal.textContent.includes('训练演示') &&
      Math.abs(inner.top-outer.top)<1 && Math.abs(inner.bottom-outer.bottom)<1 &&
      Math.abs(inner.left-outer.left)<1 && Math.abs(inner.right-outer.right)<1 &&
      getComputedStyle(bar).color===getComputedStyle(document.querySelector('.welcome-loss-window .welcome-window-label')).color &&
      getComputedStyle(bar).fontSize==='13px' &&
      Number(getComputedStyle(bar).zIndex)<Number(getComputedStyle(viewport).zIndex);
  })()`),
  );
  assert.ok(
    await evaluate(
      "[...document.querySelectorAll('.welcome-terminal .welcome-window-dots b')].every((el,i)=>{const s=getComputedStyle(el),c=s.backgroundColor.match(/[\\d.]+/g).map(Number),label=getComputedStyle(document.querySelector('.welcome-terminal .welcome-window-bar')).color.match(/[\\d.]+/g).map(Number);return c.slice(0,3).join(',')===['255,95,87','254,188,46','40,200,64'][i] && Math.abs(c[3]-label[3])<.005 && s.width==='10px' && s.height==='10px'})",
    ),
  );
  await until(
    "document.querySelector('#welcomeRecentCount').textContent==='近 30 天新增 · 0 篇'",
  );
  await call("Emulation.setEmulatedMedia", {
    features: [{ name: "prefers-reduced-motion", value: "reduce" }],
  });
  await until("!document.body.classList.contains('welcome-motion-active')");
  assert.ok(
    await evaluate(
      "document.querySelector('#welcome').getAnimations({subtree:true}).every(a=>a.playState!=='running')",
    ),
  );
  assert.equal(
    await evaluate(
      "getComputedStyle(document.querySelector('.welcome-intro')).opacity",
    ),
    "1",
  );
  await call("Emulation.setEmulatedMedia", {
    features: [{ name: "prefers-reduced-motion", value: "no-preference" }],
  });
  await until("document.body.classList.contains('welcome-motion-active')");
  assert.deepEqual(
    await evaluate(
      `['.welcome-intro h1', '.welcome-description', '.welcome-tape', '.welcome-cta', '.welcome-metrics', '.welcome-desk', '.welcome-scroll-cue'].map(s=>getComputedStyle(document.querySelector(s)).animationDelay)`,
    ),
    ["0.55s", "1.05s", "1.5s", "1.5s", "1.5s", "1.5s", "1.5s"],
  );
  assert.deepEqual(
    await evaluate(
      `['.welcome-intro h1', '.welcome-description', '.welcome-desk'].map(s=>getComputedStyle(document.querySelector(s)).animationDuration)`,
    ),
    ["1s", "0.85s", "0.7s"],
  );
  // Sample a fixed animation time rather than relying on real-time screenshots.
  // At 0.8s the title is entering while the remaining two groups stay hidden.
  await evaluate(`document.querySelector('.welcome-hero').getAnimations({subtree:true})
    .filter(a=>a.animationName==='welcome-hero-enter')
    .forEach(a=>{a.pause();a.currentTime=800})`);
  const titleMotion = await evaluate(`(()=>{
    const title=getComputedStyle(document.querySelector('.welcome-intro h1'));
    return {opacity:Number(title.opacity),blur:parseFloat(title.filter.slice(5)),
      description:Number(getComputedStyle(document.querySelector('.welcome-description')).opacity),
      desk:Number(getComputedStyle(document.querySelector('.welcome-desk')).opacity)};
  })()`);
  assert.ok(titleMotion.opacity > 0.4 && titleMotion.opacity < 0.8);
  assert.ok(titleMotion.blur > 0 && titleMotion.blur < 4);
  assert.equal(titleMotion.description, 0);
  assert.equal(titleMotion.desk, 0);
  assert.ok(
    await evaluate(`(()=>{
    const cue=document.querySelector('.welcome-scroll-cue');
    const animation=cue.getAnimations().find(a=>a.animationName==='welcome-hero-enter');
    const hero=document.querySelector('.welcome-hero').getBoundingClientRect();
    return [800,1500,1700,2199,2201].every(time=>{
      animation.currentTime=time;
      const rect=cue.getBoundingClientRect();
      return Math.abs(rect.left+rect.width/2-(hero.left+hero.width/2))<1;
    });
  })()`),
  );
  const titleEntry = await call("Page.captureScreenshot", { format: "png" });
  await writeFile(
    "/tmp/paper-compass-welcome-title-entry.png",
    Buffer.from(titleEntry.data, "base64"),
  );
  await evaluate(`document.querySelector('.welcome-hero').getAnimations({subtree:true})
    .filter(a=>a.animationName==='welcome-hero-enter').forEach(a=>a.finish())`);
  assert.equal(
    await evaluate(
      "getComputedStyle(document.querySelector('.welcome-hero'),'::before').animationName",
    ),
    "none",
  );
  assert.equal(
    await evaluate(
      "getComputedStyle(document.querySelector('#welcomeNav')).animationName",
    ),
    "none",
  );
  await until(
    "document.querySelector('.welcome-hero').getAnimations({subtree:true}).filter(a=>a.animationName==='welcome-hero-enter').length===0",
  );
  assert.ok(
    await evaluate(`(async()=>{
    const desk=await import('/assets/pages/welcome-desk.js');
    const terminal=document.querySelector('.welcome-terminal');
    const selectors=['.welcome-intro h1','.welcome-terminal','.welcome-loss-window','.welcome-desk-paper'];
    const rects=()=>selectors.map(s=>document.querySelector(s).getBoundingClientRect());
    terminal.style.height='252px';terminal.style.marginTop='0px';
    const before=rects();terminal.style.removeProperty('height');terminal.style.removeProperty('margin-top');desk.renderWelcomeDesk();const after=rects();
    const row=parseFloat(getComputedStyle(terminal.querySelector('pre')).lineHeight);
    return Math.abs(after[1].height-row*10)<1 && Math.abs(after[1].height-204)<1 &&
      Math.abs(before[1].bottom-after[1].bottom)<1 &&
      [0,2,3].every(i=>Math.abs(before[i].top-after[i].top)<1);
  })()`),
  );
  const welcome = await call("Page.captureScreenshot", { format: "png" });
  assert.ok(
    await evaluate(`(()=>{
    const nav=document.querySelector('.welcome-nav').getBoundingClientRect();
    const hero=document.querySelector('.welcome-hero-inner').getBoundingClientRect();
    return Math.abs(hero.width/nav.width-.96)<.005 && Math.abs((hero.left-nav.left)-(nav.right-hero.right))<1;
  })()`),
  );
  await writeFile(
    "/tmp/paper-compass-welcome-desktop.png",
    Buffer.from(welcome.data, "base64"),
  );
  await call("Emulation.setDeviceMetricsOverride", {
    width: 1920,
    height: 1080,
    deviceScaleFactor: 1,
    mobile: false,
  });
  assert.ok(
    await evaluate(`(()=>{
    const hero=document.querySelector('.welcome-hero-inner');
    const desk=document.querySelector('.welcome-desk').getBoundingClientRect();
    const loss=document.querySelector('.welcome-loss-window').getBoundingClientRect();
    return getComputedStyle(hero).columnGap==='64px' &&
      desk.width>490 && desk.width<500 && Math.abs(desk.left-loss.left-18)<1 &&
      document.documentElement.scrollWidth<=innerWidth;
  })()`),
  );
  const wide = await call("Page.captureScreenshot", { format: "png" });
  await writeFile(
    "/tmp/paper-compass-welcome-wide.png",
    Buffer.from(wide.data, "base64"),
  );
  await call("Emulation.setDeviceMetricsOverride", {
    width: 390,
    height: 844,
    deviceScaleFactor: 1,
    mobile: true,
  });
  assert.ok(await evaluate("document.documentElement.scrollWidth<=innerWidth"));
  assert.equal(
    await evaluate(
      "getComputedStyle(document.querySelector('.welcome-terminal pre')).fontSize",
    ),
    "12px",
  );
  assert.equal(
    await evaluate(
      "getComputedStyle(document.querySelector('.welcome-desk'),'::before').backdropFilter",
    ),
    "none",
  );
  assert.ok(
    await evaluate(
      `Math.abs(document.querySelector('.welcome-hero-inner').getBoundingClientRect().width-document.querySelector('.welcome-nav').getBoundingClientRect().width)<1`,
    ),
  );
  assert.equal(
    await evaluate(
      "getComputedStyle(document.querySelector('.welcome-description')).fontSize",
    ),
    "16px",
  );
  assert.equal(
    await evaluate(
      "getComputedStyle(document.querySelector('.welcome-intro h1')).getPropertyValue('--welcome-enter-blur').trim()",
    ),
    "0px",
  );
  const welcomeMobile = await call("Page.captureScreenshot", { format: "png" });
  await writeFile(
    "/tmp/paper-compass-welcome-mobile.png",
    Buffer.from(welcomeMobile.data, "base64"),
  );
  await evaluate(
    "document.querySelector('#welcomeAreas').scrollIntoView({block:'start',behavior:'auto'})",
  );
  await evaluate(
    "new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))",
  );
  await until(
    "document.querySelector('#welcomeAreas').getAnimations({subtree:true}).length===0",
  );
  assert.ok(await evaluate("document.documentElement.scrollWidth<=innerWidth"));
  const areaMobile = await call("Page.captureScreenshot", { format: "png" });
  await writeFile(
    "/tmp/paper-compass-welcome-areas-mobile.png",
    Buffer.from(areaMobile.data, "base64"),
  );
  await call("Emulation.setDeviceMetricsOverride", {
    width: 1440,
    height: 1000,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await evaluate(
    "document.querySelector('[data-welcome-scroll=welcomeAreas]').click()",
  );
  await until(
    "Math.abs(document.querySelector('#welcomeAreas').getBoundingClientRect().top-98)<10",
  );
  await until(
    "document.querySelector('#welcomeAreaList a').dataset.motionKnown==='true'",
  );
  await until(
    "document.querySelector('#welcomeAreaList').getAnimations({subtree:true}).length===0",
  );
  const areaDesktop = await call("Page.captureScreenshot", { format: "png" });
  await writeFile(
    "/tmp/paper-compass-welcome-areas-desktop.png",
    Buffer.from(areaDesktop.data, "base64"),
  );
  await evaluate(
    "document.querySelector('[data-welcome-scroll=welcomeResearch]').click()",
  );
  await until(
    "Math.abs(document.querySelector('#welcomeResearch').getBoundingClientRect().top-98)<10",
  );
  await evaluate(
    "new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))",
  );
  await until(
    "document.querySelector('#welcomeResearch').getAnimations({subtree:true}).length===0",
  );
  assert.ok(
    await evaluate(`(()=>{
    const firstByColumn=new Map();
    for(const pin of document.querySelectorAll('#welcomePreview .welcome-paper-pin')){
      const r=pin.getBoundingClientRect(),column=Math.round(r.left);
      if(!firstByColumn.has(column)||r.top<firstByColumn.get(column).getBoundingClientRect().top)firstByColumn.set(column,pin);
    }
    return firstByColumn.size===3 && [...firstByColumn.values()].every(pin=>{
      const r=pin.getBoundingClientRect();
      return document.elementFromPoint(r.left+r.width/2,r.top+1)?.closest('.welcome-paper-pin')===pin;
    });
  })()`),
  );
  const researchShot = await call("Page.captureScreenshot", { format: "png" });
  await writeFile(
    "/tmp/paper-compass-welcome-research.png",
    Buffer.from(researchShot.data, "base64"),
  );
  await evaluate(
    "document.querySelector('#welcomeAreaList [data-track=\"research_area:ai_infra\"]').click()",
  );
  await until(
    "location.hash==='#discover?theme=AI+Infra' && document.querySelectorAll('#paperGrid .paper').length===30",
  );
  assert.equal(
    await evaluate("document.querySelector('#themeFilter').value"),
    "AI Infra",
  );
  await evaluate("history.back()");
  await until(
    "location.hash==='#welcome' && document.body.classList.contains('is-welcome')",
  );
  await evaluate(
    "document.querySelector('[data-welcome-scroll=welcomeWorkflow]').click()",
  );
  await until(
    "Math.abs(document.querySelector('#welcomeWorkflow').getBoundingClientRect().top-98)<10",
  );
  assert.equal(await evaluate("location.hash"), "#welcome");
  await until(
    "!document.querySelector('#welcomeSticky').hidden && document.body.classList.contains('is-welcome-scrolled')",
  );
  const workflowShot = await call("Page.captureScreenshot", { format: "png" });
  await writeFile(
    "/tmp/paper-compass-welcome-workflow.png",
    Buffer.from(workflowShot.data, "base64"),
  );
  await evaluate(
    "document.querySelector('[data-welcome-scroll=welcomeMCP]').click()",
  );
  await until(
    "Math.abs(document.querySelector('#welcomeMCP').getBoundingClientRect().top-98)<10",
  );
  assert.ok(
    await evaluate(
      "document.querySelector('#welcomeMCP').textContent.includes('读取摘要')",
    ),
  );
  await evaluate(
    "scrollTo(0,document.querySelector('.welcome-footer').getBoundingClientRect().top+scrollY-innerHeight-20)",
  );
  await until(
    "document.querySelector('#welcomeFinal').getBoundingClientRect().top<innerHeight && !document.querySelector('#welcomeSticky').hidden",
  );
  await evaluate(
    "scrollTo(0,document.querySelector('.welcome-footer').getBoundingClientRect().top+scrollY-innerHeight+20)",
  );
  await until(
    "document.querySelector('.welcome-footer').getBoundingClientRect().top<innerHeight && document.querySelector('#welcomeSticky').hidden",
  );
  await evaluate(
    "scrollTo(0,document.querySelector('.welcome-footer').getBoundingClientRect().top+scrollY-innerHeight-20)",
  );
  await until("!document.querySelector('#welcomeSticky').hidden");
  await evaluate("document.querySelector('.welcome-primary').click()");
  await until(
    "location.hash==='#home' && !document.body.classList.contains('is-welcome')",
  );
  assert.ok(
    await evaluate(
      "!document.body.classList.contains('welcome-motion-active') && document.querySelector('#welcome').getAnimations({subtree:true}).every(a=>a.playState!=='running')",
    ),
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
  assert.ok(
    await evaluate(`(()=>{
    const nav=document.querySelector('#mobileNav').getBoundingClientRect();
    return [...document.querySelectorAll('#mobileNav .nav-label')].every(el=>{
      const r=el.getBoundingClientRect();return r.top>=nav.top && r.bottom<=nav.bottom && el.scrollHeight<=el.clientHeight+1;
    });
  })()`),
  );
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
  assert.deepEqual(
    await evaluate(`(()=>{
    const p=document.querySelector('.doc-article p'),s=getComputedStyle(p);
    return [s.fontSize,s.lineHeight,p.clientWidth<=parseFloat(s.fontSize)*36+1];
  })()`),
    ["16px", "27.2px", true],
  );
  if (process.env.PAPER_COMPASS_TEST_LOGIN) {
    await call("Network.setCookie", {
      name: "pc_session",
      value: process.env.PAPER_COMPASS_TEST_LOGIN,
      url: base,
      path: "/",
    });
    await call("Page.navigate", { url: base + "/?signed=1#account" });
    await until(
      "document.querySelector('#accountSlot')?.textContent.includes('browser-researcher') && document.querySelector('#importLocal')",
    );
    assert.deepEqual(
      await evaluate(
        "fetch('/api/account/favorites').then(r=>r.json()).then(d=>d.ids)",
      ),
      ["new"],
    );
    assert.ok(
      await evaluate(
        "JSON.parse(localStorage.getItem('pc-saved')).includes('old')",
      ),
    );
    await evaluate("document.querySelector('#importLocal').click()");
    await until(
      "document.querySelector('#accountNotice')?.textContent==='本机文献记录已导入'",
    );
    assert.ok(
      (
        await evaluate(
          "fetch('/api/account/favorites').then(r=>r.json()).then(d=>d.ids)",
        )
      ).includes("old"),
    );
    await call("Page.navigate", { url: base + "/#discover" });
    await until(
      "document.querySelector('#paperGrid .paper[data-id=new] .save.on')",
    );
    await evaluate(
      "document.querySelector('#paperGrid .paper[data-id=new] .save').click()",
    );
    await until(
      "document.querySelector('#paperGrid .paper[data-id=new] .save:not(.on)')",
    );
    assert.ok(
      !(
        await evaluate(
          "fetch('/api/account/favorites').then(r=>r.json()).then(d=>d.ids)",
        )
      ).includes("new"),
    );
    await evaluate(
      "document.querySelector('#paperGrid .paper[data-id=new] h3 a').click()",
    );
    await until("document.querySelector('#paperDetail h1')");
    await until(
      "fetch('/api/account/reading').then(r=>r.json()).then(d=>d.ids.includes('new'))",
    );
    await evaluate("import('/assets/analytics.js').then(m=>m.flush())");
    await call("Page.navigate", { url: base + "/#account" });
    await until("document.querySelector('#analyticsPreference')");
    await evaluate("document.querySelector('#analyticsPreference').click()");
    assert.equal(
      await evaluate("localStorage.getItem('pc-analytics-off')"),
      "1",
    );
    await evaluate("document.querySelector('#accountLogout').click()");
    await until("document.querySelector('.account-login')");
    assert.equal(
      await evaluate("fetch('/api/account').then(r=>r.json()).then(d=>d.user)"),
      null,
    );
  }
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
