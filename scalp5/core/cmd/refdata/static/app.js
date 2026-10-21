/* Reference Data explorer — talks to the refdata service /v1 API.
   Vanilla JS, no build step; served embedded from cmd/refdata. */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const PRED_COLOR = {
  same_as: "#a78bfa", listed_on: "#4f9bff", has_document: "#37c98b",
  issued_by: "#43c6d8", tracks: "#43c6d8", derived_from: "#43c6d8",
};

/* ---------- resizable / collapsible split panels ----------
   Wide screens: two-column grid with a draggable divider; width and
   collapse state persist in localStorage. Narrow screens (CSS switches to
   drawer mode): the panel slides in from the right over a backdrop. */
const panels = {}; // key -> {aside, split, apply}

function wireSplit(key, splitSel, vsplitSel, asideSel) {
  const split = $(splitSel), vs = $(vsplitSel), aside = $(asideSel);
  const btn = aside.querySelector(".dcollapse");
  const state = { aside, split, apply };
  panels[key] = state;

  function apply() {
    const collapsed = aside.classList.contains("collapsed");
    split.classList.toggle("nodetail", collapsed);
    btn.textContent = collapsed ? "◂" : "▾";
    btn.title = collapsed ? "expand panel" : "collapse panel (persisted)";
    if (document.body.classList.contains("narrow")) updateBackdrop();
  }
  btn.addEventListener("click", () => {
    aside.classList.toggle("collapsed");
    localStorage.setItem(`refdata:${key}:collapsed`, aside.classList.contains("collapsed") ? "1" : "0");
    apply();
  });
  if (localStorage.getItem(`refdata:${key}:collapsed`) === "1") aside.classList.add("collapsed");
  const savedW = parseInt(localStorage.getItem(`refdata:${key}:w`) || "0", 10);
  if (savedW >= 280 && savedW <= 900) split.style.setProperty("--detail-w", savedW + "px");

  vs.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    vs.setPointerCapture(e.pointerId);
    document.body.classList.add("resizing");
    const move = (ev) => {
      const w = split.getBoundingClientRect().right - ev.clientX;
      if (w >= 280 && w <= 900) split.style.setProperty("--detail-w", w + "px");
    };
    const up = (ev) => {
      vs.removeEventListener("pointermove", move);
      vs.removeEventListener("pointerup", up);
      document.body.classList.remove("resizing");
      const w = Math.round(split.getBoundingClientRect().right - ev.clientX);
      if (w >= 280 && w <= 900) localStorage.setItem(`refdata:${key}:w`, String(w));
    };
    vs.addEventListener("pointermove", move);
    vs.addEventListener("pointerup", up);
  });
  apply();
  return state;
}

function openDetail(key) {
  const p = panels[key];
  if (!p) return;
  p.aside.classList.remove("collapsed");
  localStorage.setItem(`refdata:${key}:collapsed`, "0");
  p.apply();
}

let backdrop = null;
function updateBackdrop() {
  const anyOpen = Object.values(panels).some((p) =>
    p.aside.closest(".tab").classList.contains("on") && !p.aside.classList.contains("collapsed"));
  if (anyOpen && document.body.classList.contains("narrow")) {
    if (!backdrop) {
      backdrop = document.createElement("div");
      backdrop.className = "backdrop";
      backdrop.addEventListener("click", () =>
        Object.values(panels).forEach((p) => {
          if (!p.aside.classList.contains("collapsed")) p.aside.querySelector(".dcollapse").click();
        }));
      document.body.appendChild(backdrop);
    }
  } else if (backdrop) {
    backdrop.remove();
    backdrop = null;
  }
}
const narrowMQ = window.matchMedia("(max-width:1080px)");
function onNarrow() {
  document.body.classList.toggle("narrow", narrowMQ.matches);
  Object.values(panels).forEach((p) => p.apply());
  updateBackdrop();
}
narrowMQ.addEventListener("change", onNarrow);

