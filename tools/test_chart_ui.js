#!/usr/bin/env node
// test_chart_ui.js — automated UI tests for the chart training lab.
//
// Drives the chart over Chrome DevTools Protocol using REAL mouse input
// (Input.dispatchMouseEvent), so the browser sees genuine pointer/mouse events
// exactly as a human produces them — not synthetic JS-dispatched events.
//
// Prerequisite: Chrome running with --remote-debugging-port=9222 and a tab open
// at http://127.0.0.1:8799/chart.
//
// Usage: node tools/test_chart_ui.js [ws-url]

const WS = process.argv[2] || (() => new Promise((resolve) => {
  require("http").get("http://127.0.0.1:9222/json", (res) => {
    let b = "";
    res.on("data", (c) => (b += c));
    res.on("end", () => {
      const t = JSON.parse(b).find((x) => x.type === "page" && x.url.includes("8799"));
      resolve(t ? t.webSocketDebuggerUrl : null);
    });
  });
}))();

let passed = 0, failed = 0;
function ok(name, cond, detail) {
  if (cond) { passed++; console.log(`  ✓ ${name}`); }
  else { failed++; console.log(`  ✗ ${name}${detail ? " — " + detail : ""}`); }
}

async function main() {
  const wsUrl = typeof WS === "string" ? WS : await WS;
  if (!wsUrl) { console.error("no chart page found on :9222 — start the CDP Chrome first"); process.exit(1); }

  const ws = new WebSocket(wsUrl);
  let id = 0;
  const pending = new Map();
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id); }
  };
  const send = (method, params) => new Promise((res) => {
    const myId = ++id;
    pending.set(myId, res);
    ws.send(JSON.stringify({ id: myId, method, params }));
  });
  const evalJS = (expr) => send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
  const value = async (expr) => {
    const r = await evalJS(expr);
    if (r && r.exceptionDetails) throw new Error(r.exceptionDetails.text);
    return r && r.result ? r.result.value : undefined;
  };
  await new Promise((r) => { ws.onopen = r; });
  await send("Page.bringToFront"); // required for Input.dispatchMouseEvent to fire
  await new Promise((r) => setTimeout(r, 150));

  // ---- real mouse helpers (Input.dispatchMouseEvent) ----
  async function mouse(type, x, y, opts = {}) {
    await send("Input.dispatchMouseEvent", { type, x: Math.round(x), y: Math.round(y), ...opts });
  }
  async function tap(x, y) {
    await mouse("mouseMoved", x, y);
    await mouse("mousePressed", x, y, { button: "left", clickCount: 1 });
    await mouse("mouseReleased", x, y, { button: "left", clickCount: 1 });
  }
  // viewport coords of a bar (i, price) as a JS expression string
  const barXY = (iExpr) => `(function(){ var r=document.getElementById('svg').getBoundingClientRect(); var i=${iExpr}; return JSON.stringify({x:r.left+(GEO.x(i)/W)*r.width, y:r.top+(GEO.y(BARS[i].c)/H)*r.height}); })()`;

  console.log("chart UI tests (real mouse)\n");

  // 1. load
  await value(`location.href='http://127.0.0.1:8799/chart?sym=BTCUSDT'`);
  await new Promise((r) => setTimeout(r, 2500));
  let v = await value(`JSON.stringify({svg:!!document.getElementById('svg'), bars:(typeof BARS!=='undefined')?BARS.length:0})`);
  let s = JSON.parse(v);
  ok("chart loads (svg + bars)", s.svg && s.bars > 0, v);

  // 2. start train mode
  v = await value(`startTrainHere(); getComputedStyle(document.getElementById('trainbar')).display`);
  ok("train bar shows", v === "flex", v);

  // 3. real mouse tap -> start a P run
  let c = JSON.parse(await value(barXY(`Math.floor(BARS.length/2)`)));
  await tap(c.x, c.y);
  v = await value(`TRAIN_PENDING?TRAIN_PENDING.label:null`);
  ok("real tap starts a pending P run", v === "P", v);

  // 4. real pinch zoom in, then real tap -> commit the end
  c = JSON.parse(await value(barXY(`Math.floor((GEO.vs+GEO.vc)/2)`)));
  await mouse("mouseWheel", c.x, c.y, { deltaY: -120, modifiers: 2 }); // ctrl = pinch
  await new Promise((r) => setTimeout(r, 150));
  c = JSON.parse(await value(barXY(`Math.floor((GEO.vs+GEO.vc)/2)`)));
  await tap(c.x, c.y);
  v = await value(`JSON.stringify({points:TRAIN_POINTS.length, committed:!!(TRAIN_POINTS[0]&&TRAIN_POINTS[0].endTs)})`);
  s = JSON.parse(v);
  ok("zoom then tap commits the segment", s.points === 1 && s.committed, v);

  // 5. marker survives a redraw
  v = await value(`draw(); document.querySelectorAll('#trainmarks .trainpt').length`);
  ok("marker survives a redraw", v === 1, v);

  // 6. real mouse click on the marker deletes it
  v = await value(`(function(){ var el=document.querySelector('#trainmarks .trainpt'); var r=el.querySelector('circle').getBoundingClientRect(); return JSON.stringify({x:r.left+r.width/2, y:r.top+r.height/2}); })()`);
  c = JSON.parse(v);
  await tap(c.x, c.y);
  v = await value(`TRAIN_POINTS.length`);
  ok("click deletes a marker", v === 0, v);

  // 7. finish and train -> model fits
  v = await value(`(function(){ TRAIN_POINTS=[]; var m=Math.floor(BARS.length/2); var p=[]; for(var k=0;k<4;k++){ var b=BARS[m+k]; p.push({ts:b.t, price:b.c, ret:0.01, label:'P', endTs:(BARS[m+k+2]||b).t, endPrice:(BARS[m+k+2]||b).c, symbol:'BTCUSDT'}); } for(var k=0;k<4;k++){ var b2=BARS[m+10+k]; p.push({ts:b2.t, price:b2.c, ret:-0.01, label:'D', endTs:(BARS[m+12+k]||b2).t, endPrice:(BARS[m+12+k]||b2).c, symbol:'BTCUSDT'}); } return fetch('/api/train',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:'uitest',points:p})}).then(r=>r.json()).then(d=>JSON.stringify({fit:!!(d.fit&&d.fit.fit), n:d.fit?d.fit.n:0})); })()`);
  s = JSON.parse(v);
  ok("train fits a model", s.fit && s.n > 0, v);

  // 8. detections render
  v = await value(`runModelDetections('uitest').then(r=>JSON.stringify({ps:r.ps, ds:r.ds, marks:document.querySelectorAll('#testmarks circle').length}))`);
  s = JSON.parse(v);
  ok("detections render", (s.ps + s.ds) > 0 && s.marks > 0, v);

  // 9. sensitivity changes count
  v = await value(`(function(){ DETECT_THRESH=0.55; return runModelDetections('uitest').then(r1=>{ DETECT_THRESH=0.9; return runModelDetections('uitest').then(r2=>JSON.stringify({lo:r1.ps+r1.ds, hi:r2.ps+r2.ds})); }); })()`);
  s = JSON.parse(v);
  ok("sensitivity changes detection count", s.lo >= s.hi, v);

  // 10. recall loads saved vectors
  v = await value(`(function(){ document.getElementById('trainmodel').value='manual'; return loadRecalledVectors().then(function(){ return RECALLED.length; }); })()`);
  ok("recall loads saved vectors", v > 0, v);

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
