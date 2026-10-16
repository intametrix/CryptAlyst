/**
 * Drives the headless-browser reactivity harness over the DevTools protocol.
 *
 * `chrome --dump-dom` snapshots the DOM when the page goes idle, which is
 * unusable for a test that deliberately waits on timers. Speaking CDP instead
 * lets this script collect the harness's console output as it happens, wait for
 * a result marker, and then read the summary element.
 *
 *   node scripts/rt-drive.mjs            (expects the harness already built)
 *
 * Env: CHROME (path to the binary), RT_URL (page to open).
 */
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const chromePath =
  process.env.CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const pageUrl = process.env.RT_URL ?? `file://${join(root, 'node_modules', '.tmp', 'rt', 'index.html')}`;
const port = 9333 + Math.floor(Math.random() * 200);
const profile = mkdtempSync(join(tmpdir(), 'rt-profile-'));

const chrome = spawn(
  chromePath,
  [
    '--headless=new',
    '--disable-gpu',
    '--no-first-run',
    '--no-default-browser-check',
    '--no-sandbox',
    '--disable-dev-shm-usage',
    `--window-size=${process.env.RT_WINDOW ?? '1440,900'}`,
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
const logs = [];

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

try {
  const wsUrl = await debuggerUrl();
  socket = new WebSocket(wsUrl);
  await new Promise((openResolve, reject) => {
    socket.addEventListener('open', () => openResolve());
    socket.addEventListener('error', (event) => reject(new Error(`ws error: ${event.message ?? 'unknown'}`)));
  });

  let targetSession = null;
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
      logs.push(text);
      if (text.startsWith('RT ')) console.log(text);
    }
    if (message.method === 'Runtime.exceptionThrown') {
      const d = message.params.exceptionDetails;
      console.log(`RT FAIL uncaught :: ${d.exception?.description ?? d.text}`);
      logs.push('RT FAIL uncaught');
    }
  });

  // Attach to a fresh page target.
  const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
  const attached = await send('Target.attachToTarget', { targetId, flatten: true });
  targetSession = attached.sessionId;

  await send('Runtime.enable', {}, targetSession);
  await send('Page.enable', {}, targetSession);
  await send('Page.navigate', { url: pageUrl }, targetSession);

  // Wait for the harness to publish a terminal result (or give up).
  let summary = null;
  for (let i = 0; i < 240; i += 1) {
    await sleep(250);
    const probe = await send(
      'Runtime.evaluate',
      {
        expression: `(() => { const el = document.getElementById('rt-results'); return el ? el.textContent : null; })()`,
        returnByValue: true,
      },
      targetSession,
    );
    const value = probe?.result?.value ?? null;
    if (typeof value === 'string' && (value.startsWith('DONE') || value.startsWith('PARTIAL'))) {
      summary = value;
      if (value.startsWith('DONE')) break;
    }
  }

  if (summary === null) {
    // Diagnose rather than just failing: what did the page actually do?
    const state = await send(
      'Runtime.evaluate',
      {
        expression: `JSON.stringify({
          root: document.getElementById('root')?.childElementCount ?? -1,
          results: document.getElementById('rt-results')?.textContent?.slice(0, 400) ?? null,
          overlays: document.querySelectorAll('.chart-overlay').length,
          canvases: document.querySelectorAll('canvas').length,
          scripts: [...document.scripts].map((s) => s.src),
          body: document.body.innerHTML.length,
        })`,
        returnByValue: true,
      },
      targetSession,
    );
    console.log(`RT FAIL no-results :: the harness never published a result`);
    console.log(`driver-state :: ${state?.result?.value ?? 'unavailable'}`);
    process.exitCode = 1;
  } else {
    console.log('--- harness summary ---');
    console.log(summary);
    const header = summary.split('\n')[0];
    const [passed, total] = header.replace(/^(DONE|PARTIAL) /, '').split('/').map(Number);
    process.exitCode = passed === total ? 0 : 1;
  }
} catch (error) {
  console.log(`RT FAIL driver :: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
} finally {
  try {
    socket?.close();
  } catch {
    // ignore
  }
  chrome.kill('SIGKILL');
  rmSync(profile, { recursive: true, force: true });
}