async function api(path) {
  const r = await fetch(path, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  const env = await r.json();
  if (env && typeof env === "object" && "data" in env && env.meta) return env.data;
  return env;
}

/* ---------- tabs ---------- */
$$("#tabs button").forEach((b) =>
  b.addEventListener("click", () => {
    $$("#tabs button").forEach((x) => x.classList.toggle("on", x === b));
    $$(".tab").forEach((t) => t.classList.toggle("on", t.id === `tab-${b.dataset.tab}`));
    updateBackdrop();
  }));
const showTab = (name) => $(`#tabs button[data-tab="${name}"]`).click();

/* ---------- stats header ---------- */
async function refreshStats() {
  try {
    const s = await api("/v1/stats");
    $("#stats").innerHTML = [
      ["instruments", s.instruments], ["documents", s.documents],
      ["edges", s.edges], ["identifiers", s.identifiers],
    ].map(([k, v]) => `<div class="stat"><b>${v}</b><span>${k}</span></div>`).join("");
  } catch { /* service momentarily unavailable: keep last stats */ }
}
refreshStats();
setInterval(refreshStats, 5000);

/* ---------- generic sortable table ---------- */
/* suppressSortClick: set while a column-resize drag ends, so the click that
   follows pointerup does not also toggle sort on that header. */
let suppressSortClick = false;
function wireSort(theadSel, tbodySel, rows, render, state) {
  const thead = $(theadSel);
  thead.addEventListener("click", (e) => {
    if (suppressSortClick || e.target.closest(".rz")) return;
    const th = e.target.closest("th");
    if (!th) return;
    const k = th.dataset.k;
    state.dir = state.key === k && state.dir === "asc" ? "desc" : "asc";
    state.key = k;
    thead.querySelectorAll("th").forEach((x) => x.classList.remove("sorted", "asc", "desc"));
    th.classList.add("sorted", state.dir);
    render(sorted(rows(), k, state.dir));
  });
}
function sorted(rows, k, dir) {
  const m = dir === "asc" ? 1 : -1;
  const col = INST_COLS.find((c) => c.k === k);
  return [...rows].sort((a, b) => {
    let va = col && col.sk ? col.sk(a) : a[k];
    let vb = col && col.sk ? col.sk(b) : b[k];
    if (Array.isArray(va)) va = va.join(",");
    if (typeof va === "object" && va) va = Object.keys(va).length;
    if (Array.isArray(vb)) vb = vb.join(",");
    if (typeof vb === "object" && vb) vb = Object.keys(vb).length;
    if (va == null) va = ""; if (vb == null) vb = "";
    return va > vb ? m : va < vb ? -m : 0;
  });
}
function countSels(rows, key) {
  const set = new Set();
  rows.forEach((r) => (key === "venues" ? r.venues || [] : [r[key]])
    .forEach((v) => v && set.add(v)));
  return [...set].sort();
}

const fmtMS = (ms) => (ms ? new Date(ms).toLocaleString() : "—");

/* ---------- instruments ---------- */
/* Column model: k = sort key, label = header text, def = visible by default,
   w = default width (px) before the user saves one, html = cell renderer,
   tip = optional tooltip text, sk = sort key value. Visibility and widths
   persist in localStorage. */
const joinKV = (o) => o && typeof o === "object"
  ? Object.entries(o).map(([k, v]) => `${k}=${v}`).join(", ") : "";
const INST_COLS = [
  { k: "symbol",      label: "symbol",      def: true,  w: 110, html: (r) => esc(r.symbol), sk: (r) => r.symbol || "" },
  { k: "id",          label: "id",          def: true,  w: 210, html: (r) => `<span class="mono">${esc(r.id)}</span>`, sk: (r) => r.id || "" },
  { k: "class",       label: "class",       def: true,  w: 110, html: (r) => `<span class="pill ${esc(r.class)}">${esc(r.class)}</span>` },
  { k: "name",        label: "name",        def: false, w: 160, html: (r) => esc(r.name || "") },
  { k: "status",      label: "status",      def: true,  w: 100, html: (r) => `<span class="pill ${esc(r.status)}">${esc(r.status)}</span>` },
  { k: "venues",      label: "venues",      def: true,  w: 130, html: (r) => esc((r.venues || []).join(", ")), sk: (r) => (r.venues || []).join(",") },
  { k: "identifiers", label: "identifiers", def: false, w: 200, html: (r) => esc(joinKV(r.identifiers)), tip: (r) => joinKV(r.identifiers), sk: (r) => joinKV(r.identifiers) },
  { k: "nidents",     label: "#ids",        def: true,  w: 56,  html: (r) => String(r.identifiers ? Object.keys(r.identifiers).length : 0), sk: (r) => r.identifiers ? Object.keys(r.identifiers).length : 0 },
  { k: "limits",      label: "limits",      def: false, w: 160, html: (r) => esc(joinKV(r.limits)), tip: (r) => joinKV(r.limits), sk: (r) => joinKV(r.limits) },
  { k: "meta",        label: "meta",        def: false, w: 160, html: (r) => esc(joinKV(r.meta)), tip: (r) => joinKV(r.meta), sk: (r) => joinKV(r.meta) },
  { k: "created_ms",  label: "created",     def: false, w: 150, html: (r) => fmtMS(r.created_ms), sk: (r) => r.created_ms || 0 },
  { k: "updated_ms",  label: "updated",     def: true,  w: 150, html: (r) => fmtMS(r.updated_ms), sk: (r) => r.updated_ms || 0 },
];

function instCols() {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem("refdata:inst:cols") || "null"); } catch {}
  if (Array.isArray(saved) && saved.length)
    return INST_COLS.filter((c) => saved.includes(c.k));
  return INST_COLS.filter((c) => c.def);
}
function instWidths() {
  try { return JSON.parse(localStorage.getItem("refdata:inst:widths") || "{}") || {}; }
  catch { return {}; }
}

