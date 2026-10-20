/* The Envelope Explorer — a read-only lens on the lake. */
const $ = (id) => document.getElementById(id);
const state = { plane: "", topic: "", key: "", producer: "", cid: "", q: "", docs: [], sel: null,
  live: true, role: "owner", from: 0, to: 0, buckets: [], playing: null, span: 0,
  view: "list", journeyKey: "", whyKey: "", whyEngine: "", whyHours: 6, whyOutcome: "any" };

const TOPIC_MEANING = {
  "ticks": "batched price snapshot across streamed symbols",
  "market.price": "mid price per symbol",
  "market.depth": "five-level order book with imbalance",
  "market.candle": "closed kline (1m or 15m)",
  "market.mini24h": "24h change and quote volume",
  "intel.signal": "20-field signal set for one symbol",
  "intel.flow": "money-flow confidence, -100 to +100",
  "intel.ta": "technical analysis bundle",
  "intel.forecast": "seven-agent blended forecast",
  "intel.movers": "gainers, losers and most traded",
  "model.decision": "a model's trade decision, allowed or refused",
  "model.fill": "an executed paper fill",
  "model.pnl": "a book's realised and unrealised result",
  "model.rank": "cross-sectional ranking",
  "risk.state": "portfolio exposure and limits",
  "exec.quote": "maker quote construction",
  "alert.pumpdump": "pump or dump alert from the detector",
  "system.health": "service health probe",
  "review.verdict": "portfolio review outcome",
};

const PLANE_COLOUR = {
  market: "var(--market)", intel: "var(--intel)", model: "var(--model)",
  control: "var(--control)", system: "var(--system)", unknown: "var(--system)",
};

function fmtBytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB"]; let i = 0; let v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return v.toFixed(v < 10 ? 1 : 0) + " " + u[i];
}
function fmtTime(ms) {
  if (!ms) return "—";
  const d = new Date(ms);
  return d.toLocaleTimeString([], { hour12: false }) + "." + String(d.getMilliseconds()).padStart(3, "0");
}
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* JSON with syntax colouring, built from text so it can never execute markup. */
function colourJSON(value) {
  const text = JSON.stringify(value, null, 2);
  if (text == null) return "";
  // tokenise the raw text, then escape each piece — escaping first destroys the
  // quotes the pattern needs and nothing gets highlighted
  const re = /("(?:\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*"\s*:?)|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?)/g;
  let out = "", last = 0, m;
  while ((m = re.exec(text)) !== null) {
    out += esc(text.slice(last, m.index));
    let cls = "j-num";
    if (m[1]) cls = /:\s*$/.test(m[1]) ? "j-key" : "j-str";
    else if (m[2]) cls = m[2] === "null" ? "j-null" : "j-bool";
    out += `<span class="${cls}">${esc(m[0])}</span>`;
    last = m.index + m[0].length;
  }
  return out + esc(text.slice(last));
}

async function loadStats() {
  try {
    const s = await (await fetch("/api/stats", { cache: "no-store" })).json();
    $("stats").innerHTML = [
      ["envelopes", s.envelopes.toLocaleString()],
      ["bytes", fmtBytes(s.bytes)],
      ["planes", Object.keys(s.by_plane || {}).length],
      ["topics", Object.keys(s.by_topic || {}).length],
    ].map(([k, v]) => `<div class="stat"><b>${esc(v)}</b><span>${esc(k)}</span></div>`).join("");
    $("foot").textContent = `${s.envelopes.toLocaleString()} envelopes in memory · oldest ${fmtTime(s.oldest_ms)} · newest ${fmtTime(s.newest_ms)}`;
  } catch (e) { /* the list still works without stats */ }
}

async function loadTopics() {
  try {
    const rows = await (await fetch("/api/topics", { cache: "no-store" })).json();
    const sel = $("topic");
    const keep = sel.value;
    sel.innerHTML = '<option value="">all topics</option>' +
      rows.map((r) => `<option value="${esc(r.topic)}">${esc(r.topic)} (${r.count})</option>`).join("");
    sel.value = keep;
    $("topicList").innerHTML = rows.slice(0, 14).map((r) => `
      <div class="topic-row" data-topic="${esc(r.topic)}">
        <span class="dotP" style="background:${PLANE_COLOUR[r.plane] || "#888"}"></span>
        <span>${esc(r.topic)}</span><b>${r.count}</b>
      </div>`).join("");
    document.querySelectorAll(".topic-row").forEach((el) => {
      el.onclick = () => { $("topic").value = el.dataset.topic; state.topic = el.dataset.topic; load(); };
    });
  } catch (e) {}
}

function query() {
  const p = new URLSearchParams();
  if (state.plane) p.set("plane", state.plane);
  if (state.topic) p.set("topic", state.topic);
  if (state.key) p.set("key", state.key);
  if (state.producer) p.set("producer", state.producer);
  if (state.cid) p.set("cid", state.cid);
  if (state.q) p.set("q", state.q);
  p.set("limit", "200");
  if (state.from) p.set("from", String(state.from));
  if (state.to) p.set("to", String(state.to));
  return p.toString();
}

async function load() {
  try {
    const data = await (await fetch("/api/envelopes?" + query(), { cache: "no-store" })).json();
    state.docs = data.envelopes || [];
    renderRows();
    $("resultCount").textContent = `${data.count} envelope${data.count === 1 ? "" : "s"}`;
  } catch (e) {
    $("resultCount").textContent = "unavailable";
  }
}

function renderRows() {
  const box = $("rows");
  if (!state.docs.length) {
    box.innerHTML = '<div class="empty">no envelopes match these filters</div>';
    return;
  }
  box.innerHTML = state.docs.map((d) => `
    <div class="row ${state.sel === d.id ? "sel" : ""}" data-id="${d.id}">
      <span class="bar" style="background:${PLANE_COLOUR[d.plane] || "#888"}"></span>
      <div>
        <div class="t">${esc(d.topic)}${d.key ? " · " + esc(d.key) : ""}</div>
        <div class="s">
          <span class="pill ${esc(d.plane)}">${esc(d.plane)}</span>
          ${d.producer ? `<span>${esc(d.producer)}</span>` : ""}
          ${d.cid ? `<span>${esc(d.cid)}</span>` : ""}
          ${d.generation ? `<span>gen ${d.generation}</span>` : ""}
        </div>
      </div>
      <div class="r">${fmtTime(d.ts)}<br/>${fmtBytes(d.size)}</div>
    </div>`).join("");
  box.querySelectorAll(".row").forEach((el) => {
    el.onclick = () => select(Number(el.dataset.id));
  });
}

function findDoc(id) {
  if (id === null || id === undefined) return null;
  const key = String(id);
  // the live feed replaces the list every second, so the selected envelope is
  // remembered rather than expected to still be in the newest page
  if (!state.cache) state.cache = new Map();
  return state.docs.find((x) => String(x.id) === key) || state.cache.get(key) || null;
}

function metaField(meta, name) {
  if (!meta) return "";
  const v = meta[name];
  if (v === undefined || v === null || v === "") return "";
  return String(v);
}

