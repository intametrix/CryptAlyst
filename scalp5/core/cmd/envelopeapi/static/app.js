/* The Envelope Explorer — a read-only lens on the lake. */
const $ = (id) => document.getElementById(id);
const state = { plane: "", topic: "", key: "", producer: "", cid: "", q: "", docs: [], sel: null,
  live: true, role: "owner", from: 0, to: 0, buckets: [], playing: null, span: 0,
  view: "list", journeyKey: "" };

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

function metaField(meta, name) {
  if (!meta) return "";
  const v = meta[name];
  if (v === undefined || v === null || v === "") return "";
  return String(v);
}

async function select(id) {
  state.sel = id;
  const picked = state.docs.find((x) => x.id === id);
  if (picked && picked.key) state.journeyKey = picked.key;
  renderRows();
  try {
    const d = await (await fetch("/api/envelope/" + id + "?role=" + encodeURIComponent(state.role), { cache: "no-store" })).json();
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

    $("detail").innerHTML = notice + `
      <div class="dhead">
        <div>
          <h2>${esc(d.topic)}${d.key ? " · " + esc(d.key) : ""}</h2>
          <div class="sub"><span class="pill ${esc(d.plane)}">${esc(d.plane)}</span>
            &nbsp;${esc(d.producer || "unknown producer")} &nbsp;·&nbsp; ${fmtTime(d.ts)}</div>
          <div class="sub" style="margin-top:5px;color:var(--dim)">${esc(TOPIC_MEANING[d.topic] || "")}</div>
        </div>
      </div>
      <div class="section"><h3>Meta — provenance and freshness</h3><dl class="kv">${rows}</dl></div>
      <div class="section"><h3>Data — the payload</h3><pre>${colourJSON(payload)}</pre></div>
      ${refs && refs.length ? `<div class="section"><h3>References — what it was computed from</h3><pre>${colourJSON(refs)}</pre></div>` : ""}
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
    if (state.view === "journey") renderJourney(d.key || state.journeyKey);
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

/* The flow view: stages as columns, topics as boxes sized by volume, links as
   ribbons whose width is the number of correlations observed. */
async function renderJourney(keyHint) {
  const key = keyHint || state.journeyKey || state.key || "";
  const params = new URLSearchParams();
  if (key) params.set("key", key);
  if (state.from) params.set("from", String(state.from));
  if (state.to) params.set("to", String(state.to));
  const d = await (await fetch("/api/flow?" + params.toString(), { cache: "no-store" })).json();
  state.journeyKey = key;
  $("journeyKey").textContent = "key: " + (key || "all symbols");
  drawFlow(d);
}

function drawFlow(d) {
  const nodes = d.nodes || [], links = d.links || [];
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
    const label = n.count > 1 ? `${short}  ${n.count.toLocaleString()}` : short;
    const span = fmtTime(n.first_ts) + "–" + fmtTime(n.last_ts);
    return `<g class="jnode" data-id="${n.id}">
      <rect x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}" rx="6" fill="${fill}" opacity="0.92"/>
      <text x="${p.x + 9}" y="${p.y + p.h / 2 - 2}" font-size="10" font-weight="600" fill="#06121f">${esc(label)}</text>
      <text x="${p.x + 9}" y="${p.y + p.h / 2 + 10}" font-size="7.5" fill="#12283d" opacity="0.8">${esc(span)}</text>
    </g>`;
  }).join("");

  const stageSvg = stages.map((st, ci) =>
    `<text class="jstage" x="${padX + ci * colW + 14}" y="${padTop - 26}">${STAGE_LABEL[st]}</text>`).join("");

  svg.innerHTML = stageSvg + linkSvg + nodeSvg;
  svg.querySelectorAll(".jnode").forEach((el) => {
    el.onclick = () => select(Number(el.dataset.id));
  });
  const totalCount = nodes.reduce((a, n) => a + n.count, 0);
  $("journeyLegend").innerHTML = ["market", "intel", "model", "control", "system"].map((p) =>
    `<span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${PLANE_COLOUR[p]};margin-right:5px"></span>${p}</span>`).join("")
    + `<span style="margin-left:auto">${nodes.length} topics · ${totalCount.toLocaleString()} envelopes · ${links.length} links</span>`;
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
document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    state.view = tab.dataset.view;
    const rows = $("rows"), journey = $("journey"), panel = document.querySelector(".panel.list");
    rows.classList.toggle("hidden", state.view !== "list");
    journey.classList.toggle("hidden", state.view !== "journey");
    panel.classList.toggle("journey-on", state.view === "journey");
    if (state.view === "journey") renderJourney();
  };
});

$("role").onchange = (e) => {
  state.role = e.target.value;
  if (state.sel != null) select(state.sel);
};
$("live").onclick = () => {
  state.live = !state.live;
  $("live").classList.toggle("on", state.live);
  $("live").textContent = state.live ? "live" : "paused";
};

loadStats(); loadTopics(); loadTimeline(); load(); connect();
setInterval(loadTimeline, 5000);
setInterval(loadStats, 5000);