const inst = { rows: [], sort: { key: "symbol", dir: "asc" }, sel: null };

async function loadInstruments() {
  inst.rows = await api("/v1/instruments");
  for (const id of ["f-class", "f-status", "f-venue"]) $(`#${id}`).length = 1;
  countSels(inst.rows, "class").forEach((v) => $("#f-class").insertAdjacentHTML("beforeend", `<option>${esc(v)}</option>`));
  countSels(inst.rows, "status").forEach((v) => $("#f-status").insertAdjacentHTML("beforeend", `<option>${esc(v)}</option>`));
  countSels(inst.rows, "venues").forEach((v) => $("#f-venue").insertAdjacentHTML("beforeend", `<option>${esc(v)}</option>`));
  renderInstHead();
  renderInstruments();
}

function filteredInstruments() {
  const q = $("#f-q").value.trim().toLowerCase();
  const cls = $("#f-class").value, st = $("#f-status").value, vn = $("#f-venue").value;
  return inst.rows.filter((r) =>
    (!q || (r.id + " " + r.symbol + " " + (r.name || "")).toLowerCase().includes(q)) &&
    (!cls || r.class === cls) &&
    (!st || r.status === st) &&
    (!vn || (r.venues || []).includes(vn)));
}

function renderInstHead() {
  const cols = instCols();
  const widths = instWidths();
  const { key, dir } = inst.sort;
  $("#inst-thead").innerHTML = `<tr>${cols.map((c) => {
    const w = widths[c.k] || c.w;
    const srt = c.k === key ? ` class="sorted ${dir}"` : "";
    return `<th data-k="${c.k}"${w ? ` style="width:${w}px"` : ""}${srt}>` +
      `${c.label}<span class="rz" title="drag to resize"></span></th>`;
  }).join("")}</tr>`;
  wireResize();
}

/* drag the right edge of a header to resize its column; width persists. */
function wireResize() {
  $$("#inst-thead .rz").forEach((rz) => {
    rz.addEventListener("pointerdown", (e) => {
      e.preventDefault(); e.stopPropagation();
      const th = rz.closest("th");
      const startX = e.clientX, startW = th.getBoundingClientRect().width;
      rz.classList.add("on");
      rz.setPointerCapture(e.pointerId);
      let moved = false;
      const move = (ev) => {
        const w = Math.max(48, Math.min(640, Math.round(startW + ev.clientX - startX)));
        if (w !== Math.round(startW)) moved = true;
        th.style.width = w + "px";
      };
      const up = (ev) => {
        rz.removeEventListener("pointermove", move);
        rz.removeEventListener("pointerup", up);
        rz.classList.remove("on");
        const w = Math.max(48, Math.min(640, Math.round(startW + ev.clientX - startX)));
        const widths = instWidths();
        widths[th.dataset.k] = w;
        localStorage.setItem("refdata:inst:widths", JSON.stringify(widths));
        if (moved) { suppressSortClick = true; setTimeout(() => (suppressSortClick = false), 0); }
      };
      rz.addEventListener("pointermove", move);
      rz.addEventListener("pointerup", up);
    });
  });
}