async function select(id, quiet) {
  if (!quiet && state.sel !== id) pushState();
  state.sel = id;
  const picked = findDoc(id);
  if (picked) {
    if (!state.cache) state.cache = new Map();
    state.cache.set(String(id), picked);
    if (picked.key) state.journeyKey = picked.key;
  }
  // open the context surface first, so a failed fetch can never leave a blank pane
  if (state.view === "list") applyView("context");
  renderRows();
  try {
    const resp = await fetch("/api/envelope/" + id + "?role=" + encodeURIComponent(state.role), { cache: "no-store" });
    if (!resp.ok) throw new Error("status " + resp.status);
    renderDetail(await resp.json());
  } catch (e) {
    $("detail").innerHTML = '<div class="empty">could not load this envelope</div>';
  }
  try { updateTabs(); } catch (e) { /* chrome, never fatal */ }
  try { updateBackBtn(); } catch (e) {}
  const picked2 = findDoc(id);
  if (picked2) {
    $("crumbHere").textContent = `${picked2.key || picked2.topic} · ${picked2.topic}`;
  }
  if (state.view === "context") renderContext();
}

/* renderDetail draws one envelope: the three members, the payload and the chain. */
async function renderDetail(d) {
  try {
    let payload = d.payload;
    let meta = d.meta || null;
    if (typeof payload === "string") { try { payload = JSON.parse(payload); } catch (e) {} }
    if (typeof meta === "string") { try { meta = JSON.parse(meta); } catch (e) {} }
    const m = meta || {};
    const refs = (d.refs && d.refs.length) ? d.refs : (m.refs || []);
    const extra = (m && m.extra) || {};
    const notice = extra.redacted_for
      ? `<div class="redacted">redacted for <b>${esc(extra.redacted_for)}</b> — hidden: ${esc(String(extra.redacted_fields || ""))}</div>`
      : "";
    const legacy = !d.has_envelope;
    const rows = (legacy ? [
      ["plane", d.plane],
      ["producer", d.producer || "—"],
      ["recorded_at", fmtTime(d.ts)],
      ["bytes", fmtBytes(d.size)],
      ["provenance", "recorded before envelopes existed — payload only"],
    ] : [
      ["version", metaField(m, "version") || d.version],
      ["plane", metaField(m, "plane") || d.plane],
      ["producer", metaField(m, "producer") || d.producer || "—"],
      ["generation", metaField(m, "generation") || d.generation || "—"],
      ["correlation id", metaField(m, "cid") || d.cid || "—"],
      ["computed_at", metaField(m, "computed_at") || "—"],
      ["recorded_at", fmtTime(d.ts)],
      ["bytes", fmtBytes(d.size)],
    ]).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");

    // a Python producer publishes its own wrapper: {topic,key,producer,data,...}
    const nested = payload && typeof payload === "object" && payload.data !== undefined &&
      (payload.topic || payload.producer || payload.version !== undefined);
    const inner = nested ? payload.data : payload;
    const outer = nested ? payload : null;

    let hops = d.hops || [];
    if (typeof hops === "string") { try { hops = JSON.parse(hops); } catch (e) { hops = []; } }
    if (role !== "owner" && role) {
      hops = hops.concat([{ kind: "transformer", name: "redact", outcome: "modify", detail: "view-time, for " + role }]);
    }
    const pipeline = hops.length ? `
      <div class="section"><h3>Pipeline — interceptors, operator, transformers</h3>
        <div class="pipeline">
          ${hops.map((hp) => `<span class="hop ${hp.kind} ${hp.outcome}" title="${esc(hp.detail || "")}">
             <b>${esc(hp.kind)}</b>${esc(hp.name)}
             <i>${esc(hp.outcome)}</i></span>`).join('<span class="hop-arrow">→</span>')}
        </div>
      </div>` : `
      <div class="section"><h3>Pipeline — interceptors, operator, transformers</h3>
        <div class="sub">not recorded on this envelope: it was stored before pipeline tracing existed.</div>
      </div>`;

    const memberStrip = `
      <div class="members">
        <span class="member on"><b>data</b> ${nested ? "inner payload" : "payload"}</span>
        <span class="member on"><b>meta</b> ${d.has_envelope ? "recorded" : "inferred"}</span>
        <span class="member ${refs && refs.length ? "on" : "off"}"><b>refs</b> ${refs && refs.length ? refs.length : "none"}</span>
      </div>`;

    $("detail").innerHTML = notice + `
      <div class="dhead">
        <div>
          <h2>${esc(d.topic)}${d.key ? " · " + esc(d.key) : ""}</h2>
          <div class="sub"><span class="pill ${esc(d.plane)}">${esc(d.plane)}</span>
            &nbsp;${esc(d.producer || "unknown producer")} &nbsp;·&nbsp; ${fmtTime(d.ts)}</div>
          <div class="sub" style="margin-top:5px;color:var(--dim)">${esc(TOPIC_MEANING[d.topic] || "")}</div>
        </div>
      </div>
      ${memberStrip}
      ${pipeline}
      <div class="section"><h3>Meta — provenance and freshness</h3><dl class="kv">${rows}</dl></div>
      <div class="section"><h3>Data — the payload</h3>
        ${outer ? `<div class="sub">published as topic <b>${esc(outer.topic || d.topic)}</b>
          by <b>${esc(outer.producer || d.producer)}</b>${outer.seq ? " · seq " + esc(outer.seq) : ""}</div>` : ""}
        <pre>${colourJSON(nested ? inner : payload)}</pre></div>
      <div class="section"><h3>References — what it was computed from</h3>
        ${refs && refs.length
          ? `<div class="sub">${(refs || []).some((r) => r && r.derived)
              ? "derived by the recorder from the window at ingestion — the producer does not declare its inputs yet"
              : "declared by the producer"}</div>
             <pre>${colourJSON(refs)}</pre>`
          : `<div class="sub">none — ${esc(d.producer || "this producer")} does not declare references, and the
             recorder found no input envelopes for this symbol in the preceding five minutes.
             A reference names the topic, key, generation and correlation id of each input.</div>`}
      </div>
      <div class="section"><h3>Chain — everything recorded for this key</h3><div id="chain" class="chain">loading…</div></div>`;

    if (d.key) {
      const t = await (await fetch("/api/trace?key=" + encodeURIComponent(d.key), { cache: "no-store" })).json();
      const steps = (t.chain || []).slice(-14);
      $("chain").innerHTML = steps.map((s, i) => `
        <div class="step" data-id="${s.id}">
          <span class="pill ${esc(s.plane)}">${esc(s.plane)}</span>
          <span>${esc(s.topic)}</span>
          <span style="margin-left:auto;color:var(--faint)">${fmtTime(s.ts)}</span>
        </div>${i < steps.length - 1 ? '<div class="arrow">↓</div>' : ""}`).join("").replace(/<\/div><div class="arrow">/g, '</div><div class="arrow">');
      $("chain").querySelectorAll(".step").forEach((el) => {
        el.onclick = () => select(Number(el.dataset.id));
      });
    } else {
      $("chain").textContent = "no key on this envelope";
    }
    if (!restoring) {
      if (state.view === "journey") renderJourney(d.id);
      if (state.view === "execution") renderExecution(d.id);
    }
  } catch (e) {
    $("detail").innerHTML = '<div class="empty">could not load this envelope</div>';
  }
}

/* live tail */
function connect() {
  const es = new EventSource("/api/stream");
  es.addEventListener("envelope", (ev) => {
    if (!state.live) return;
    const d = JSON.parse(ev.data);
    if (matches(d)) {
      state.docs.unshift(d);
      state.docs = state.docs.slice(0, 200);
      renderRows();
      loadStats();
    }
  });
  es.onopen = () => { $("conn").classList.add("on"); $("connText").textContent = "live"; };
  es.onerror = () => { $("conn").classList.remove("on"); $("connText").textContent = "reconnecting…"; };
}

