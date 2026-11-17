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
  let providerRequests = 0;
  socket.addEventListener("message", ({ data }) => {
    const msg = JSON.parse(data);
    if (msg.id) {
      const pair = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? pair.reject(msg.error) : pair.resolve(msg.result);
    }
    if (msg.method === "Runtime.exceptionThrown")
      errors.push(msg.params.exceptionDetails);
    if (msg.method === "Fetch.requestPaused") {
      if (msg.params.request.url.startsWith('https://avatars.githubusercontent.com/')) {
        const failed = new URL(msg.params.request.url).searchParams.has('broken');
        void call('Fetch.fulfillRequest', {
          requestId: msg.params.requestId, responseCode: failed ? 404 : 200,
          responseHeaders: [{name:'Content-Type',value:'image/png'}],
          body: failed ? '' : 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aU1sAAAAASUVORK5CYII=',
        });
        return;
      }
      if (msg.params.request.url.startsWith('https://github.com/login/oauth/authorize?')) {
        providerRequests++;
        const state = new URL(msg.params.request.url).searchParams.get('state');
        void call('Fetch.fulfillRequest', {
          requestId: msg.params.requestId, responseCode: 302,
          responseHeaders: [{name:'Location',value:process.argv[2] + '/auth/github/callback?code=browser-fixture&state=' + encodeURIComponent(state)}],
        });
        return;
      }
      void call("Fetch.fulfillRequest", {
        requestId: msg.params.requestId,
        responseCode: 503,
        responseHeaders: [{ name: "Content-Type", value: "application/json" }],
        body: Buffer.from(
          JSON.stringify({ error: "test catalog temporarily unavailable" }),
        ).toString("base64"),
      });
    }
  });
  async function call(method, params = {}, sessionId = session) {
    if (method === "Page.captureScreenshot" && process.env.PAPER_COMPASS_VISUAL_FREEZE) {
      const frozen = await call("Runtime.evaluate", {expression: `(()=>{
        const stamp=document.querySelector('#generated');
        if(stamp) stamp.textContent='2026-11-15 00:00（UTC+8）';
        document.getAnimations().forEach(animation=>{
          if(['welcome-log-scroll','welcome-source-scroll'].includes(animation.animationName)) {
            animation.pause(); animation.currentTime=3000;
          } else if(!animation.animationName) animation.finish();
        });
        return new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))
      })()`, awaitPromise:true}, sessionId);
      if (frozen.exceptionDetails) throw new Error(JSON.stringify(frozen.exceptionDetails));
    }
    if (method === 'Fetch.disable') {
      method = 'Fetch.enable'; params = {patterns: []};
    }
    if (method === 'Fetch.enable') params = {...params, patterns: [
      ...(params.patterns || []), {urlPattern:'https://github.com/login/oauth/authorize*'},
      {urlPattern:'https://avatars.githubusercontent.com/*'},
    ]};
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
      try {
        if (await evaluate(expression)) return;
      } catch (error) {
        // Native authorization links reload the document. An old readiness
        // expression may briefly run against a fresh, not-yet-parsed document.
        const detail = error.message || JSON.stringify(error);
        if (!/Cannot read properties of null|Execution context was destroyed|Cannot find context/.test(detail)) throw error;
      }
      await new Promise((r) => setTimeout(r, 100));
    }
    const diagnostic = await evaluate(`JSON.stringify({hash:location.hash,scrollY,
      viewport:{width:innerWidth,height:innerHeight},
      workflowTop:document.querySelector('#welcomeWorkflow')?.getBoundingClientRect().top,
      researchTop:document.querySelector('#welcomeResearch')?.getBoundingClientRect().top,
      body:document.body.className})`);
    throw new Error("Timed out: " + expression + "\n" + diagnostic);
  }
  const base = process.argv[2];
  if (process.env.PAPER_COMPASS_TEST_DEV_LOGIN) {
    await call('Page.navigate', {url: base});
    await until("document.querySelector('#accountSlot')?.textContent.includes('local-developer')");
    const developer = await evaluate("fetch('/api/account').then(r=>r.json())");
    assert.equal(developer.devMode, true);
    assert.equal(developer.user.name, '本地开发账号');
    await evaluate("document.querySelector('.welcome-primary').click()");
    await until("location.hash==='#home' && document.querySelector('#stats b')?.textContent==='2'");
    assert.equal(providerRequests,0);
    await evaluate("location.hash='#discover'");
    await until("document.querySelector('#paperGrid .paper .save:not(.on)')");
    await evaluate("document.querySelector('#paperGrid .paper .save').click()");
    await until("document.querySelector('#paperGrid .paper .save.on')");
    await evaluate("document.querySelector('#paperGrid .paper h3 a').click()");
    await until("document.querySelector('#detailRead')");
    await evaluate("document.querySelector('#detailRead').click()");
    await until("document.querySelector('#detailRead').getAttribute('aria-pressed')==='true'");
    await call('Page.reload');
    await until("document.querySelector('#detailRead')?.getAttribute('aria-pressed')==='true' && document.querySelector('#detailSave')?.getAttribute('aria-pressed')==='true'");
    await evaluate("location.hash='#account'");
    await until("document.querySelector('#accountSyncStatus')?.textContent.includes('已保存到本地开发账号')");
    assert.equal(await evaluate("document.querySelector('.account-profile h2').textContent"),'本地开发账号');
    assert.equal(await evaluate("document.querySelectorAll('.account-avatar img').length"),0);
    assert.equal(await evaluate("document.querySelectorAll('.account-profile a[href^=\"https://github.com/\"]').length"),0);
    await call('Network.deleteCookies', {name:'pc_dev_session',url:base});
    await call('Page.reload');
    await until("document.querySelector('#accountSyncStatus')?.textContent.includes('已保存到本地开发账号')");
    assert.equal((await evaluate("fetch('/api/account').then(r=>r.json())")).user.id,developer.user.id);
    await evaluate("location.hash='#library'");
    await until("document.querySelectorAll('#libraryGrid .paper').length===1");
    assert.ok(await evaluate("document.querySelector('#libraryScope').textContent.includes('本地开发账号')"));
    assert.equal(providerRequests,0);
    assert.deepEqual(errors,[]);
    console.log('Developer account browser passed: automatic session, no GitHub, favorites, reading, persistence.');
  } else {
  // The first screen must work even before the workspace and full icon library
  // arrive. Fail those downloads to model a slow/unavailable dependency chain.
  await call("Fetch.enable", {
    patterns: [{ urlPattern: "*/app.js" }, { urlPattern: "*/lucide.min.js" }],
  });
  await call("Page.navigate", { url: base });
  await until("document.querySelector('.welcome-log-track') && document.querySelectorAll('#welcomeNav svg.lucide-arrow-up-right').length===2");
  assert.equal(await evaluate("document.querySelector('.welcome-nav-enter').textContent.trim()"),'登录');
  assert.equal(await evaluate("document.querySelector('.welcome-primary').textContent.trim()"),'进入工作区');
  assert.equal(await evaluate("[...document.querySelectorAll('#welcome a')].filter(a=>a.getAttribute('href').startsWith('#') && a.getAttribute('href')!=='#welcome').length"),0);
  assert.equal(await evaluate("getComputedStyle(document.querySelector('.welcome-log-track')).animationDelay"), "0s");
  assert.equal(await evaluate("getComputedStyle(document.querySelector('.welcome-log-track')).animationPlayState"), "running");
  const initialLogTransform = await evaluate("getComputedStyle(document.querySelector('.welcome-log-track')).transform");
  await new Promise((resolve) => setTimeout(resolve, 150));
  assert.notEqual(await evaluate("getComputedStyle(document.querySelector('.welcome-log-track')).transform"), initialLogTransform);
  assert.equal(await evaluate("performance.getEntriesByType('resource').some(r=>r.name.includes('/api/v1/bootstrap'))"), false);
  await call("Fetch.disable");
  await call("Fetch.enable", {
    patterns: [{ urlPattern: "*/api/v1/bootstrap*" }],
  });
  await call("Page.navigate", { url: base });
  await until(
    "location.hash==='#welcome' && document.body.classList.contains('is-welcome')",
  );
  assert.equal(await evaluate("performance.getEntriesByType('resource').some(r=>/pages\\/(home|discover|docs|library|paper)\\.js/.test(r.name))"), false);
  assert.equal(await evaluate("performance.getEntriesByType('resource').some(r=>r.name.endsWith('/lucide.min.js'))"), false);
  assert.equal(await evaluate("document.querySelector('#welcome [data-lucide=\"align-left\"]').tagName.toLowerCase()"), "svg");
  assert.ok(await evaluate(`(()=>{
    const original=document.querySelector('#welcomeNav svg');
    return import(document.querySelector('script[src$="/app.js"]').src.replace('/app.js','/components/ui.js'))
      .then(ui=>{ui.refreshIcons(document);ui.refreshIcons(document);return original===document.querySelector('#welcomeNav svg')});
  })()`));
  assert.equal(
    await evaluate("getComputedStyle(document.querySelector('.app')).display"),
    "none",
  );
  await evaluate("document.querySelector('.welcome-primary').click()");
  await until(
    "document.querySelector('#workspaceNotice') && !document.querySelector('#workspaceNotice').hidden && document.querySelector('#workspaceNotice button')",
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
    "document.querySelector('#welcomeRecentCount').textContent==='近 30 天发表 · 0 篇'",
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
  const beforeSignedEntry = providerRequests;
  await evaluate(
    "document.querySelector('#welcomeAreaList [data-track=\"research_area:ai_infra\"]').click()",
  );
  await until(
    "location.hash==='#discover?theme=AI+Infra' && document.querySelectorAll('#paperGrid .paper').length===30",
  );
  assert.equal(providerRequests, beforeSignedEntry);
  assert.equal(
    await evaluate("document.querySelector('#themeFilter').value"),
    "AI Infra",
  );
  await evaluate("history.back()");
  await until(
    "location.hash==='#welcome' && document.body.classList.contains('is-welcome')",
  );
  // The route class changes before its render and animation-frame restoration.
  // Let those frames finish before starting another independent smooth scroll.
  await evaluate(
    "new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))",
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
  await call('Network.deleteCookies',{name:'pc_session',url:base});
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
  await evaluate("document.querySelector('#libraryGrid .paper[data-id=old] h3 a').click()");
  await until("document.querySelector('#paperDetail h1') && JSON.parse(localStorage.getItem('pc-visited')||'[]').at(-1)==='old'");
  await evaluate("location.hash='#library?mode=visited'");
  await until("document.querySelector('#libraryGrid .paper')?.dataset.id==='old'");
  assert.ok(await evaluate("document.querySelector('#libraryScope').textContent.includes('按浏览顺序排列')"));
  await call("Page.navigate", { url: base + "/#docs" });
  await until("document.querySelector('#docArticle h1')");
  assert.deepEqual(
    await evaluate(`(()=>{
    const p=document.querySelector('.doc-article p'),s=getComputedStyle(p);
    return [s.fontSize,s.lineHeight,p.clientWidth<=parseFloat(s.fontSize)*36+1];
  })()`),
    ["16px", "27.2px", true],
  );
  // Product experience: composition-safe search and recoverable filter states.
  await call("Page.navigate", { url: base + "/#discover" });
  await until("document.querySelectorAll('#paperGrid .paper').length===30");
  await evaluate(`(()=>{
    const input=document.querySelector('#globalSearch');
    input.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));
    input.value='qiang'; input.dispatchEvent(new InputEvent('input',{bubbles:true,isComposing:true}));
    input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',keyCode:229,isComposing:true,bubbles:true,cancelable:true}));
  })()`);
  await new Promise(resolve=>setTimeout(resolve,350));
  assert.equal(await evaluate("location.hash"), "#discover");
  await evaluate(`(()=>{
    const input=document.querySelector('#globalSearch');input.value='infrastructure';
    input.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true}));
    input.dispatchEvent(new InputEvent('input',{bubbles:true,isComposing:false}));
  })()`);
  await until("new URLSearchParams(location.hash.split('?')[1]).get('q')==='infrastructure' && document.querySelector('#paperGrid').getAttribute('aria-busy')==='false'");
  await evaluate("document.querySelector('#globalSearch').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}))");
  await until("location.hash==='#discover' && document.querySelectorAll('#paperGrid .paper').length===30");
  assert.equal(await evaluate("document.querySelector('#typeFilter').tabIndex"), -1);
  await evaluate("document.querySelector('#typeFilter-picker-trigger').focus()");
  const key=async value=>{await call('Input.dispatchKeyEvent',{type:'keyDown',key:value});await call('Input.dispatchKeyEvent',{type:'keyUp',key:value});};
  await key('ArrowDown');
  assert.equal(await evaluate("document.querySelector('#typeFilter-picker-trigger').getAttribute('aria-expanded')"), 'true');
  await key('ArrowDown');await key('Escape');
  assert.equal(await evaluate("document.querySelector('#typeFilter').value"), '');
  assert.equal(await evaluate("document.activeElement.id"), 'typeFilter-picker-trigger');
  await key('ArrowDown');await key('End');await key('Enter');
  await until("document.querySelector('#typeFilter').value==='预印本' && document.querySelector('#paperGrid [data-relax=type]') && document.querySelector('#paperGrid').getAttribute('aria-busy')==='false'");
  assert.equal(await evaluate("document.querySelectorAll('#paperGrid .paper').length"),0);
  assert.ok(await evaluate("document.querySelector('#paperGrid [data-relax=type]')!==null"));
  await evaluate("document.querySelector('#discoverScope [data-relax=type]').click()");
  await until("document.querySelectorAll('#paperGrid .paper').length===30");
  await evaluate("globalThis.pcRetainedPaper=document.querySelector('#paperGrid .paper')");
  await call('Fetch.enable',{patterns:[{urlPattern:'*/api/v1/papers*'}]});
  await evaluate("document.querySelector('#globalSearch').value='test-unavailable';document.querySelector('#paperSearch').requestSubmit()");
  await until("document.querySelector('#discoverFeedback').textContent.includes('已保留上次结果')");
  assert.ok(await evaluate("globalThis.pcRetainedPaper===document.querySelector('#paperGrid .paper')"));
  assert.ok(await evaluate("!document.querySelector('#discoverFeedback').textContent.includes('503')"));
  await call('Fetch.disable');
  await evaluate("document.querySelector('#discoverFeedback [data-retry]').click()");
  await until("document.querySelector('#paperGrid [data-relax=query]')");
  await evaluate("document.querySelector('#paperGrid [data-relax=query]').click()");
  await until("document.querySelectorAll('#paperGrid .paper').length===30");
  assert.ok(await evaluate("document.querySelector('#paperGrid .save').getBoundingClientRect().width>=44"));
  // A storage error must never show a favorite as successfully saved.
  const favoriteBefore = await evaluate("document.querySelector('#paperGrid .save').getAttribute('aria-pressed')");
  await evaluate(`(()=>{
    globalThis.pcSetItem=Storage.prototype.setItem;
    Storage.prototype.setItem=function(key,value){
      if(key==='pc-saved') throw new DOMException('storage blocked','QuotaExceededError');
      return globalThis.pcSetItem.call(this,key,value);
    };
    document.querySelector('#paperGrid .save').click();
  })()`);
  await until("document.querySelector('#accountNotice')?.textContent.includes('浏览器未能保存记录')");
  assert.equal(await evaluate("document.querySelector('#paperGrid .save').getAttribute('aria-pressed')"),favoriteBefore);
  await evaluate("Storage.prototype.setItem=globalThis.pcSetItem;delete globalThis.pcSetItem");
  await evaluate("location.hash='#venues'");
  await until("document.querySelector('.venue-series summary')");
  await evaluate("document.querySelector('.venue-series summary').click()");
  assert.equal(await evaluate("document.querySelector('.venue-series').open"),true);
  assert.equal(await evaluate("document.querySelector('[data-source]').tagName"),'A');
  assert.ok(await evaluate("document.querySelector('[data-source]').getAttribute('href').includes('source=ICLR+2026')"));
  await evaluate("location.hash='#topics'");
  await until("document.querySelector('#topicGrid .topic-row')");
  assert.equal(await evaluate("document.querySelector('#topicGrid .topic-row').tagName"),'A');
  await evaluate("document.querySelector('#skipContent').click()");
  assert.equal(await evaluate("document.activeElement.tagName"),'H1');
  if (process.env.PAPER_COMPASS_TEST_LOGIN) {
    // Preserve a reading record from an older anonymous browser session.
    await evaluate("localStorage.setItem('pc-read',JSON.stringify(['old']))");
    // Exercise a real browser authorization round trip with only GitHub mocked.
    await evaluate("location.hash='#discover?theme=AI+Infra&q=infrastructure'");
    await until("document.querySelectorAll('#paperGrid .paper').length===30 && document.querySelector('#paperGrid').getAttribute('aria-busy')==='false'");
    assert.equal(await evaluate("new URL(document.querySelector('#accountSlot a').href).pathname"),'/auth/github/login');
    assert.equal(await evaluate("new URL(document.querySelector('#accountSlot a').href).searchParams.get('return_to')"),'#discover?theme=AI+Infra&q=infrastructure');
    assert.equal(await evaluate("document.querySelector('#login')"),null);
    await evaluate("document.querySelector('#accountSlot a').focus()");
    await key('Enter');
    await until("location.hash==='#discover?theme=AI+Infra&q=infrastructure' && document.querySelector('#accountSlot')?.textContent.includes('browser-researcher')");
    await call("Page.navigate", { url: base + "/?signed=1#account" });
    await until(
      "document.querySelector('#accountSlot')?.textContent.includes('browser-researcher') && document.querySelector('#importLocal')",
    );
    assert.ok(await evaluate("document.querySelector('#accountSyncStatus').textContent.includes('已同步到账号')"));
    await until("document.querySelectorAll('.account-avatar img.is-loaded').length===2");
    assert.deepEqual(await evaluate(`['#accountSlot .account-avatar','.account-profile .account-avatar'].map(selector=>{
      const avatar=document.querySelector(selector), image=avatar.querySelector('img');
      return [avatar.offsetWidth,avatar.offsetHeight,image.alt,image.referrerPolicy,getComputedStyle(image).opacity];
    })`), [[24,24,'','no-referrer','1'],[48,48,'','no-referrer','1']]);
    assert.ok(await evaluate(`(async()=>{
      const {avatar}=await import(new URL('components/avatar.js',document.querySelector('script[src$="/app.js"]').src));
      return ['', 'javascript:alert(1)', 'https://example.com/avatar.png',
        'https://avatars.githubusercontent.com.evil.test/u/42',
        'http://avatars.githubusercontent.com/u/42',
        'https://user:password@avatars.githubusercontent.com/u/42'].every(avatar_url=>!avatar({avatar_url}).includes('<img'));
    })()`));
    await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
    assert.equal(await evaluate("document.querySelector('#accountSlot .account-avatar').getBoundingClientRect().width"),24);
    await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
    await evaluate("document.querySelector('.account-profile .account-avatar img').src+='&broken=1'");
    await until("!document.querySelector('.account-profile .account-avatar img')");
    assert.ok(await evaluate("document.querySelector('.account-profile .account-avatar svg') && document.querySelector('.account-profile .account-avatar').offsetWidth===48"));
    const accountDesktop = await call('Page.captureScreenshot',{format:'png'});
    await writeFile('/tmp/paper-compass-account-desktop.png',Buffer.from(accountDesktop.data,'base64'));
    await call('Fetch.enable',{patterns:[{urlPattern:'*/api/account/favorites*'}]});
    await evaluate("document.querySelector('#accountSync').click()");
    await until("document.querySelector('#accountSyncStatus').textContent.includes('账号操作暂未完成')");
    assert.ok(await evaluate("!document.querySelector('#accountSyncStatus').textContent.includes('已同步到账号') && document.querySelector('#importLocal').disabled"));
    await call('Fetch.disable');
    await evaluate("document.querySelector('#accountSync').click()");
    await until("document.querySelector('#accountSyncStatus').textContent.includes('已同步到账号') && !document.querySelector('#importLocal').disabled");
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
    await call('Fetch.enable',{patterns:[{urlPattern:'*/api/account/reading'}]});
    await evaluate("document.querySelector('#importLocal').click()");
    await until("document.querySelector('#importStatus')?.textContent.includes('已成功导入的记录已保留')");
    assert.ok((await evaluate("fetch('/api/account/favorites').then(r=>r.json()).then(d=>d.ids)")).includes('old'));
    await call('Fetch.disable');
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
    await until("document.querySelector('#detailRead') && !document.querySelector('#detailRead').disabled");
    assert.equal(await evaluate("fetch('/api/account/reading').then(r=>r.json()).then(d=>d.ids.includes('new'))"), false);
    await evaluate("document.querySelector('#detailRead').click()");
    await until(
      "fetch('/api/account/reading').then(r=>r.json()).then(d=>d.ids.includes('new'))",
    );
    await until("document.querySelector('#detailRead').getAttribute('aria-pressed')==='true'");
    await call('Fetch.enable',{patterns:[{urlPattern:'*/api/account/reading'}]});
    await evaluate("document.querySelector('#detailRead').click()");
    await until("document.querySelector('#accountNotice')?.textContent.includes('账号操作暂未完成')");
    assert.equal(await evaluate("document.querySelector('#detailRead').getAttribute('aria-pressed')"),'true');
    assert.equal(await evaluate("document.querySelector('#detailReadStatus').textContent"),'已标记为已读');
    await call('Fetch.disable');
    // Expiring a browser session must not turn a failed write into a guest write.
    await call('Network.deleteCookies',{name:'pc_session',url:base});
    await evaluate("document.querySelector('#detailRead').click()");
    await until("document.querySelector('#accountNotice')?.textContent.includes('登录已过期')");
    assert.equal(await evaluate("document.querySelector('#detailRead').getAttribute('aria-pressed')"),'true');
    assert.ok(await evaluate("document.querySelector('#accountSlot').textContent.includes('需登录')"));
    const resumePaper = await evaluate('location.hash');
    await evaluate("document.querySelector('#accountSlot a').click()");
    await until("document.querySelector('#accountBody a[href^=\"/auth/github/login\"]')");
    await call('Fetch.enable',{patterns:[{urlPattern:'https://github.com/login/oauth/authorize*'}]});
    await evaluate("document.querySelector('#accountBody a[href^=\"/auth/github/login\"]').click()");
    await until("location.hash===" + JSON.stringify(resumePaper) + " && document.querySelector('#detailRead')?.getAttribute('aria-pressed')==='true'");
    await call('Fetch.disable');
    await evaluate("import('/assets/analytics.js').then(m=>m.flush())");
    await call("Page.navigate", { url: base + "/#account" });
    await until("document.querySelector('#analyticsPreference')");
    assert.ok(await evaluate("document.querySelector('.account-library-links a[href=\"#library?mode=read\"]')!==null"));
    await evaluate("document.querySelector('.account-privacy').open=true");
    await evaluate("document.querySelector('#analyticsPreference').click()");
    assert.equal(
      await evaluate("localStorage.getItem('pc-analytics-off')"),
      "1",
    );
    await evaluate("document.querySelector('#accountLogout').click()");
    await until("location.hash==='#welcome' && document.querySelector('.welcome-nav-enter')?.textContent.includes('登录')");
    assert.equal(
      await evaluate("fetch('/api/account').then(r=>r.json()).then(d=>d.user)"),
      null,
    );
    assert.ok(await evaluate("JSON.parse(localStorage.getItem('pc-saved')).includes('old')"));
    await evaluate("location.hash='#library'");
    await until("document.querySelector('#libraryGrid .paper[data-id=old]')");
    assert.ok(await evaluate("document.querySelector('#libraryScope').textContent.includes('本机文献库')"));
    const beforeCancelled = providerRequests;
    await evaluate("location.hash='#welcome?auth=cancelled&return_to=%23docs'");
    await until("!document.querySelector('#welcomeAuthFeedback')?.hidden && document.querySelector('#welcomeAuthFeedback').textContent.includes('取消 GitHub 授权')");
    assert.equal(await evaluate("new URL(document.querySelector('#welcomeAuthFeedback a').href).searchParams.get('return_to')"),'#docs');
    assert.equal(providerRequests,beforeCancelled);
    await evaluate("document.querySelector('#welcomeAuthFeedback button').click()");
    await until("location.hash==='#welcome' && document.querySelector('#welcomeAuthFeedback').hidden");
    await call('Network.setCookie',{name:'pc_session',value:process.env.PAPER_COMPASS_TEST_OTHER_LOGIN,url:base,path:'/'});
    await call('Page.navigate',{url:base+'/?other-account=1#account'});
    await until("document.querySelector('#accountSlot')?.textContent.includes('another-researcher') && document.querySelector('#accountSyncStatus')?.textContent.includes('已同步到账号')");
    assert.equal(await evaluate("document.querySelectorAll('.account-avatar img').length"),0);
    assert.deepEqual(await evaluate("fetch('/api/account/favorites').then(r=>r.json()).then(d=>d.ids)"),['fixture-0']);
    assert.equal(await evaluate("document.querySelector('.account-library-links strong').textContent"),'1');
    await evaluate("location.hash='#library'");
    await until("document.querySelectorAll('#libraryGrid .paper').length===1");
    assert.equal(await evaluate("document.querySelector('#libraryGrid .paper').dataset.id"),'fixture-0');
    await evaluate("location.hash='#login'");
    await until("location.hash==='#library' && document.querySelector('#libraryGrid .paper')?.dataset.id==='fixture-0'");
    assert.equal(await evaluate("document.querySelector('#login')"),null);
  }
  assert.deepEqual(errors, []);
  console.log(
    "Browser passed: routing, details, back, blank click, save, old collections, mobile, docs.",
  );
  }
} finally {
  socket?.close();
  chrome.kill();
  await new Promise((resolve) => chrome.once("close", resolve));
  await rm(profile, { recursive: true, force: true });
}