/* column chooser: every attribute toggleable, >=1 column must stay visible. */
function renderColMenu() {
  const visible = new Set(instCols().map((c) => c.k));
  $("#col-menu").innerHTML = INST_COLS.map((c) =>
    `<label class="colitem"><input type="checkbox" data-col="${c.k}"${visible.has(c.k) ? " checked" : ""}> ${c.label}</label>`).join("");
  $$("#col-menu input").forEach((cb) => cb.addEventListener("change", () => {
    if (!$("#col-menu input:checked")) { cb.checked = true; return; }
    localStorage.setItem("refdata:inst:cols",
      JSON.stringify($$("#col-menu input:checked").map((x) => x.dataset.col)));
    renderInstHead();
    renderInstruments();
  }));
}
$("#col-chooser").addEventListener("click", (e) => {
  e.stopPropagation();
  const menu = $("#col-menu");
  if (menu.hidden) { renderColMenu(); menu.hidden = false; }
  else menu.hidden = true;
});
document.addEventListener("click", (e) => {
  const menu = $("#col-menu");
  if (!menu.hidden && !e.target.closest(".colchooser-wrap")) menu.hidden = true;
});

function renderInstruments(rows = sorted(filteredInstruments(), inst.sort.key, inst.sort.dir)) {
  $("#inst-count").textContent = `${rows.length} of ${inst.rows.length} instruments`;
  const cols = instCols();
  $("#inst-table tbody").innerHTML = rows.map((r) => `
    <tr data-id="${esc(r.id)}" class="${inst.sel === r.id ? "sel" : ""}">
      ${cols.map((c) => {
        const tip = c.tip ? ` title="${esc(c.tip(r))}"` : "";
        return `<td${tip}>${c.html(r)}</td>`;
      }).join("")}
    </tr>`).join("");
}

wireSort("#inst-thead", "#inst-table tbody", filteredInstruments, renderInstruments, inst.sort);
["#f-q", "#f-class", "#f-status", "#f-venue"].forEach((s) =>
  $(s).addEventListener("input", () => renderInstruments()));

$("#inst-table tbody").addEventListener("click", (e) => {
  const tr = e.target.closest("tr");
  if (tr) selectInstrument(tr.dataset.id);
});