function matches(d) {
  if (state.plane && d.plane !== state.plane) return false;
  if (state.topic && d.topic !== state.topic) return false;
  if (state.key && !String(d.key).toLowerCase().includes(state.key.toLowerCase())) return false;
  if (state.producer && !String(d.producer).toLowerCase().includes(state.producer.toLowerCase())) return false;
  if (state.cid && !String(d.cid).toLowerCase().includes(state.cid.toLowerCase())) return false;
  if (state.q) {
    const hay = (JSON.stringify(d.payload) + JSON.stringify(d.meta) + d.topic + d.key).toLowerCase();
    if (!hay.includes(state.q.toLowerCase())) return false;
  }
  return true;
}

let dragging = false, dragFrom = 0;

async function loadTimeline() {
  if (state.from && state.to && state.playing === null) return; // hold the view while brushed
  try {
    const d = await (await fetch("/api/timeline?buckets=120", { cache: "no-store" })).json();
    state.buckets = d.buckets || [];
    if (d.from) { state.span = d.to - d.from; }
    drawTimeline(d);
  } catch (e) {}
}

function drawTimeline(d) {
  const track = $("track");
  if (!state.buckets.length) { track.innerHTML = '<div class="tl-empty">no data yet</div>'; return; }
  const max = Math.max(...state.buckets.map((b) => b.total), 1);
  track.innerHTML = state.buckets.map((b, i) => {
    const h = Math.max(2, Math.round((b.total / max) * 100));
    const segs = Object.entries(b.planes || {}).map(([plane, n]) => {
      const share = (n / Math.max(b.total, 1)) * 100;
      return `<span class="tl-seg" style="height:${share}%;background:${PLANE_COLOUR[plane] || "#888"}"></span>`;
    }).join("");
    return `<div class="tl-bar" data-i="${i}" style="height:${h}%" title="${b.total} envelopes · ${fmtTime(b.from)}">${segs}</div>`;
  }).join("");
  $("axis").innerHTML = `<span>${fmtTime(d.from)}</span><span>${fmtTime((d.from + d.to) / 2)}</span><span>${fmtTime(d.to)}</span>`;
  const bars = track.querySelectorAll(".tl-bar");
  // a redraw (the timeline refreshes every few seconds) must not lose the brush
  if (state.from && state.to) {
    bars.forEach((el) => {
      const b = state.buckets[Number(el.dataset.i)];
      const inside = b && b.to > state.from && b.from < state.to;
      el.classList.toggle("sel", !!inside);
      el.classList.toggle("dim", !inside);
    });
  }
  bars.forEach((bar) => {
    bar.onmousedown = (ev) => {
      dragging = true; dragFrom = Number(bar.dataset.i);
      applySelection(dragFrom, dragFrom);
    };
    bar.onmouseenter = () => { if (dragging) applySelection(dragFrom, Number(bar.dataset.i)); };
  });
}

function applySelection(a, b) {
  const [lo, hi] = a <= b ? [a, b] : [b, a];
  const first = state.buckets[lo], last = state.buckets[hi];
  if (!first || !last) return;
  state.from = first.from; state.to = last.to;
  state.live = false;
  $("live").classList.remove("on"); $("live").textContent = "paused";
  $("tlWindow").textContent = `${fmtTime(state.from)} → ${fmtTime(state.to)}`;
  document.querySelectorAll(".tl-bar").forEach((el) => {
    const i = Number(el.dataset.i);
    el.classList.toggle("sel", i >= lo && i <= hi);
    el.classList.toggle("dim", i < lo || i > hi);
  });
  load();
}

document.addEventListener("mouseup", () => { dragging = false; });

$("tlClear").onclick = () => {
  state.from = state.to = 0; state.live = true;
  stopPlay();
  $("live").classList.add("on"); $("live").textContent = "live";
  $("tlWindow").textContent = "live · last 200";
  document.querySelectorAll(".tl-bar").forEach((el) => el.classList.remove("sel", "dim"));
  load();
};

function stopPlay() {
  if (state.playing) { clearInterval(state.playing); state.playing = null; }
  $("play").classList.remove("on"); $("play").textContent = "▶ play";
}

$("play").onclick = () => {
  if (state.playing) { stopPlay(); return; }
  const width = Math.max(5000, Math.round(state.span / 12));
  if (!state.from) {
    const last = state.buckets[state.buckets.length - 1];
    if (!last) return;
    state.from = last.from - width; state.to = last.to;
  }
  $("play").classList.add("on"); $("play").textContent = "❚❚ pause";
  state.playing = setInterval(() => {
    state.from += width; state.to += width;
    $("tlWindow").textContent = `${fmtTime(state.from)} → ${fmtTime(state.to)}`;
    load();
  }, 900);
};

const STAGE_LABEL = ["market data", "advisories", "risk & alerts", "decisions", "other"];
const LANE_LABEL = ["market", "advisories", "risk", "model", "other"];
let exZoom = 1;

/* Health: the services the Explorer depends on, probed through the API. */
async function renderHealth() {
  const d = await (await fetch("/api/health", { cache: "no-store" })).json();
  const rows = d.services || [];
  $("healthTitle").textContent = `service health — ${d.up}/${rows.length} up`;
  $("healthBody").innerHTML = `
    <table><tr><th>service</th><th>port</th><th>status</th><th>latency</th></tr>
    ${rows.map((s) => `<tr><td>${esc(s.name)}</td><td>${s.port || ""}</td>
      <td class="${s.up ? "" : "neg"}">${s.up ? "up" : "down"}</td>
      <td>${s.ms} ms</td></tr>`).join("")}</table>`;
}

/* Books: the compliance officer's entry point. */
async function renderBooks() {
  const d = await (await fetch("/api/books", { cache: "no-store" })).json();
  const rows = d || [];
  $("booksTitle").textContent = `books — ${rows.length} engines · ${rows.reduce((a, b) => a + b.trades, 0)} trades`;
  $("booksBody").innerHTML = `
    <div class="book-card"><span class="hdr">engine</span><span class="hdr num">trades</span>
      <span class="hdr num">net sum</span><span class="hdr num">mean net</span>
      <span class="hdr num">mean held</span><span class="hdr num">refusals</span></div>
    ${rows.map((b) => `<div class="book-card" data-engine="${esc(b.engine)}">
      <span class="name">${esc(b.engine)}</span>
      <span class="num">${b.trades}</span>
      <span class="num ${b.net_sum >= 0 ? "" : "neg"}">${b.net_sum >= 0 ? "+" : ""}${b.net_sum.toFixed(2)}%</span>
      <span class="num">${b.mean_net >= 0 ? "+" : ""}${b.mean_net.toFixed(3)}%</span>
      <span class="num">${Math.round(b.mean_held)}s</span>
      <span class="num">${b.refusals}</span></div>`).join("")}`;
  $("booksBody").querySelectorAll(".book-card[data-engine]").forEach((el) => {
    el.onclick = () => openBook(el.dataset.engine);
  });
}

