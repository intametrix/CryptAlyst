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
function wireSort(theadSel, tbodySel, rows, render, state) {
  const thead = $(theadSel);
  thead.addEventListener("click", (e) => {
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
  return [...rows].sort((a, b) => {
    let va = a[k], vb = b[k];
    if (Array.isArray(va)) va = va.join(",");
    if (typeof va === "object" && va) va = Object.keys(va).length;
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
const inst = { rows: [], sort: { key: "id", dir: "asc" }, sel: null };

async function loadInstruments() {
  inst.rows = await api("/v1/instruments");
  for (const id of ["f-class", "f-status", "f-venue"]) $(`#${id}`).length = 1;
  countSels(inst.rows, "class").forEach((v) => $("#f-class").insertAdjacentHTML("beforeend", `<option>${esc(v)}</option>`));
  countSels(inst.rows, "status").forEach((v) => $("#f-status").insertAdjacentHTML("beforeend", `<option>${esc(v)}</option>`));
  countSels(inst.rows, "venues").forEach((v) => $("#f-venue").insertAdjacentHTML("beforeend", `<option>${esc(v)}</option>`));
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

function renderInstruments(rows = sorted(filteredInstruments(), inst.sort.key, inst.sort.dir)) {
  $("#inst-count").textContent = `${rows.length} of ${inst.rows.length} instruments`;
  $("#inst-table tbody").innerHTML = rows.map((r) => `
    <tr data-id="${esc(r.id)}" class="${inst.sel === r.id ? "sel" : ""}">
      <td class="id">${esc(r.id)}</td>
      <td><span class="pill ${esc(r.class)}">${esc(r.class)}</span></td>
      <td>${esc(r.symbol)}</td>
      <td><span class="pill ${esc(r.status)}">${esc(r.status)}</span></td>
      <td>${esc((r.venues || []).join(", "))}</td>
      <td>${r.identifiers ? Object.keys(r.identifiers).length : 0}</td>
      <td>${fmtMS(r.updated_ms)}</td>
    </tr>`).join("");
}

wireSort("#inst-table thead", "#inst-table tbody", filteredInstruments, renderInstruments, inst.sort);
["#f-q", "#f-class", "#f-status", "#f-venue"].forEach((s) =>
  $(s).addEventListener("input", () => renderInstruments()));

$("#inst-table tbody").addEventListener("click", (e) => {
  const tr = e.target.closest("tr");
  if (tr) selectInstrument(tr.dataset.id);
});

async function selectInstrument(id) {
  inst.sel = id;
  renderInstruments();
  const box = $("#inst-detail");
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
  const d = await api(`/v1/documents/${encodeURIComponent(id)}`);
  $("#doc-detail").innerHTML = `
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
loadInstruments().catch((e) => ($("#inst-count").textContent = e.message));
loadDocuments().catch(() => {});
loadEdges().catch(() => {});
