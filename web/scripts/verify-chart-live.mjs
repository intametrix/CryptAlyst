/**
 * Verifies the chart actually renders against the LIVE app (not a fixture):
 * opens the dashboard, clicks a symbol, waits for the klines path to settle,
 * then reports whether the "loading" placeholder is gone and whether the
 * lightweight-charts canvases have painted (non-uniform) pixels. Screenshots
 * at each stage so a human (or KIMI) can look at the result directly.
 *
 *   node scripts/verify-chart-live.mjs
 *
 * Env: CHROME (binary path), APP_URL (defaults to the Go server on :8787).
 */
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const chromePath =
  process.env.CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const appUrl = process.env.APP_URL ?? 'http://127.0.0.1:8787';
const shotDir = process.env.SHOT_DIR ?? join(tmpdir(), 'kimi-chart-verify');
mkdirSync(shotDir, { recursive: true });

const port = 9333 + Math.floor(Math.random() * 200);
const profile = join(tmpdir(), `chart-verify-profile-${Date.now()}`);

const chrome = spawn(
  chromePath,
  [
    '--headless=new',
    '--disable-gpu',
    '--no-first-run',
    '--no-default-browser-check',
    '--no-sandbox',
    '--disable-dev-shm-usage',
    '--window-size=1600,1000',
    `--user-data-dir=${profile}`,
    `--remote-debugging-port=${port}`,
    'about:blank',
  ],
  { stdio: ['ignore', 'ignore', 'ignore'] },
);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function debuggerUrl() {
  for (let i = 0; i < 100; i += 1) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (res.ok) return (await res.json()).webSocketDebuggerUrl;
    } catch {
      // Chrome is still starting.
    }
    await sleep(120);
  }
  throw new Error('chrome did not expose a debugger endpoint');
}

let socket;
let nextId = 1;
const pending = new Map();
const consoleLines = [];
const exceptions = [];
const netFailures = [];

function send(method, params = {}, sessionId) {
  const id = nextId;
  nextId += 1;
  const payload = { id, method, params };
  if (sessionId !== undefined) payload.sessionId = sessionId;
  socket.send(JSON.stringify(payload));
  return new Promise((resolvePromise, reject) => {
    pending.set(id, { resolvePromise, reject });
    setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id);
        reject(new Error(`${method} timed out`));
      }
    }, 30000);
  });
}

async function evaluate(sessionId, expression) {
  const result = await send(
    'Runtime.evaluate',
    { expression, awaitPromise: true, returnByValue: true },
    sessionId,
  );
  if (result.exceptionDetails !== undefined) {
    throw new Error(`evaluate failed: ${JSON.stringify(result.exceptionDetails).slice(0, 300)}`);
  }
  return result.result.value;
}

async function screenshot(sessionId, name) {
  const { data } = await send('Page.captureScreenshot', { format: 'png' }, sessionId);
  const path = join(shotDir, name);
  writeFileSync(path, Buffer.from(data, 'base64'));
  return path;
}