async function openBook(engine) {
  const d = await (await fetch("/api/book?engine=" + encodeURIComponent(engine), { cache: "no-store" })).json();
  const s = d.summary || {};
  $("booksTitle").textContent = `book — ${engine}`;
  $("booksBody").innerHTML = `<div class="book-detail">
    <div class="ctx-actions" style="margin-bottom:10px">
      <button class="ctx-btn" id="bookReport">export regulatory report ↗</button>
      <button class="ctx-btn" id="bookBack">← all books</button>
    </div>
    <table class="kv" style="width:auto">
      <tr><td>Trades</td><td>${s.trades}</td></tr>
      <tr><td>Refusals</td><td>${s.refusals}</td></tr>
      <tr><td>Mean net</td><td>${s.mean_net_pct}%</td></tr>
      <tr><td>Sum of net</td><td>${s.net_pct_sum}%</td></tr>
      <tr><td>Mean held</td><td>${Math.round(s.mean_held_s)}s</td></tr>
      <tr><td>Longest hold</td><td>${Math.round(s.max_held_s)}s</td></tr>
      <tr><td>Instruments</td><td>${s.symbols}</td></tr>
    </table>
    <h3>trades</h3>
    <table><tr><th>time</th><th>instrument</th><th>side</th><th>held</th><th>net</th><th>envelope</th></tr>
      ${(d.trades || []).slice(0, 40).map((t) => `<tr data-id="${t.id}"><td>${fmtTime(t.ts)}</td>
        <td>${esc(t.key)}</td><td>${esc(t.side)}</td><td>${Math.round(t.held_s)}s</td>
        <td>${t.net_pct.toFixed(3)}%</td><td>${t.id}</td></tr>`).join("")}</table>
    <h3>refusals</h3>
    ${(d.refusals || []).length ? `<table><tr><th>time</th><th>instrument</th><th>rule</th></tr>
      ${d.refusals.slice(0, 20).map((x) => `<tr><td>${fmtTime(x.ts)}</td><td>${esc(x.key)}</td>
        <td>${esc(x.reason)}</td></tr>`).join("")}</table>` : `<div class="muted">none recorded</div>`}
  </div>`;
  $("bookReport").onclick = () => window.open("/api/book.html?engine=" + encodeURIComponent(engine), "_blank");
  $("bookBack").onclick = renderBooks;
  $("booksBody").querySelectorAll("tr[data-id]").forEach((tr) => {
    tr.onclick = () => select(Number(tr.dataset.id));
  });
}

/* The Gantt: horizontal spans. Positions show how long a trade was held; trace
   shows how long each input had been valid before the outcome read it. */
let ganttMode = "positions";

async function renderGantt() {
  const params = new URLSearchParams({ mode: ganttMode });
  if (ganttMode === "trace") {
    if (state.sel == null) {
      $("ganttTitle").textContent = "gantt — select an outcome to see its inputs";
      $("ganttSvg").innerHTML = "";
      $("ganttLegend").innerHTML = "";
      return;
    }
    params.set("id", String(state.sel));
  } else {
    params.set("limit", "40");
  }
  const d = await (await fetch("/api/gantt?" + params.toString(), { cache: "no-store" })).json();
  drawGantt(d);
}

function drawGantt(d) {
  const rows = d.rows || [];
  const svg = $("ganttSvg");
  if (!rows.length) {
    svg.innerHTML = '<text x="420" y="150" fill="#5a6b80" font-size="12">nothing to plot in this mode</text>';
    $("ganttTitle").textContent = "gantt — no data";
    return;
  }
  const padL = 128, padT = 26, padB = 26, rowH = 22;
  const t0 = Math.min(...rows.map((r) => r.start));
  const t1 = Math.max(...rows.map((r) => r.end));
  const span = Math.max(1000, t1 - t0);
  // scale so a typical bar is readable: the median duration gets ~120 px
  const durations = rows.map((r) => Math.max(1, r.end - r.start)).sort((a, b) => a - b);
  const median = durations[Math.floor(durations.length / 2)] || 60000;
  const pxPerSec = Math.min(0.4, Math.max(0.004, 120000 / median / 1000));
  const width = Math.min(14000, Math.max(900, Math.round((span / 1000) * pxPerSec) + padL + 40));
  const H = padT + rows.length * rowH + padB;
  svg.setAttribute("width", width);
  svg.setAttribute("height", H);
  svg.setAttribute("viewBox", `0 0 ${width} ${H}`);
  const x = (ts) => padL + ((ts - t0) / span) * (width - padL - 40);

  let out = "";
  const step = Math.max(5000, Math.round(span / 10));
  for (let t = t0; t <= t1; t += step) {
    out += `<line class="g-grid" x1="${x(t)}" y1="${padT - 10}" x2="${x(t)}" y2="${padT + rows.length * rowH}"/>`;
    out += `<text class="g-tick" x="${x(t) + 3}" y="${H - 8}">${fmtTime(t)}</text>`;
  }
  rows.forEach((r, i) => {
    const y = padT + i * rowH;
    const colour = PLANE_COLOUR["market"] && r.stage === 0 ? PLANE_COLOUR.market
      : r.stage === 1 ? PLANE_COLOUR.intel : r.stage === 2 ? PLANE_COLOUR.control : PLANE_COLOUR.model;
    const x0 = x(r.start), x1 = Math.max(x0 + 2, x(r.end));
    const label = r.label.length > 12 ? r.label.slice(0, 12) : r.label;
    out += `<text class="g-row-label" x="8" y="${y + 13}">${esc(label)}</text>`;
    out += `<g class="g-bar" data-id="${r.id}">
      <rect x="${x0}" y="${y + 3}" width="${x1 - x0}" height="${rowH - 8}" fill="${colour}" opacity="0.9"/>
      <text class="g-label" x="${x0 + 4}" y="${y + 14}">${esc((r.producer || "").slice(0, 10))}</text>
    </g>`;
  });
  svg.innerHTML = out;
  svg.querySelectorAll(".g-bar").forEach((el) => {
    el.onclick = () => select(Number(el.dataset.id));
  });
  const total = rows.length;
  const held = d.mode === "positions"
    ? rows.reduce((a, r) => a + (r.end - r.start), 0) / rows.length / 1000
    : 0;
  $("ganttTitle").textContent = d.mode === "positions"
    ? `positions — ${total} trades · mean hold ${held.toFixed(0)}s`
    : `trace — ${total} inputs leading to ${d.topic || ""} ${d.key || ""}`;
  $("ganttLegend").innerHTML = d.mode === "positions"
    ? `<table><tr><th>symbol</th><th>engine</th><th>held</th><th>net</th></tr>
        ${rows.slice(0, 12).map((r) => `<tr data-id="${r.id}"><td>${esc(r.label)}</td><td>${esc(r.producer)}</td>
        <td>${Math.round((r.end - r.start) / 1000)}s</td><td>${(r.value || 0).toFixed(2)}%</td></tr>`).join("")}</table>`
    : `<table><tr><th>input</th><th>producer</th><th>valid for</th></tr>
        ${rows.slice(0, 12).map((r) => `<tr data-id="${r.id}"><td>${esc(r.topic)}</td><td>${esc(r.producer)}</td>
        <td>${Math.round((r.end - r.start) / 1000)}s</td></tr>`).join("")}</table>`;
  $("ganttLegend").querySelectorAll("tr[data-id]").forEach((tr) => {
    tr.onclick = () => select(Number(tr.dataset.id));
  });
}