async function selectInstrument(id) {
  inst.sel = id;
  renderInstruments();
  openDetail("inst");
  const box = $("#inst-detail-body");
  box.innerHTML = `<p class="placeholder">loading…</p>`;
  const [r, docs, edges] = await Promise.all([
    api(`/v1/instruments/${encodeURIComponent(id)}`),
    api(`/v1/instruments/${encodeURIComponent(id)}/documents`),
    api(`/v1/instruments/${encodeURIComponent(id)}/correlations`),
  ]);
  const identChips = Object.entries(r.identifiers || {})
    .map(([t, v]) => `<span class="pill same_as click" data-resolve="${esc(t)}:${esc(v)}" title="resolve this identifier">${esc(t)}: ${esc(v)}</span>`).join(" ");
  const kv = (obj) => obj && Object.keys(obj).length
    ? `<dl class="kv">${Object.entries(obj).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>`
    : `<p class="empty">none recorded</p>`;
  box.innerHTML = `
    <h2>${esc(r.id)}</h2>
    <div>${esc(r.name || "")}</div>
    <h3>identifiers — click to resolve transitively</h3>
    <div>${identChips || '<p class="empty">none</p>'}</div>
    <div id="resolve-out"></div>
    <h3>limits</h3>${kv(r.limits)}
    <h3>meta</h3>${kv(r.meta)}
    <h3>documents (${docs.length})</h3>
    ${docs.map((d) => `<div class="click" data-doc="${esc(d.id)}">📄 ${esc(d.title)} <span class="pill">${esc(d.kind)}</span></div>`).join("") || '<p class="empty">none attached</p>'}
    <h3>correlation neighborhood (${edges.length})</h3>
    <svg id="graph" viewBox="0 0 340 300" preserveAspectRatio="xMidYMid meet"></svg>
    <div id="graph-note" class="hint"></div>`;
  drawGraph($("#graph"), r.id, edges);
  box.querySelectorAll("[data-doc]").forEach((el) =>
    el.addEventListener("click", () => { showTab("documents"); openDocument(el.dataset.doc); }));
  box.querySelectorAll("[data-resolve]").forEach((el) =>
    el.addEventListener("click", async () => {
      const [t, v] = el.dataset.resolve.split(/:(.*)/s);
      const out = $("#resolve-out");
      out.innerHTML = `<p class="hint">resolving ${esc(t)}:${esc(v)} …</p>`;
      try {
        const hits = await api(`/v1/resolve?type=${encodeURIComponent(t)}&value=${encodeURIComponent(v)}`);
        out.innerHTML = hits.length
          ? hits.map((h) => `<div>→ <span class="click" data-goto="${esc(h.id)}">${esc(h.id)}</span></div>`).join("")
          : `<p class="empty">no resolution</p>`;
        out.querySelectorAll("[data-goto]").forEach((g) =>
          g.addEventListener("click", () => selectInstrument(g.dataset.goto)));
      } catch (err) { out.innerHTML = `<p class="empty">${esc(err.message)}</p>`; }
    }));
}

/* radial correlation graph: instrument centre, neighbours on rings by predicate */
function drawGraph(svg, centerId, edges) {
  const W = 340, H = 300, cx = W / 2, cy = H / 2;
  const nodes = edges.map((e) => {
    const other = e.subject === centerId || e.subject === "id:" + centerId ? e.object : e.subject;
    return { id: other, pred: e.predicate };
  });
  const byKey = new Map();
  nodes.forEach((n) => { if (!byKey.has(n.id)) byKey.set(n.id, n); });
  const uniq = [...byKey.values()];
  const rings = [[], [], []];
  uniq.forEach((n, i) => {
    const ring = n.pred === "same_as" ? 0 : n.pred === "listed_on" || n.pred === "has_document" ? 1 : 2;
    rings[ring].push(n);
  });
  const radii = [70, 115, 140];
  const pos = new Map([[centerId, [cx, cy]]]);
  let parts = "";
  rings.forEach((ring, ri) => {
    ring.forEach((n, i) => {
      const a = (2 * Math.PI * i) / Math.max(ring.length, 1) + ri * 0.7;
      const x = cx + radii[ri] * Math.cos(a), y = cy + radii[ri] * Math.sin(a);
      pos.set(n.id, [x, y]);
      parts += `<line class="edge" x1="${cx}" y1="${cy}" x2="${x}" y2="${y}" stroke="${PRED_COLOR[n.pred] || "#8b95a8"}"/>`;
    });
  });
  uniq.forEach((n) => {
    const [x, y] = pos.get(n.id);
    const short = n.id.length > 26 ? n.id.slice(0, 24) + "…" : n.id;
    parts += `<g class="node" data-node="${esc(n.id)}"><title>${esc(n.id)} (${esc(n.pred)})</title>
      <circle cx="${x}" cy="${y}" r="4" fill="${PRED_COLOR[n.pred] || "#8b95a8"}"/>
      <text x="${x + 7}" y="${y + 3}">${esc(short)}</text></g>`;
  });
  parts += `<g class="node" data-node="${esc(centerId)}"><circle cx="${cx}" cy="${cy}" r="6" fill="#e6eef8"/>
    <text x="${cx + 9}" y="${cy + 3}">${esc(centerId.split(":").pop())}</text></g>`;
  svg.innerHTML = parts;
  $("#graph-note").textContent =
    `rings: 1 identifiers · 2 venues/documents · 3 other links — ${uniq.length} neighbours`;
}

/* ---------- documents ---------- */
const docs = { rows: [], sort: { key: "title", dir: "asc" } };