const DIAGNOSE = `(() => {
  const loading = document.querySelector('.chart-loading');
  const overlay = document.querySelector('.chart-overlay');
  const header = overlay?.textContent?.trim()?.split(/\\s+/).slice(0, 5).join(' ') ?? null;
  const canvases = [...document.querySelectorAll('canvas')].map((c) => ({
    w: c.width, h: c.height, cssW: Math.round(c.getBoundingClientRect().width),
    cssH: Math.round(c.getBoundingClientRect().height),
  }));
  let painted = null;
  const bottom = document.querySelector('.cp-bottom');
  const divider = document.querySelector('.cp-divider');
  const panelInfo = {
    bottomPresent: !!bottom,
    bottomVisible: bottom ? !!bottom.offsetParent : false,
    bottomHeight: bottom ? Math.round(bottom.getBoundingClientRect().height) : 0,
    dividerPresent: !!divider,
    dividerText: divider ? divider.textContent.trim().slice(0, 30) : null,
  };
  const big = [...document.querySelectorAll('canvas')].filter((c) => c.width > 200 && c.height > 100)
    .sort((a, b) => b.width * b.height - a.width * a.height)[0];
  if (big) {
    const ctx = big.getContext('2d');
    if (ctx) {
      // Sample several strips: a single mid-strip can cross a quiet region of
      // a legitimately drawn chart, so sum across 20%..80% height.
      let nonBg = 0;
      let total = 0;
      for (const frac of [0.2, 0.35, 0.5, 0.65, 0.8]) {
        const strip = ctx.getImageData(0, Math.floor(big.height * frac), Math.min(400, big.width), 1).data;
        const first = [strip[0], strip[1], strip[2], strip[3]];
        total += Math.floor(strip.length / 4);
        for (let i = 0; i < strip.length; i += 4) {
          if (Math.abs(strip[i] - first[0]) + Math.abs(strip[i + 1] - first[1]) + Math.abs(strip[i + 2] - first[2]) > 24) nonBg += 1;
        }
      }
      painted = { nonBgOf: total, nonBg };
    } else {
      painted = 'no-2d-context';
    }
  }
  return {
    url: location.href,
    header,
    loadingText: loading ? loading.textContent.trim().slice(0, 60) : null,
    loadingVisible: loading ? !!loading.offsetParent : false,
    overlayPresent: !!overlay,
    canvasCount: canvases.length,
    panel: panelInfo,
    painted,
  };
})()`;