document.querySelectorAll("[data-gmode]").forEach((b) => {
  b.onclick = () => {
    ganttMode = b.dataset.gmode;
    document.querySelectorAll("[data-gmode]").forEach((x) => x.classList.toggle("on", x === b));
    renderGantt();
  };
});

/* The contextual surface: instead of choosing a mode, the selection decides what
   is relevant — a decision shows why and what it read, an input shows what it fed. */
async function renderContext() {
  const box = $("context");
  const d = findDoc(state.sel);
  if (!d) { box.innerHTML = '<div class="empty">select an envelope to see its anatomy</div>'; return; }
  const isOutcome = /^model\./.test(d.topic) || d.topic === "risk.state";
  const html = [];

  // 1 · identity
  html.push(`<div class="ctx">
    <h3>${esc(d.topic)}</h3>
    <div class="big">${esc(d.key || "multi-symbol")} <span class="muted">· ${esc(d.producer || "")} · ${fmtTime(d.ts)}</span></div>
    <div class="muted">${esc(TOPIC_MEANING[d.topic] || "")}</div>
  </div>`);

  // 2 · verdict, when the envelope is a decision
  if (d.topic === "model.decision") {
    try {
      const full = await (await fetch("/api/envelope/" + d.id, { cache: "no-store" })).json();
      let p = full.payload; if (typeof p === "string") p = JSON.parse(p);
      const inner = (p && p.data) ? p.data : p;
      const ok = inner.allowed !== false;
      html.push(`<div class="ctx">
        <h3>verdict</h3>
        <div><span class="verdict ${ok ? "yes" : "no"}">${ok ? "traded" : "refused"}</span>
        <span class="muted">${esc(inner.reason || "ok")}</span></div>
        <div class="muted" style="margin-top:6px">confidence ${esc(inner.confidence != null ? inner.confidence : "—")}</div>
      </div>`);
    } catch (e) {}
  }

  // 3 · what it read (outcomes) or what read it (inputs)
  if (isOutcome) {
    try {
      const t = await (await fetch("/api/execution?id=" + d.id, { cache: "no-store" })).json();
      const used = (t.nodes || []).filter((n) => n.age_ms >= 0 && !n.anchor).sort((a, b) => a.stage - b.stage);
      html.push(`<div class="ctx">
        <h3>what it read</h3>
        ${used.length ? `<table><tr><th>stage</th><th>topic</th><th>age</th></tr>
          ${used.map((n) => `<tr data-id="${n.id}"><td>${LANE_LABEL[n.stage] || n.stage}</td>
            <td>${esc(n.topic)}</td><td>${Math.round(n.age_ms / 1000)}s</td></tr>`).join("")}</table>`
          : `<div class="muted">no input envelopes for this symbol in the preceding window</div>`}
        <div class="ctx-actions"><button class="ctx-btn" id="ctxTrace">open full trace →</button></div>
      </div>`);
    } catch (e) {}
  } else if (d.key) {
    try {
      const later = (state.docs || []).filter((x) => x.key === d.key && x.ts > d.ts).slice(0, 6).reverse();
      if (later.length) {
        html.push(`<div class="ctx">
          <h3>what read it next</h3>
          <table><tr><th>topic</th><th>producer</th><th>when</th></tr>
            ${later.map((n) => `<tr data-id="${n.id}"><td>${esc(n.topic)}</td>
              <td>${esc(n.producer || "")}</td><td>${fmtTime(n.ts)}</td></tr>`).join("")}</table>
        </div>`);
      } else {
        // nothing downstream yet: show where this message sits in its own chain
        const chain = await (await fetch("/api/trace?key=" + encodeURIComponent(d.key), { cache: "no-store" })).json();
        const before = (chain.chain || []).filter((x) => x.ts < d.ts).slice(-6);
        html.push(`<div class="ctx">
          <h3>chain — what led here</h3>
          ${before.length ? `<table><tr><th>topic</th><th>producer</th><th>when</th></tr>
            ${before.map((n) => `<tr data-id="${n.id}"><td>${esc(n.topic)}</td>
              <td>${esc(n.producer || "")}</td><td>${fmtTime(n.ts)}</td></tr>`).join("")}</table>`
            : `<div class="muted">this is the first envelope recorded for ${esc(d.key)}</div>`}
          <div class="muted" style="margin-top:8px">nothing downstream yet — this message has not been read by a model in the window</div>
        </div>`);
      }
    } catch (e) {}
  }

  // multi-symbol snapshots have no chain; explain what they are instead of an empty page
  if (!d.key) {
    html.push(`<div class="ctx">
      <h3>this message carries no single symbol</h3>
      <div class="muted">${esc(d.topic)} is a snapshot across every streamed symbol, so there is no per-symbol
      chain to walk. Open the flow to see where it sits in the pipeline.</div>
    </div>`);
  }

  // 4 · ways out, contextual
  html.push(`<div class="ctx">
    <h3>explore</h3>
    <div class="ctx-actions">
      <button class="ctx-btn" id="ctxFlow">flow for ${esc(d.key || "all symbols")} →</button>
      <button class="ctx-btn" id="ctxGantt">gantt →</button>
      <button class="ctx-btn" id="ctxCase">export case file ↗</button>
      ${d.topic === "model.decision" || /^model\./.test(d.topic)
        ? `<button class="ctx-btn" id="ctxWhy">why ${esc(d.key || "")} traded or not →</button>` : ""}
    </div>
  </div>`);

  box.innerHTML = html.join("");
  box.querySelectorAll("tr[data-id]").forEach((tr) => {
    tr.onclick = () => select(Number(tr.dataset.id));
  });
  if ($("ctxTrace")) $("ctxTrace").onclick = () => setView("execution");
  if ($("ctxFlow")) $("ctxFlow").onclick = () => setView("journey");
  if ($("ctxGantt")) $("ctxGantt").onclick = () => setView("gantt");
  if ($("ctxCase")) $("ctxCase").onclick = () => window.open("/api/case.html?id=" + state.sel, "_blank");
  if ($("ctxWhy")) $("ctxWhy").onclick = () => { state.whyKey = d.key || ""; setView("why"); };
}