async function loadDocuments() {
  docs.rows = await api("/v1/documents");
  renderDocuments();
}
function filteredDocs() {
  const q = $("#d-q").value.trim().toLowerCase();
  const kind = $("#d-kind").value;
  return docs.rows.filter((r) =>
    (!q || (r.id + " " + r.title).toLowerCase().includes(q)) && (!kind || r.kind === kind));
}
function renderDocuments(rows = sorted(filteredDocs(), docs.sort.key, docs.sort.dir)) {
  $("#doc-count").textContent = `${rows.length} of ${docs.rows.length} documents`;
  $("#doc-table tbody").innerHTML = rows.map((r) => `
    <tr data-id="${esc(r.id)}">
      <td>${esc(r.title)}</td>
      <td><span class="pill ${esc(r.kind)}">${esc(r.kind)}</span></td>
      <td class="id">${esc(r.instrument_id || "— class level —")}</td>
      <td>${fmtMS(r.updated_ms)}</td>
    </tr>`).join("");
}
wireSort("#doc-table thead", "#doc-table tbody", filteredDocs, renderDocuments, docs.sort);
["#d-q", "#d-kind"].forEach((s) => $(s).addEventListener("input", () => renderDocuments()));
$("#doc-table tbody").addEventListener("click", (e) => {
  const tr = e.target.closest("tr");
  if (tr) openDocument(tr.dataset.id);
});

async function openDocument(id) {
  openDetail("doc");
  const d = await api(`/v1/documents/${encodeURIComponent(id)}`);
  $("#doc-detail-body").innerHTML = `
    <h2>${esc(d.title)}</h2>
    <div><span class="pill ${esc(d.kind)}">${esc(d.kind)}</span>
      ${d.class ? `<span class="pill ${esc(d.class)}">${esc(d.class)}</span>` : ""}
      ${d.instrument_id ? `<span class="pill">${esc(d.instrument_id)}</span>` : ""}</div>
    <h3>provenance</h3>
    <dl class="kv">
      <dt>id</dt><dd class="mono">${esc(d.id)}</dd>
      <dt>sha256</dt><dd class="mono">${esc(d.sha256)}</dd>
      <dt>source</dt><dd>${esc(d.source || "—")}</dd>
      <dt>updated</dt><dd>${fmtMS(d.updated_ms)}</dd>
    </dl>
    <h3>body</h3>
    <div class="docbody">${md(d.body)}</div>`;
}

/* minimal markdown: headings, fenced code, inline code, bold, lists, paragraphs */
function md(src) {
  const lines = String(src).split("\n");
  let html = "", inCode = false, inList = false, para = [];
  const flushPara = () => {
    if (para.length) { html += `<p>${inline(para.join(" "))}</p>`; para = []; }
  };
  const inline = (s) => esc(s)
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/`([^`]+)`/g, '<code>$1</code>');
  for (const line of lines) {
    if (line.startsWith("```")) {
      if (inCode) { html += "</code></pre>"; inCode = false; }
      else { flushPara(); if (inList) { html += "</ul>"; inList = false; } html += "<pre><code>"; inCode = true; }
      continue;
    }
    if (inCode) { html += esc(line) + "\n"; continue; }
    const h = line.match(/^(#{1,4})\s+(.*)/);
    if (h) { flushPara(); if (inList) { html += "</ul>"; inList = false; } const lvl = Math.min(h[1].length + 1, 4); html += `<h${lvl}>${inline(h[2])}</h${lvl}>`; continue; }
    const li = line.match(/^\s*[-*]\s+(.*)/);
    if (li) { flushPara(); if (!inList) { html += "<ul>"; inList = true; } html += `<li>${inline(li[1])}</li>`; continue; }
    if (inList) { html += "</ul>"; inList = false; }
    if (line.trim() === "") flushPara();
    else para.push(line.trim());
  }
  flushPara();
  if (inList) html += "</ul>";
  if (inCode) html += "</code></pre>";
  return html;
}

/* ---------- edges ---------- */
const edges = { rows: [], sort: { key: "subject", dir: "asc" } };