try {
  const wsUrl = await debuggerUrl();
  socket = new WebSocket(wsUrl);
  await new Promise((openResolve, reject) => {
    socket.addEventListener('open', () => openResolve());
    socket.addEventListener('error', () => reject(new Error('ws error')));
  });

  let session = null;
  socket.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    if (message.id !== undefined) {
      const waiter = pending.get(message.id);
      if (waiter !== undefined) {
        pending.delete(message.id);
        if (message.error !== undefined) waiter.reject(new Error(JSON.stringify(message.error)));
        else waiter.resolvePromise(message.result);
      }
      return;
    }
    if (message.method === 'Runtime.consoleAPICalled') {
      const text = (message.params.args ?? [])
        .map((a) => (a.value !== undefined ? String(a.value) : a.description ?? ''))
        .join(' ');
      consoleLines.push(text);
    }
    if (message.method === 'Runtime.exceptionThrown') {
      const d = message.params.exceptionDetails;
      exceptions.push(d.exception?.description ?? d.text ?? 'unknown');
    }
    if (message.method === 'Network.loadingFailed') {
      netFailures.push(`${message.params.type}: ${message.params.errorText}`);
    }
  });

  const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
  session = (await send('Target.attachToTarget', { targetId, flatten: true })).sessionId;

  await send('Runtime.enable', {}, session);
  await send('Page.enable', {}, session);
  await send('Network.enable', {}, session);

  console.log(`navigating: ${appUrl}`);
  await send('Page.navigate', { url: appUrl }, session);
  await sleep(4000);

  // Pin the chart overlay to a size that fits the headless window: the default
  // is 75% of the viewport, which in headless Chrome can exceed the visible
  // window and push the bottom panel off-screen. The panel itself is what the
  // screenshots must show.
  await evaluate(
    session,
    `(() => {
      try {
        window.localStorage.setItem('pumpdump.layout.v1', JSON.stringify({
          boxes: { 'chart.size': { w: 1280, h: 780 }, 'chart.pos': { w: 60, h: 40 } },
          chips: {},
        }));
        return 'pinned';
      } catch { return 'no-storage'; }
    })()`,
  ).then((r) => console.log(`overlay geometry: ${r}`));

  // Click the first non-header flow row's SYMBOL BUTTON to open the chart.
  const clicked = await evaluate(
    session,
    `(() => {
      const row = document.querySelector('.flow-row:not(.flow-head)');
      const btn = row?.querySelector('.sym-btn');
      if (!btn) return 'NO_BUTTON';
      const sym = btn.textContent.trim();
      btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      return sym;
    })()`,
  );
  console.log(`clicked symbol button: ${clicked}`);
  await sleep(5000);

  const first = await evaluate(session, DIAGNOSE);
  console.log('DIAGNOSIS after first symbol:');
  console.log(JSON.stringify(first, null, 2));
  await evaluate(
    session,
    `(() => {
      for (const el of document.querySelectorAll('.chart-overlay')) {
        if ((el.textContent ?? '').toUpperCase().includes('TRADE')) el.style.visibility = 'hidden';
      }
      return 'hidden';
    })()`,
  );
  await sleep(400);
  const shot1 = await screenshot(session, '01-first-symbol.png');
  console.log(`screenshot: ${shot1}`);

  // The symbol-change race: two rapid button clicks, then settle on the last.
  const race = await evaluate(
    session,
    `(() => {
      const rows = [...document.querySelectorAll('.flow-row:not(.flow-head)')];
      const btns = rows.map((r) => r.querySelector('.sym-btn')).filter(Boolean);
      if (btns.length < 3) return 'NOT_ENOUGH_ROWS';
      const a = btns[1].textContent.trim();
      const b = btns[2].textContent.trim();
      btns[1].dispatchEvent(new MouseEvent('click', { bubbles: true }));
      setTimeout(() => btns[2].dispatchEvent(new MouseEvent('click', { bubbles: true })), 120);
      return a + ' then ' + b;
    })()`,
  );
  console.log(`race: ${race}`);
  await sleep(5000);

  const second = await evaluate(session, DIAGNOSE);
  const lastSym = race.includes(' then ') ? race.split(' then ')[1] : null;
  const headerOk = lastSym !== null && (second.header ?? '').toUpperCase().includes(lastSym.toUpperCase());
  console.log('DIAGNOSIS after symbol race:');
  console.log(JSON.stringify(second, null, 2));
  console.log(`race target was ${lastSym}; chart header matches: ${headerOk}`);

  // The sym button also opens the drill-down on top of the chart; hide it for
  // the screenshot so the chart underneath is what we capture.
  await evaluate(
    session,
    `(() => {
      for (const el of document.querySelectorAll('.chart-overlay')) {
        if ((el.textContent ?? '').toUpperCase().includes('TRADE')) el.style.visibility = 'hidden';
      }
      return 'hidden';
    })()`,
  );
  await sleep(400);
  console.log('DIAGNOSIS after symbol race:');
  console.log(JSON.stringify(second, null, 2));
  const shot2 = await screenshot(session, '02-after-race.png');
  console.log(`screenshot: ${shot2}`);

  console.log('--- console (last 15) ---');
  for (const line of consoleLines.slice(-15)) console.log(`  ${line.slice(0, 200)}`);
  console.log('--- exceptions ---');
  for (const e of exceptions.slice(0, 10)) console.log(`  ${String(e).slice(0, 300)}`);
  console.log('--- network failures ---');
  for (const f of netFailures.slice(0, 10)) console.log(`  ${f}`);

  const verdict =
    second.loadingVisible === false &&
    headerOk &&
    second.canvasCount > 0 &&
    typeof second.painted === 'object' &&
    second.painted.nonBg > 0;
  console.log(verdict
    ? `VERDICT: chart renders and the symbol race settles correctly (target ${lastSym}, painted sanity ${second.painted.nonBg}px)`
    : 'VERDICT: still broken — see diagnostics above');
  process.exitCode = verdict ? 0 : 1;
} catch (error) {
  console.error(`VERIFY FAILED: ${error.message ?? error}`);
  process.exitCode = 2;
} finally {
  try {
    chrome.kill('SIGKILL');
  } catch {
    // already gone
  }
}