/* WHY: did the model trade, and if not, which rule stopped it. */
async function renderWhy() {
  const params = new URLSearchParams();
  if (state.whyKey) params.set("key", state.whyKey);
  if (state.whyEngine) params.set("engine", state.whyEngine);
  if (state.whyHours) params.set("from", String(Date.now() - state.whyHours * 3600 * 1000));
  if (state.whyOutcome && state.whyOutcome !== "any") params.set("outcome", state.whyOutcome);
  params.set("limit", "80");
  const d = await (await fetch("/api/why?" + params.toString(), { cache: "no-store" })).json();
  const search = await (await fetch("/api/search?" + params.toString(), { cache: "no-store" })).json();
  const allowed = d.allowed || 0, refused = d.refused || 0;
  if (search && search.hits) {
    d.decisions = search.hits.map((h) => ({ id: h.id, ts: h.ts, key: h.key, engine: h.engine,
      allowed: h.allowed, reason: h.reason, confidence: 0, topic: h.topic }));
  }
  $("whyTitle").textContent = `why — ${allowed} traded · ${refused} refused` + (d.key ? " · " + d.key : "");
  const reasons = d.reasons || [];
  const max = Math.max(...reasons.map((r) => r.count), 1);
  $("whyReasons").innerHTML = reasons.length
    ? `<div style="font-size:9px;color:var(--faint);letter-spacing:.7px;text-transform:uppercase;margin-bottom:8px">why not</div>` +
      reasons.slice(0, 10).map((r) => `
        <div class="why-reason"><div class="lbl"><span>${esc(r.reason.split(":")[0])}</span><b>${r.count}</b></div>
        <div class="bar" style="width:${Math.round((r.count / max) * 100)}%"></div></div>`).join("")
    : `<div style="color:var(--faint);font-size:11px">no refusals in the scan</div>`;
  const rows = d.decisions || [];
  $("whyList").innerHTML = rows.length ? rows.map((x) => `
    <div class="why-row" data-idx="${rows.indexOf(x)}">
      <span class="t">${fmtTime(x.ts)}</span>
      <span>${esc(x.key || "")}</span>
      <span class="reason" title="${esc(x.reason || "")}">${esc(x.reason || "ok")}</span>
      <span class="outcome ${x.allowed ? "yes" : "no"}">${x.allowed ? "traded" : "refused"}</span>
      <span style="color:var(--faint)">${esc((x.engine || "").slice(0, 8))}</span>
    </div>`).join("") : `<div class="empty" style="padding:24px">no decisions recorded</div>`;
  $("whyList").querySelectorAll(".why-row").forEach((el) => {
    const idx = Number(el.dataset.idx);
    el.onclick = () => {
      const row = rows[idx];
      if (row && row.payload) {
        renderDetail({ id: row.id, topic: row.topic || "model.decision", key: row.key,
          ts: row.ts, plane: "model", producer: row.producer || row.engine,
          version: 1, generation: 0, size: JSON.stringify(row.payload).length,
          payload: row.payload, meta: row.meta, has_envelope: !!row.meta });
      }
    };
    el.ondblclick = () => { renderExecution(); setView("execution"); };
  });
}

/* The execution trace: one decision, the data it read, on a scrollable timeline. */
async function renderExecution(idHint) {
  const id = idHint || state.sel;
  if (!id) {
    $("exTitle").textContent = "execution trace — select a message first";
    $("exSvg").innerHTML = "";
    $("exUsed").innerHTML = "";
    return;
  }
  const d = await (await fetch("/api/execution?id=" + id, { cache: "no-store" })).json();
  if (!d || !d.nodes) return;
  $("exTitle").textContent = `${d.topic} · ${d.key || "all symbols"} · ${d.nodes.length} nodes · window ${Math.round((d.to - d.from) / 1000)}s`;
  drawExecution(d);
}

function drawExecution(d) {
  const nodes = d.nodes || [], path = new Set(d.path || []);
  const svg = $("exSvg");
  const lanes = 5, laneH = 46, padL = 86, padT = 18;
  const span = Math.max(1, d.to - d.from);
  const pxPerSec = 4 * exZoom;
  const width = Math.min(12000, Math.max(900, Math.round((span / 1000) * pxPerSec) + padL + 40));
  const H = padT + lanes * laneH + 28;
  svg.setAttribute("width", width);
  svg.setAttribute("height", H);
  svg.setAttribute("viewBox", `0 0 ${width} ${H}`);
  const x = (ts) => padL + ((ts - d.from) / span) * (width - padL - 40);
  const laneY = (stage) => padT + stage * laneH + laneH / 2;

  let out = "";
  for (let i = 0; i < lanes; i++) {
    out += `<rect class="ex-lane" x="${padL - 8}" y="${padT + i * laneH + 6}" width="${width - padL - 24}" height="${laneH - 14}" rx="6"/>`;
    out += `<text class="ex-lane-label" x="10" y="${padT + i * laneH + laneH / 2 + 3}">${LANE_LABEL[i]}</text>`;
  }
  // time ticks every 30 s of the window
  const step = Math.max(15000, Math.round(span / 12));
  for (let t = d.from; t <= d.to; t += step) {
    const tx = x(t);
    out += `<line x1="${tx}" y1="${padT}" x2="${tx}" y2="${padT + lanes * laneH}" stroke="#16202b"/>`;
    out += `<text class="ex-tick" x="${tx + 3}" y="${H - 8}">${fmtTime(t)}</text>`;
  }
  // the path: a polyline through the chosen nodes in reading order
  const ordered = nodes.filter((n) => path.has(n.id)).sort((a, b) => a.ts - b.ts);
  if (ordered.length > 1) {
    const pts = ordered.map((n) => `${x(n.ts)},${laneY(n.stage)}`).join(" ");
    out += `<polyline class="ex-path" points="${pts}"/>`;
  }
  nodes.forEach((n) => {
    const colour = PLANE_COLOUR[n.plane] || "#888";
    const w = Math.min(150, 40 + n.topic.length * 4.6);
    const short = n.topic.replace("market.", "").replace("intel.", "").replace("model.", "");
    const age = n.age_ms >= 0 ? `−${Math.round(n.age_ms / 1000)}s` : `+${Math.round(-n.age_ms / 1000)}s`;
    out += `<g class="ex-node ${n.anchor ? "ex-anchor" : ""}" data-id="${n.id}">
      <rect x="${x(n.ts)}" y="${laneY(n.stage) - 11}" width="${w}" height="22" fill="${colour}" opacity="0.92"/>
      <text x="${x(n.ts) + 5}" y="${laneY(n.stage) + 3}">${esc(short)} ${age}</text>
    </g>`;
  });
  // a vertical marker at the anchor, and the scroll brought to it: an execution
  // trace is read at the moment of execution, not at the start of the window
  const anchorNode = nodes.find((n) => n.anchor);
  if (anchorNode) {
    const ax = x(anchorNode.ts);
    out += `<line x1="${ax}" y1="${padT - 6}" x2="${ax}" y2="${padT + lanes * laneH}" stroke="#e6eef8" stroke-width="1.5" stroke-dasharray="4 3" opacity="0.8"/>`;
  }
  svg.innerHTML = out;
  svg.querySelectorAll(".ex-node").forEach((el) => {
    el.onclick = () => select(Number(el.dataset.id));
  });
  const scroll = $("exScroll");
  if (anchorNode) {
    const ax = x(anchorNode.ts);
    scroll.scrollLeft = Math.max(0, ax - scroll.clientWidth * 0.62);
  }

  // what the anchor used, with age at the moment of execution
  const used = nodes.filter((n) => n.age_ms >= 0 && !n.anchor).sort((a, b) => a.stage - b.stage);
  $("exUsed").innerHTML = `<table>
    <tr><th>stage</th><th>topic</th><th>producer</th><th>age at execution</th><th>bytes</th></tr>
    ${used.map((n) => `<tr data-id="${n.id}">
      <td>${LANE_LABEL[n.stage] || n.stage}</td>
      <td>${esc(n.topic)}</td>
      <td>${esc(n.producer || "")}</td>
      <td>${Math.round(n.age_ms / 1000)}s</td>
      <td>${n.size} B</td></tr>`).join("")}
  </table>`;
  $("exUsed").querySelectorAll("tr[data-id]").forEach((tr) => {
    tr.onclick = () => select(Number(tr.dataset.id));
  });
}

$("whyRun").onclick = () => {
  state.whyKey = $("whyKey").value.trim();
  state.whyEngine = $("whyEngine").value.trim();
  state.whyHours = Number($("whyHours").value);
  state.whyOutcome = $("whyOutcome").value;
  renderWhy();
};
$("whyKey").onkeydown = (e) => { if (e.key === "Enter") $("whyRun").click(); };
$("whyEngine").onkeydown = (e) => { if (e.key === "Enter") $("whyRun").click(); };