async function loadEdges() {
  edges.rows = await api("/v1/edges");
  renderEdges();
}
function filteredEdges() {
  const q = $("#e-q").value.trim().toLowerCase();
  const pred = $("#e-pred").value;
  return edges.rows.filter((r) =>
    (!q || (r.subject + " " + r.object).toLowerCase().includes(q)) && (!pred || r.predicate === pred));
}
function renderEdges(rows = sorted(filteredEdges(), edges.sort.key, edges.sort.dir)) {
  $("#edge-count").textContent = `${rows.length} of ${edges.rows.length} edges`;
  $("#edge-table tbody").innerHTML = rows.map((r) => `
    <tr><td class="id">${esc(r.subject)}</td>
      <td><span class="pill ${esc(r.predicate)}">${esc(r.predicate)}</span></td>
      <td class="id">${esc(r.object)}</td>
      <td>${fmtMS(r.created_ms)}</td></tr>`).join("");
}
wireSort("#edge-table thead", "#edge-table tbody", filteredEdges, renderEdges, edges.sort);
["#e-q", "#e-pred"].forEach((s) => $(s).addEventListener("input", () => renderEdges()));

/* ---------- registry-wide search ---------- */
let searchTimer = null;
$("#s-q").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(runSearch, 250);
});

async function runSearch() {
  const q = $("#s-q").value.trim();
  const box = $("#search-results");
  if (!q) { box.innerHTML = ""; return; }
  box.innerHTML = `<p class="empty">searching…</p>`;
  try {
    const r = await api(`/v1/search?q=${encodeURIComponent(q)}`);
    const group = (title, rows, render) => rows.length
      ? `<div class="sgroup"><h3>${title} (${rows.length})</h3>${rows.map(render).join("")}</div>` : "";
    box.innerHTML =
      group("instruments", r.instruments, (x) => `
        <div class="row" data-kind="inst" data-id="${esc(x.id)}">
          <span class="mono">${esc(x.id)}</span><span>${esc(x.name || "")}</span>
          <span class="pill ${esc(x.class)}">${esc(x.class)}</span></div>`) +
      group("documents", r.documents, (x) => `
        <div class="row" data-kind="doc" data-id="${esc(x.id)}">
          <span class="mono">${esc(x.title)}</span><span class="pill">${esc(x.kind)}</span></div>`) +
      group("edges", r.edges, (x) => `
        <div class="row" data-kind="edge">
          <span class="mono">${esc(x.subject)} —${esc(x.predicate)}→ ${esc(x.object)}</span></div>`) ||
      `<p class="empty">no results for “${esc(q)}”</p>`;
    box.querySelectorAll('.row[data-kind="inst"]').forEach((el) =>
      el.addEventListener("click", () => { showTab("instruments"); selectInstrument(el.dataset.id); }));
    box.querySelectorAll('.row[data-kind="doc"]').forEach((el) =>
      el.addEventListener("click", () => { showTab("documents"); openDocument(el.dataset.id); }));
  } catch (err) {
    box.innerHTML = `<p class="empty">${esc(err.message)}</p>`;
  }
}

/* ---------- boot ---------- */
wireSplit("inst", "#inst-split", "#inst-vsplit", "#inst-detail");
wireSplit("doc", "#doc-split", "#doc-vsplit", "#doc-detail");
onNarrow();
loadInstruments().catch((e) => ($("#inst-count").textContent = e.message));
loadDocuments().catch(() => {});
loadEdges().catch(() => {});

/* Deep links: ?resolve=<anything> tries a registry resolution first and
   selects the hit; otherwise it lands in the filter box. This is the one-hop
   target for external tools (e.g. the envelope explorer symbol links). */
(async () => {
  const target = new URLSearchParams(location.search).get("resolve");
  if (!target) return;
  $("#f-q").value = target;
  renderInstruments();
  try {
    const hits = await api(`/v1/resolve?value=${encodeURIComponent(target)}`);
    if (hits.length) selectInstrument(hits[0].id);
    else {
      const r = await api(`/v1/search?q=${encodeURIComponent(target)}`);
      if (r.instruments && r.instruments.length) selectInstrument(r.instruments[0].id);
    }
  } catch { /* leave the filter populated */ }
})();