document.querySelectorAll(".ex-controls .mini").forEach((b) => {
  b.onclick = () => {
    exZoom = Number(b.dataset.zoom);
    document.querySelectorAll(".ex-controls .mini").forEach((x) => x.classList.toggle("on", x === b));
    renderExecution();
  };
});

/* The flow view: stages as columns, topics as boxes sized by volume, links as
   ribbons whose width is the number of correlations observed. */
/* A journey starts at a point: the selected envelope becomes the anchor, the
   window opens from its timestamp and runs forward on its symbol. */
async function renderJourney(anchorId) {
  const params = new URLSearchParams();
  const anchor = anchorId || state.sel;
  if (anchor) {
    params.set("id", String(anchor));
  } else {
    if (state.journeyKey) params.set("key", state.journeyKey);
    if (state.from) params.set("from", String(state.from));
    if (state.to) params.set("to", String(state.to));
  }
  const d = await (await fetch("/api/flow?" + params.toString(), { cache: "no-store" })).json();
  state.journeyKey = d.key || "";
  const stamp = anchor ? " · from " + fmtTime(d.from) : " · last 5 minutes";
  $("journeyKey").textContent = "key: " + (d.key || "all symbols") + stamp;
  drawFlow(d);
}

function drawFlow(d) {
  const nodes = d.nodes || [], links = d.links || [];
  const producersByTopic = {};
  nodes.forEach((n) => {
    producersByTopic[n.topic] = producersByTopic[n.topic] || new Set();
    if (n.producer) producersByTopic[n.topic].add(n.producer);
  });
  const sharedTopics = new Set(Object.keys(producersByTopic).filter((t) => producersByTopic[t].size > 1));
  const anchorIds = new Set(nodes.filter((n) => n.id === d.anchor).map((n) => n.id));
  const svg = $("journeySvg");
  if (!nodes.length) {
    svg.innerHTML = '<text x="450" y="320" text-anchor="middle" fill="#5a6b80" font-size="12">no traffic in this window</text>';
    return;
  }
  const W = 900, H = 640, padX = 34, padTop = 58, padBottom = 34;
  const stages = [...new Set(nodes.map((n) => n.stage))].sort((a, b) => a - b);
  const colW = (W - padX * 2) / stages.length;
  const maxCount = Math.max(...nodes.map((n) => n.count), 1);

  // height by volume, then normalise so each column fits the canvas
  const byStage = {};
  stages.forEach((st) => (byStage[st] = nodes.filter((n) => n.stage === st)));
  const pos = {};
  stages.forEach((st, ci) => {
    const list = byStage[st];
    const heights = list.map((n) => 34 + 120 * Math.sqrt(n.count / maxCount));
    const total = heights.reduce((a, b) => a + b, 0) + 18 * (list.length - 1);
    const usable = H - padTop - padBottom;
    const scale = total > usable ? usable / total : 1;
    let y = padTop + (usable - total * scale) / 2;
    list.forEach((n, i) => {
      const h = heights[i] * scale;
      pos[n.id] = { x: padX + ci * colW + 14, y, w: colW - 46, h, node: n };
      y += h + 18 * scale;
    });
  });

  const linkSvg = links.filter((l) => pos[l.from] && pos[l.to]).map((l) => {
    const a = pos[l.from], b = pos[l.to];
    const x1 = a.x + a.w, y1 = a.y + a.h / 2;
    const x2 = b.x, y2 = b.y + b.h / 2;
    const mx = x1 + (x2 - x1) * 0.45;
    const width = 1 + 3.2 * Math.log10(1 + l.count);
    const colour = PLANE_COLOUR[a.node.plane] || "#4f9bff";
    return `<path d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}"
      fill="none" stroke="${colour}" stroke-width="${width.toFixed(1)}"
      stroke-opacity="0.42" stroke-linecap="round"><title>${l.count} correlated</title></path>`;
  }).join("");

  const nodeSvg = Object.values(pos).map((p) => {
    const n = p.node;
    const fill = PLANE_COLOUR[n.plane] || "#888";
    const short = n.topic.replace("market.", "").replace("intel.", "").replace("model.", "");
    const who = n.producer && sharedTopics.has(n.topic) ? " · " + n.producer : "";
    const label = (n.count > 1 ? `${short}${who}  ${n.count.toLocaleString()}` : short + who);
    const span = fmtTime(n.first_ts) + "–" + fmtTime(n.last_ts);
    return `<g class="jnode" data-id="${n.id}">
      <rect x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}" rx="6" fill="${fill}" opacity="0.92"/>
      <text x="${p.x + 9}" y="${p.y + p.h / 2 - 2}" font-size="10" font-weight="600" fill="#06121f">${esc(label)}</text>
      <text x="${p.x + 9}" y="${p.y + p.h / 2 + 10}" font-size="7.5" fill="#12283d" opacity="0.8">${esc(span)}</text>
    </g>`;
  }).join("");

  const stageSvg = stages.map((st, ci) =>
    `<text class="jstage" x="${padX + ci * colW + 14}" y="${padTop - 26}">${STAGE_LABEL[st]}</text>`).join("");

  // the anchor node — the point the journey starts from — is ringed
  const anchorRing = Object.values(pos)
    .filter((p) => p.node.anchor)
    .map((p) => `<rect x="${p.x - 3}" y="${p.y - 3}" width="${p.w + 6}" height="${p.h + 6}"
      rx="9" fill="none" stroke="#e6eef8" stroke-width="2" stroke-dasharray="5 3"/>`).join("");

  svg.innerHTML = stageSvg + linkSvg + nodeSvg + anchorRing;
  svg.querySelectorAll(".jnode").forEach((el) => {
    el.onclick = () => select(Number(el.dataset.id));
  });
  svg.querySelectorAll(".jnode").forEach((el) => {
    el.addEventListener("dblclick", (ev) => {
      ev.stopPropagation();
      renderJourney(Number(el.dataset.id));
    });
  });
  const totalCount = nodes.reduce((a, n) => a + n.count, 0);
  $("journeyLegend").innerHTML = ["market", "intel", "model", "control", "system"].map((p) =>
    `<span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${PLANE_COLOUR[p]};margin-right:5px"></span>${p}</span>`).join("")
    + `<span style="margin-left:auto">${nodes.length} topics · ${totalCount.toLocaleString()} envelopes · ${links.length} links</span>`;
}

/* Resizable panels: widths live in CSS variables and persist per browser. */
(function resizable() {
  const grid = $("grid");
  if (!grid) return;
  const saved = JSON.parse(localStorage.getItem("ex.layout") || "{}");
  if (saved.c1) grid.style.setProperty("--c1", saved.c1 + "px");
  if (saved.c3) grid.style.setProperty("--c3", saved.c3 + "fr");
  if (saved.timeline) $("timelineBar").style.height = saved.timeline + "px";

  function drag(handle, axis, onMove, onDone) {
    handle.addEventListener("mousedown", (e) => {
      e.preventDefault();
      document.body.classList.add("resizing");
      if (axis === "row") document.body.classList.add("row");
      const move = (ev) => onMove(ev);
      const up = () => {
        document.body.classList.remove("resizing", "row");
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", up);
        onDone();
      };
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
    });
  }

  const persist = () => localStorage.setItem("ex.layout", JSON.stringify({
    c1: parseInt(getComputedStyle(grid).gridTemplateColumns.split(" ")[0], 10),
    c3: 1.15,
    timeline: parseInt($("timelineBar").getBoundingClientRect().height, 10),
  }));

  drag($("vsplit1"), "col", (ev) => {
    const rect = grid.getBoundingClientRect();
    const w = Math.min(Math.max(160, ev.clientX - rect.left), rect.width * 0.45);
    grid.style.setProperty("--c1", w + "px");
  }, persist);

  drag($("vsplit2"), "col", (ev) => {
    const rect = grid.getBoundingClientRect();
    const right = Math.min(Math.max(240, rect.right - ev.clientX), rect.width * 0.62);
    grid.style.setProperty("--c3", right + "px");
  }, persist);

  drag($("hsplit"), "row", (ev) => {
    const top = $("timelineBar").getBoundingClientRect().top;
    const h = Math.min(Math.max(72, ev.clientY - top - 6), 320);
    $("timelineBar").style.height = h + "px";
  }, persist);
})();

/* Contextual tabs: a view is enabled when the selection can feed it. */
function updateTabs() {
  const execTab = document.querySelector('.tab[data-view="execution"]');
  if (!execTab) return;   // the tab bar became breadcrumbs; chips are contextual now
  const d = state.docs.find((x) => x.id === state.sel);
  const topic = d ? d.topic : "";
  const isOutcome = /^model\./.test(topic) || topic === "risk.state";
  execTab.classList.toggle("disabled", !isOutcome);
  execTab.title = isOutcome ? "the inputs this outcome read" : "select a decision or a fill first";
  const whyTab = document.querySelector('.tab[data-view="why"]');
  whyTab.title = d && d.key ? "why " + d.key + " traded or not" : "all decisions";
}

/* wiring */
document.querySelectorAll("#planeChips .chip").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll("#planeChips .chip").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    state.plane = b.dataset.plane;
    load();
  };
});
$("topic").onchange = (e) => { state.topic = e.target.value; load(); };
let timer = null;
function debounced() {
  clearTimeout(timer);
  timer = setTimeout(() => {
    state.key = $("key").value.trim();
    state.producer = $("producer").value.trim();
    state.cid = $("cid").value.trim();
    state.q = $("q").value.trim();
    load();
  }, 220);
}
["key", "producer", "cid", "q"].forEach((id) => ($(id).oninput = debounced));
$("reset").onclick = () => {
  state.plane = state.topic = state.key = state.producer = state.cid = state.q = "";
  ["key", "producer", "cid", "q"].forEach((id) => ($(id).value = ""));
  $("topic").value = "";
  document.querySelectorAll("#planeChips .chip").forEach((x) => x.classList.remove("active"));
  document.querySelector('#planeChips .chip[data-plane=""]').classList.add("active");
  load();
};
// navigation history: every view change and every selection records what was on
// screen, so Escape restores the last visible state rather than a guess
const viewStack = [];
let restoring = false;

function snapshotState() {
  return {
    view: state.view,
    sel: state.sel,
    journeyKey: state.journeyKey,
    whyKey: state.whyKey,
    whyEngine: state.whyEngine,
    rowsScroll: ($("rows") || {}).scrollTop || 0,
    exScroll: ($("exScroll") || {}).scrollLeft || 0,
  };
}

function pushState() {
  if (restoring) return;
  viewStack.push(snapshotState());
  if (viewStack.length > 60) viewStack.shift();
  updateBackBtn();
}

async function restoreState(snap) {
  restoring = true;
  try {
    state.journeyKey = snap.journeyKey;
    state.whyKey = snap.whyKey;
    state.whyEngine = snap.whyEngine;
    state.sel = snap.sel;
    if (snap.view !== state.view) applyView(snap.view);
    renderRows();
    if (snap.sel != null) {
      await select(snap.sel, true);
    } else {
      $("detail").innerHTML = '<div class="empty">select an envelope to see its anatomy</div>';
    }
    if ($("rows")) $("rows").scrollTop = snap.rowsScroll || 0;
    if ($("exScroll")) $("exScroll").scrollLeft = snap.exScroll || 0;
  } finally {
    restoring = false;
    updateBackBtn();
  }
}

function applyView(view) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
  state.view = view;
  const rows = $("rows"), journey = $("journey"), panel = document.querySelector(".panel.list");
  const execution = $("execution");
  const why = $("why");
  const context = $("context");
  const gantt = $("gantt");
  const books = $("books");
  const health = $("health");
  gantt.classList.toggle("hidden", view !== "gantt");
  books.classList.toggle("hidden", view !== "books");
  health.classList.toggle("hidden", view !== "health");
  if (view === "gantt") renderGantt();
  if (view === "books") renderBooks();
  if (view === "health") renderHealth();
  gantt.classList.toggle("hidden", view !== "gantt");
  if (view === "gantt") renderGantt();
  rows.classList.toggle("hidden", view !== "list");
  context.classList.toggle("hidden", view !== "context");
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.view === view && view !== "context"));
  if (view === "context") renderContext();
  journey.classList.toggle("hidden", view !== "journey");
  execution.classList.toggle("hidden", view !== "execution");
  why.classList.toggle("hidden", view !== "why");
  panel.classList.toggle("journey-on", view !== "list");
  if (view === "journey") renderJourney();
  if (view === "execution") renderExecution();
  if (view === "why") renderWhy();
}

function setView(view, remember) {
  if (state.view === view) return;

  if (remember !== false) pushState();
  applyView(view);
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    setView(tab.dataset.view);
  };
});

document.querySelectorAll(".crumb").forEach((b) => {
  b.onclick = () => { state.sel = null; renderRows(); applyView("list"); $("crumbHere").textContent = "nothing selected"; };
});

document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = () => setView(tab.dataset.view);
});

// Escape is "back": it pops the view stack, and when there is nothing to pop it
// clears the selected envelope.
function canGoBack() {
  return viewStack.length > 0 || state.sel != null;
}

function goBack() {
  if (viewStack.length) {
    restoreState(viewStack.pop());
  } else if (state.sel != null) {
    state.sel = null;
    state.journeyKey = "";
    renderRows();
    $("detail").innerHTML = '<div class="empty">select an envelope to see its anatomy</div>';
    $("crumbHere").textContent = "nothing selected";
    if (state.view !== "list") applyView("list");
    updateBackBtn();
  }
}

function updateBackBtn() {
  const b = $("backBtn");
  if (b) b.disabled = !canGoBack();
}

document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") { ev.preventDefault(); goBack(); }
});

if ($("backBtn")) $("backBtn").onclick = () => goBack();
if ($("booksBtn")) $("booksBtn").onclick = () => setView("books");
if ($("booksRefresh")) $("booksRefresh").onclick = () => renderBooks();

$("role").onchange = (e) => {
  state.role = e.target.value;
  if (state.sel != null) select(state.sel);
};
$("live").onclick = () => {
  state.live = !state.live;
  $("live").classList.toggle("on", state.live);
  $("live").textContent = state.live ? "live" : "paused";
};

loadStats(); loadTopics(); loadTimeline(); load(); connect(); updateTabs();
setInterval(loadTimeline, 5000);
setInterval(loadStats, 5000);
