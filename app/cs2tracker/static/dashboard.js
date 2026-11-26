// Dashboard tab (v1.3.3): the app owner's stats over their Competitive / Premier matches.
// Ported from a friend's stand-alone page; the numbers and sections are his. Data comes from
// GET /api/dashboard (only counted matches). The page is built as HTML strings: every value
// from the data goes through esc().

import { api } from "/static/app.js";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 2) => v == null || !isFinite(v) ? "–" : v.toLocaleString("en-GB", {minimumFractionDigits: d, maximumFractionDigits: d});
const pct = (v, d = 0) => v == null || !isFinite(v) ? "–" : fmt(v * 100, d) + " %";
const hm = s => { if (s == null) return "–"; const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60); return h ? `${h} h ${m} min` : `${m} min ${Math.round(s % 60)} s`; };
const dt = t => new Date(t * 1000);
const dstr = t => dt(t).toLocaleDateString("en-GB", {day: "numeric", month: "short", year: "numeric"});
const dtstr = t => dt(t).toLocaleString("en-GB", {day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit"});
const sum = a => a.reduce((x, y) => x + (y || 0), 0);
const avg = a => { const b = a.filter(x => x != null && isFinite(x)); return b.length ? sum(b) / b.length : null; };

/* ---------- data ---------- */
let ALL = [], ME = null, NAMES = new Map(), loaded = false;

async function load() {
  const data = await api("GET", "/api/dashboard");
  ALL = data.matches;
  ME = data.me;
  NAMES = new Map();
  for (const m of ALL) for (const t of m.T) for (const p of t) if (p[0]) NAMES.set(p[0], p[1]);
  loaded = true;
}

function rows(ms) {
  const out = [];
  for (const m of ms) {
    const side = m.T.findIndex(t => t.some(p => p[0] === ME));
    if (side < 0) continue;
    const team = m.T[side], foes = m.T[1 - side];
    const me = team.find(p => p[0] === ME);
    const [, , , k, a, d, mvp, hs, score] = me;
    const us = m.s[side], them = m.s[1 - side], r = us + them;
    const byScore = [...team].sort((x, y) => (y[8] ?? 0) - (x[8] ?? 0) || (y[3] ?? 0) - (x[3] ?? 0));
    const rank = byScore.findIndex(p => p[0] === ME) + 1;
    const allByK = [...team, ...foes].sort((x, y) => (y[3] ?? 0) - (x[3] ?? 0));
    out.push({
      m, id: m.id, t: m.t, map: m.mp, mode: m.mo, wait: m.w, dur: m.du, k, a, d, mvp, hs, score,
      us, them, r, res: us > them ? "W" : us < them ? "L" : "T",
      rank, teamSize: team.length, lobbyRankK: allByK.findIndex(p => p[0] === ME) + 1,
      hsk: hs != null && k != null ? k * hs / 100 : null,
      team, foes, ot: r > 24,
      teamK: sum(team.map(p => p[3])), foeK: sum(foes.map(p => p[3])),
    });
  }
  return out;
}

function agg(R) {
  const n = R.length;
  if (!n) return null;
  const K = sum(R.map(x => x.k)), D = sum(R.map(x => x.d)), A = sum(R.map(x => x.a)), RD = sum(R.map(x => x.r));
  const W = R.filter(x => x.res === "W").length, L = R.filter(x => x.res === "L").length;
  return {
    n, W, L, T: n - W - L, win: W / n, K, D, A, RD,
    kd: D ? K / D : null, kpr: RD ? K / RD : null, dpr: RD ? D / RD : null, apr: RD ? A / RD : null,
    hs: sum(R.map(x => x.hsk)) / (K || 1), mvp: sum(R.map(x => x.mvp)) / n, mvpr: RD ? sum(R.map(x => x.mvp)) / RD : null,
    spr: RD ? sum(R.map(x => x.score)) / RD : null, kpm: avg(R.map(x => x.k)),
    top: R.filter(x => x.rank === 1).length / n, bottom: R.filter(x => x.rank === x.teamSize).length / n,
    rank: avg(R.map(x => x.rank)),
    rdiff: sum(R.map(x => x.us - x.them)) / n, share: sum(R.map(x => x.k)) / (sum(R.map(x => x.teamK)) || 1),
    dur: sum(R.map(x => x.dur)), wait: sum(R.map(x => x.wait)),
  };
}

/* ---------- filters ---------- */
function fillFilters() {
  const modes = [...new Set(ALL.map(m => m.mo))];
  const ms = $("#dash-f-mode"), cur = ms.value;
  const opts = [["*", "All counted modes"]];
  for (const m of ["Premier", "Competitive", ...modes.filter(m => m !== "Premier" && m !== "Competitive").sort()]) if (modes.includes(m)) opts.push([m, m]);
  ms.innerHTML = opts.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
  ms.value = opts.some(o => o[0] === cur) ? cur : "*";
  fillMaps();
}
function fillMaps() {
  const sel = $("#dash-f-map"), cur = sel.value;
  const cnt = {};
  for (const m of byMode(ALL)) cnt[m.mp] = (cnt[m.mp] || 0) + 1;
  const maps = Object.entries(cnt).sort((a, b) => b[1] - a[1]);
  sel.innerHTML = `<option value="*">All maps</option>` + maps.map(([m, n]) => `<option value="${esc(m)}">${esc(m)} (${n})</option>`).join("");
  sel.value = maps.some(x => x[0] === cur) ? cur : "*";
}
function byMode(ms) {
  const v = $("#dash-f-mode").value;
  return ms.filter(m => v === "*" || m.mo === v);
}
function filtered() {
  let ms = byMode(ALL);
  const mp = $("#dash-f-map").value; if (mp && mp !== "*") ms = ms.filter(m => m.mp === mp);
  const p = $("#dash-f-period").value, now = Date.now() / 1000;
  if (p.startsWith("n")) ms = ms.slice(-parseInt(p.slice(1)));
  else if (p !== "all") ms = ms.filter(m => m.t >= now - parseInt(p) * 86400);
  return ms;
}

/* ---------- tooltip ---------- */
let tip = null;
function showTip(e, html) { tip.innerHTML = html; tip.style.display = "block"; const x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8); const y = e.clientY + 14 + tip.offsetHeight > innerHeight ? e.clientY - tip.offsetHeight - 10 : e.clientY + 14; tip.style.left = x + "px"; tip.style.top = y + "px"; }
function hideTip() { tip.style.display = "none"; }

/* ---------- charts (plain SVG) ---------- */
function niceTicks(lo, hi, n = 4) {
  if (lo === hi) { lo -= 1; hi += 1; }
  const step0 = (hi - lo) / n, mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map(s => s * mag).find(s => s >= step0);
  const a = Math.floor(lo / step) * step, b = Math.ceil(hi / step) * step, t = [];
  for (let v = a; v <= b + step / 2; v += step) t.push(+v.toFixed(10));
  return t;
}
/* series: [{name,color,pts:[{x,y,tip}]}] ; x is index */
function lineChart(el, series, o = {}) {
  const W = el.clientWidth || 600, H = o.h || 220, ml = 44, mr = 12, mt = 10, mb = 26;
  const all = series.flatMap(s => s.pts.map(p => p.y)).filter(v => v != null);
  if (!all.length) { el.innerHTML = `<div class="empty">Not enough data</div>`; return; }
  let lo = Math.min(...all, o.ref ?? Infinity), hi = Math.max(...all, o.ref ?? -Infinity);
  if (o.min != null) lo = Math.min(lo, o.min); if (o.max != null) hi = Math.max(hi, o.max);
  const ticks = niceTicks(lo, hi); lo = ticks[0]; hi = ticks[ticks.length - 1];
  const n = Math.max(...series.map(s => s.pts.length));
  const X = i => ml + (n <= 1 ? 0 : i / (n - 1)) * (W - ml - mr), Y = v => mt + (1 - (v - lo) / (hi - lo)) * (H - mt - mb);
  let s = `<svg viewBox="0 0 ${W} ${H}" height="${H}">`;
  for (const t of ticks) s += `<line x1="${ml}" x2="${W - mr}" y1="${Y(t)}" y2="${Y(t)}" stroke="var(--grid)"/><text x="${ml - 6}" y="${Y(t) + 4}" fill="var(--ink2)" font-size="11" text-anchor="end">${o.yfmt ? o.yfmt(t) : t}</text>`;
  if (o.ref != null) s += `<line x1="${ml}" x2="${W - mr}" y1="${Y(o.ref)}" y2="${Y(o.ref)}" stroke="var(--axis)" stroke-dasharray="4 4"/>`;
  // x labels: first, middle, last
  const lbl = series[0].pts;
  for (const i of [0, Math.floor((n - 1) / 2), n - 1]) if (lbl[i]?.xl) s += `<text x="${X(i)}" y="${H - 6}" fill="var(--ink2)" font-size="11" text-anchor="${i === 0 ? "start" : i === n - 1 ? "end" : "middle"}">${esc(lbl[i].xl)}</text>`;
  for (const se of series) {
    let d = "", pen = false;
    se.pts.forEach((p, i) => { if (p.y == null) { pen = false; return; } d += (pen ? "L" : "M") + X(i).toFixed(1) + "," + Y(p.y).toFixed(1); pen = true; });
    s += `<path d="${d}" fill="none" stroke="${se.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    if (o.dots) se.pts.forEach((p, i) => { if (p.y != null) s += `<circle cx="${X(i)}" cy="${Y(p.y)}" r="4" fill="${se.color}" stroke="var(--surface)" stroke-width="2"/>`; });
  }
  s += `<line class="xh" x1="0" x2="0" y1="${mt}" y2="${H - mb}" stroke="#777" visibility="hidden"/>`;
  s += `<rect x="${ml}" y="0" width="${W - ml - mr}" height="${H}" fill="transparent"/></svg>`;
  el.innerHTML = s;
  const svg = el.querySelector("svg"), xh = svg.querySelector(".xh");
  svg.addEventListener("mousemove", e => {
    const r = svg.getBoundingClientRect(), px = (e.clientX - r.left) * W / r.width;
    const i = Math.max(0, Math.min(n - 1, Math.round((px - ml) / (W - ml - mr) * (n - 1))));
    xh.setAttribute("x1", X(i)); xh.setAttribute("x2", X(i)); xh.setAttribute("visibility", "visible");
    showTip(e, series.map(se => se.pts[i]?.tip ?? "").filter(Boolean).join("<br>"));
  });
  svg.addEventListener("mouseleave", () => { xh.setAttribute("visibility", "hidden"); hideTip(); });
}
/* bars: [{label,v,tip,color?}] vertical */
function barChart(el, bars, o = {}) {
  const W = el.clientWidth || 600, H = o.h || 200, ml = 40, mr = 8, mt = 10, mb = 26;
  if (!bars.length) { el.innerHTML = `<div class="empty">Not enough data</div>`; return; }
  const vals = bars.map(b => b.v ?? 0);
  const ticks = niceTicks(Math.min(0, ...vals), Math.max(...vals, o.max ?? 0)); const lo = ticks[0], hi = ticks[ticks.length - 1];
  const Y = v => mt + (1 - (v - lo) / (hi - lo)) * (H - mt - mb);
  const bw = (W - ml - mr) / bars.length, gap = Math.min(6, bw * 0.25);
  let s = `<svg viewBox="0 0 ${W} ${H}" height="${H}">`;
  for (const t of ticks) s += `<line x1="${ml}" x2="${W - mr}" y1="${Y(t)}" y2="${Y(t)}" stroke="var(--grid)"/><text x="${ml - 6}" y="${Y(t) + 4}" fill="var(--ink2)" font-size="11" text-anchor="end">${o.yfmt ? o.yfmt(t) : t}</text>`;
  if (o.ref != null) s += `<line x1="${ml}" x2="${W - mr}" y1="${Y(o.ref)}" y2="${Y(o.ref)}" stroke="var(--axis)" stroke-dasharray="4 4"/>`;
  const every = Math.ceil(bars.length / Math.max(1, Math.floor((W - ml) / 46)));
  bars.forEach((b, i) => {
    const x = ml + i * bw + gap / 2, w = Math.max(1, bw - gap), y0 = Y(Math.max(0, lo)), y1 = Y(b.v ?? 0);
    const top = Math.min(y0, y1), h = Math.max(1, Math.abs(y1 - y0)), rr = Math.min(4, w / 2, h);
    // rounded on the data end only
    const pos = (b.v ?? 0) >= 0;
    const d = pos ? `M${x},${top + h}V${top + rr}Q${x},${top} ${x + rr},${top}H${x + w - rr}Q${x + w},${top} ${x + w},${top + rr}V${top + h}Z`
                  : `M${x},${top}V${top + h - rr}Q${x},${top + h} ${x + rr},${top + h}H${x + w - rr}Q${x + w},${top + h} ${x + w},${top + h - rr}V${top}Z`;
    s += `<path d="${d}" fill="${b.color || "var(--s1)"}"/>`;
    s += `<rect class="hit" data-i="${i}" x="${ml + i * bw}" y="${mt}" width="${bw}" height="${H - mt - mb}" fill="transparent"/>`;
    if (i % every === 0) s += `<text x="${x + w / 2}" y="${H - 6}" fill="var(--ink2)" font-size="11" text-anchor="middle">${esc(b.label)}</text>`;
  });
  s += `</svg>`;
  el.innerHTML = s;
  el.querySelectorAll(".hit").forEach(r => { r.addEventListener("mousemove", e => showTip(e, bars[+r.dataset.i].tip)); r.addEventListener("mouseleave", hideTip); });
}

/* ---------- sortable tables ---------- */
function table(cols, data, o = {}) {
  const id = "t" + Math.random().toString(36).slice(2);
  setTimeout(() => {
    const el = document.getElementById(id); if (!el) return;
    let sortI = o.sort ?? 1, dir = -1;
    const draw = () => {
      const d = [...data].sort((a, b) => { const x = cols[sortI].v(a), y = cols[sortI].v(b); return (x == null) - (y == null) || (typeof x === "string" ? x.localeCompare(y) : (x - y)) * dir; });
      el.innerHTML = `<thead><tr>${cols.map((c, i) => `<th data-i="${i}" title="${esc(c.title || "")}">${esc(c.h)}${i === sortI ? (dir < 0 ? " ▾" : " ▴") : ""}</th>`).join("")}</tr></thead><tbody>` +
        d.map(r => `<tr>${cols.map(c => `<td class="${c.cls || ""}">${c.f ? c.f(r) : esc(c.v(r))}</td>`).join("")}</tr>`).join("") + `</tbody>`;
      el.querySelectorAll("th").forEach(th => th.onclick = () => { const i = +th.dataset.i; if (i === sortI) dir = -dir; else { sortI = i; dir = typeof cols[i].v(data[0] || {}) === "string" ? 1 : -1; } draw(); });
    };
    draw();
  });
  return `<div class="scroll"><table id="${id}"></table></div>`;
}

/* ---------- render ---------- */
function delta(cur, prev, better = 1, f = v => fmt(v)) {
  if (cur == null || prev == null || !isFinite(cur) || !isFinite(prev)) return `<span class="flat">–</span>`;
  const diff = cur - prev, eps = Math.abs(prev) * 0.02 + 1e-9;
  if (Math.abs(diff) < eps) return `<span class="flat">● unchanged (${f(prev)})</span>`;
  const good = diff * better > 0;
  return `<span class="${good ? "up" : "down"}">${diff > 0 ? "▲" : "▼"} ${good ? "better" : "worse"} (was ${f(prev)})</span>`;
}

function render() {
  hideTip();
  const ms = filtered(), R = rows(ms), main = $("#dash-main");
  const allR = rows(byMode(ALL));
  $("#dash-sub").textContent = ALL.length
    ? `${NAMES.get(ME) ?? "?"} · ${ALL.length} matches · ${dstr(ALL[0].t)} – ${dstr(ALL[ALL.length - 1].t)}`
    : "No matches yet";
  if (!R.length) { main.innerHTML = ALL.length ? `<div class="card c12 empty">No matches with these filters.</div>` : `<div class="card c12 empty">No Competitive or Premier matches yet. Import your Steam match history in <a href="#settings">Settings</a>, or add a demo on the <a href="#home">Home</a> page.</div>`; return; }
  const a = agg(R);
  const roll = Math.max(5, Math.min(100, parseInt($("#dash-f-roll").value) || 20));
  const recentN = Math.min(30, Math.floor(R.length / 3)) || 1;
  const recent = agg(R.slice(-recentN)), before = R.length > recentN ? agg(R.slice(-recentN - 100, -recentN)) : null;

  let h = "";
  /* KPI tiles */
  const tiles = [
    ["Matches", a.n, null, `${a.W} W · ${a.L} L · ${a.T} T`],
    ["Win %", pct(a.win, 1), ["win", 1, v => pct(v, 0)]],
    ["K/D", fmt(a.kd), ["kd", 1]],
    ["Kills per round", fmt(a.kpr), ["kpr", 1]],
    ["Deaths per round", fmt(a.dpr), ["dpr", -1]],
    ["Assists per round", fmt(a.apr), ["apr", 1]],
    ["HS %", pct(a.hs, 1), ["hs", 1, v => pct(v, 0)]],
    ["MVPs per match", fmt(a.mvp, 1), ["mvp", 1, v => fmt(v, 1)]],
    ["Score per round", fmt(a.spr), ["spr", 1]],
    ["Top of team (score)", pct(a.top), ["top", 1, v => pct(v)]],
    ["Avg place in team", fmt(a.rank, 1), ["rank", -1, v => fmt(v, 1)]],
    ["Share of team kills", pct(a.share, 1), ["share", 1, v => pct(v, 1)]],
    ["Round diff per match", (a.rdiff > 0 ? "+" : "") + fmt(a.rdiff, 1), ["rdiff", 1, v => fmt(v, 1)]],
  ];
  h += `<section class="card c12"><h2>Overview</h2><p class="sub">The arrows compare your last ${recentN} matches with the ${before ? before.n : 0} before them (within the filter).</p><div class="tiles">` +
    tiles.map(([l, v, d, extraTxt]) => `<div class="tile"><div class="lbl">${l}</div><div class="val">${v}</div><div class="d">${extraTxt ? `<span class="flat">${extraTxt}</span>` : d && before ? `<span class="flat">Last ${recentN}: ${(d[2] || fmt)(recent[d[0]])}</span><br>` + delta(recent[d[0]], before[d[0]], d[1], d[2]) : ""}</div></div>`).join("") + `</div></section>`;

  /* Improvement panel */
  const metrics = [["Win %", "win", 1, v => pct(v, 1)], ["K/D", "kd", 1], ["Kills/round", "kpr", 1], ["Deaths/round", "dpr", -1], ["Assists/round", "apr", 1],
    ["HS %", "hs", 1, v => pct(v, 1)], ["MVPs/match", "mvp", 1, v => fmt(v, 1)], ["Score/round", "spr", 1], ["Top of team %", "top", 1, v => pct(v)], ["Avg place in team", "rank", -1, v => fmt(v, 1)], ["Share of team kills", "share", 1, v => pct(v, 1)]];
  const windows = [];
  if (R.length >= 20) windows.push([`Last ${recentN} vs the ${before?.n} before`, recent, before]);
  const now = Date.now() / 1000;
  const r90 = R.filter(x => x.t >= now - 90 * 86400), p90 = R.filter(x => x.t < now - 90 * 86400 && x.t >= now - 455 * 86400);
  if (r90.length >= 5 && p90.length >= 5) windows.push([`Last 3 months vs the 12 months before`, agg(r90), agg(p90)]);
  const half = Math.floor(R.length / 2);
  if (R.length >= 20) windows.push([`Second half vs first half`, agg(R.slice(half)), agg(R.slice(0, half))]);
  const verdict = [];
  if (windows.length) {
    const [, cur, prev] = windows[0];
    for (const [l, k, b, f] of metrics) {
      const c = cur[k], p = prev?.[k]; if (c == null || p == null || !p) continue;
      const rel = (c - p) / Math.abs(p) * b; verdict.push([l, rel, f ? f(c) : fmt(c), f ? f(p) : fmt(p)]);
    }
    verdict.sort((x, y) => y[1] - x[1]);
  }
  h += `<section class="card c12"><h2>Better and worse</h2><p class="sub">Each column compares two periods within the filter. Blue is better, red is worse, whether the number went up or down.</p>`;
  if (verdict.length) {
    const best = verdict.filter(v => v[1] > 0.02).slice(0, 3), worst = verdict.filter(v => v[1] < -0.02).slice(-3).reverse();
    h += `<ul class="facts" style="margin-bottom:12px">` +
      best.map(v => `<li><b class="up">▲ ${esc(v[0])}</b><span>${v[2]} now, ${v[3]} before (${fmt(v[1] * 100, 0)} % better)</span></li>`).join("") +
      worst.map(v => `<li><b class="down">▼ ${esc(v[0])}</b><span>${v[2]} now, ${v[3]} before (${fmt(-v[1] * 100, 0)} % worse)</span></li>`).join("") + `</ul>`;
  }
  h += `<div class="scroll"><table class="cmp"><thead><tr><th>Metric</th>${windows.map(w => `<th>${esc(w[0])}</th>`).join("")}</tr></thead><tbody>` +
    metrics.map(([l, k, b, f]) => `<tr><td>${l}</td>${windows.map(([, c, p]) => `<td>${(f || fmt)(c?.[k])} &nbsp;${delta(c?.[k], p?.[k], b, f || fmt)}</td>`).join("")}</tr>`).join("") + `</tbody></table></div></section>`;

  /* Rolling charts */
  const rollPts = (fn, f) => R.map((x, i) => {
    if (i < roll - 1) return {y: null, xl: dstr(x.t)};
    const w = agg(R.slice(i - roll + 1, i + 1)); const y = fn(w);
    return {y, xl: dstr(x.t), tip: `<b>Match ${i + 1}</b> · ${dstr(x.t)}<br>Average of last ${roll}: ${f(y)}`};
  });
  h += `<section class="card c6"><h2>K/D over time</h2><p class="sub">Rolling average over ${roll} matches. Dashed line = 1.00.</p><div class="chart" id="ch-kd"></div></section>`;
  h += `<section class="card c6"><h2>Win rate over time</h2><p class="sub">Rolling average over ${roll} matches. Dashed line = 50 %.</p><div class="chart" id="ch-win"></div></section>`;
  h += `<section class="card c6"><h2>Kills per round over time</h2><p class="sub">Rolling average over ${roll} matches.</p><div class="chart" id="ch-kpr"></div></section>`;
  h += `<section class="card c6"><h2>HS % over time</h2><p class="sub">Rolling average over ${roll} matches, weighted by kills.</p><div class="chart" id="ch-hs"></div></section>`;

  const mline = x => x ? `${esc(x.map)} · ${dstr(x.t)} · ${x.us}–${x.them} <span class="dres-${x.res}">${x.res === "W" ? "win" : x.res === "L" ? "loss" : "tie"}</span>` : "";
  /* ---------- Demo stats (only matches the tracker has read a demo of) ---------- */
  const DR = R.filter(x => x.m.D);
  if (DR.length) {
    const g = (x, k) => x.m.D[k];
    const tot = k => sum(DR.map(x => +g(x, k) || 0));
    const rp = tot("rounds_played") || 1, nD = DR.length;
    const ok = tot("opening_kills"), od = tot("opening_deaths");
    const cA = sum(DR.map(x => sum(Object.values(g(x, "clutch_attempts") || {})))), cW = sum(DR.map(x => sum(Object.values(g(x, "clutch_wins") || {}))));
    const ctK = sum(DR.map(x => g(x, "kd_ct")?.k || 0)), ctD = sum(DR.map(x => g(x, "kd_ct")?.d || 0)), tK = sum(DR.map(x => g(x, "kd_t")?.k || 0)), tD = sum(DR.map(x => g(x, "kd_t")?.d || 0));
    const mk = n => sum(DR.map(x => (g(x, "multikills") || {})[n] || 0));
    const dTiles = [
      ["Demo matches", nD, `of ${R.length} in the filter`],
      ["ADR", fmt(tot("damage") / rp, 1), "damage per round"],
      ["Rating", fmt(avg(DR.map(x => g(x, "rating"))), 2), "average per match"],
      ["KAST", pct(tot("kast_rounds") / rp, 1), "rounds with a kill, assist, survival or trade"],
      ["Opening duels", `${ok}–${od}`, `${pct(ok / ((ok + od) || 1))} won`],
      ["Trade kills", fmt(tot("trade_kills") / nD, 1), "per match"],
      ["Deaths traded", pct(tot("traded_deaths") / (tot("deaths") || 1)), "of your deaths got avenged within 5 s"],
      ["Clutches", `${cW} / ${cA}`, `${pct(cW / (cA || 1))} won`],
      ["Survived rounds", pct(tot("rounds_survived") / rp), `avg ${fmt(avg(DR.map(x => g(x, "avg_time_alive_s"))), 0)} s alive per round`],
      ["Accuracy", pct(tot("hits") / (tot("shots") || 1), 1), `${tot("hits").toLocaleString("en-GB")} hits from ${tot("shots").toLocaleString("en-GB")} shots`],
      ["Utility damage", fmt(tot("utility_dmg") / nD, 0), "per match"],
      ["Flash assists", fmt(tot("flash_assists") / nD, 1), `per match · ${fmt(tot("enemies_blinded") / nD, 1)} enemies blinded`],
      ["K/D as CT", fmt(ctK / (ctD || 1)), `${ctK} kills, ${ctD} deaths`],
      ["K/D as T", fmt(tK / (tD || 1)), `${tK} kills, ${tD} deaths`],
      ["Multikills", `${mk("2")}/${mk("3")}/${mk("4")}/${mk("5")}`, "2k / 3k / 4k / aces"],
      ["Money spent", "$" + fmt(tot("money_spent") / rp, 0), "per round"],
    ];
    h += `<section class="card c12"><h2>Demo stats</h2><p class="sub">From the ${nD} matches in this filter that the tracker has read a demo of. Grows every time you play with the tracker running.</p><div class="tiles">` +
      dTiles.map(([l, v, s]) => `<div class="tile"><div class="lbl">${l}</div><div class="val">${v}</div><div class="d"><span class="flat">${s}</span></div></div>`).join("") + `</div></section>`;
    h += `<section class="card c6"><h2>ADR per demo match</h2><p class="sub">Each dot is one match. Dashed line = 80.</p><div class="chart" id="ch-adr"></div></section>`;
    h += `<section class="card c6"><h2>Rating per demo match</h2><p class="sub">Each dot is one match. Dashed line = 1.00.</p><div class="chart" id="ch-rating"></div></section>`;

    /* who kills whom */
    const vict = {}, kill = {}, aven = {};
    for (const x of DR) { for (const [k, v] of Object.entries(g(x, "victims") || {})) vict[k] = (vict[k] || 0) + v; for (const [k, v] of Object.entries(g(x, "killers") || {})) kill[k] = (kill[k] || 0) + v; for (const [k, v] of Object.entries(g(x, "avenged") || {})) aven[k] = (aven[k] || 0) + v; }
    const top = o => Object.entries(o).sort((a, b) => b[1] - a[1])[0];
    const nmOf = k => esc(NAMES.get(String(k)) || "?");
    const bestBy = (fn) => DR.reduce((b, x) => { const v = fn(x); return v != null && (b == null || v > b[0]) ? [v, x] : b; }, null);
    const longest = bestBy(x => g(x, "longest")?.any?.m), closest = DR.reduce((b, x) => { const v = g(x, "closest")?.m; return v != null && (b == null || v < b[0]) ? [v, x] : b; }, null);
    const awpL = bestBy(x => g(x, "longest")?.awp?.m), pisL = bestBy(x => g(x, "longest")?.pistol?.m);
    const streak = bestBy(x => g(x, "best_kill_streak")), fastOpen = DR.reduce((b, x) => { const v = g(x, "fastest_opening_s"); return v != null && (b == null || v < b[0]) ? [v, x] : b; }, null);
    const bigClutch = bestBy(x => { const w = g(x, "clutch_wins") || {}; const n = Math.max(0, ...Object.entries(w).filter(([, v]) => v > 0).map(([k]) => +k)); return n || null; });
    const thrown = {}; for (const x of DR) for (const [k, v] of Object.entries(g(x, "nades_thrown") || {})) thrown[k] = (thrown[k] || 0) + v;
    const wpn = w => ({awp: "AWP", ak47: "AK-47", m4a1_silencer: "M4A1-S", m4a1: "M4A4", usp_silencer: "USP-S", glock: "Glock", deagle: "Deagle", hkp2000: "P2000", p250: "P250"}[w] || w || "");
    const tv = top(vict), tk = top(kill), ta = top(aven);
    const dFacts = [
      tv ? [nmOf(tv[0]), `your favourite victim: you killed them ${tv[1]} times`] : null,
      tk ? [nmOf(tk[0]), `your nemesis: killed you ${tk[1]} times`] : null,
      ta ? [nmOf(ta[0]), `the teammate you avenged most: ${ta[1]} times`] : null,
      longest ? [`${fmt(longest[0], 1)} m`, `longest kill (${wpn(g(longest[1], "longest").any.weapon)}) · ${mline(longest[1])}`] : null,
      awpL ? [`${fmt(awpL[0], 1)} m`, `longest AWP kill · ${mline(awpL[1])}`] : null,
      pisL ? [`${fmt(pisL[0], 1)} m`, `longest pistol kill (${wpn(g(pisL[1], "longest").pistol.weapon)})`] : null,
      closest ? [`${fmt(closest[0], 1)} m`, `closest kill (${wpn(g(closest[1], "closest").weapon)}) · basically a hug`] : null,
      streak ? [`${streak[0]} kills`, `best kill streak without dying · ${mline(streak[1])}`] : null,
      fastOpen ? [`${fmt(fastOpen[0], 1)} s`, `fastest opening kill after freeze time ended`] : null,
      bigClutch ? [`1v${bigClutch[0]}`, `biggest clutch won · ${mline(bigClutch[1])}`] : [`0 clutches`, "won so far in demo matches"],
      [`${tot("wallbangs")} wallbangs`, `${tot("smoke_kills")} through smoke · ${tot("noscope_kills")} noscopes · ${tot("blind_kills")} while blind`],
      [`${tot("knife_kills")} knife · ${tot("zeus_kills")} Zeus`, `kills with the knife and the Zeus (bought ${tot("taser_buys")} Zeus)`],
      [`${fmt(tot("blind_seconds"), 0)} s`, "total time you kept enemies blind"],
      [`${tot("team_flashes")} team flashes`, `teammates blinded by you for 1 s or more (${fmt(tot("team_flashes") / nD, 1)} per match)`],
      [`${tot("team_dmg")} HP`, `damage dealt to your own team · ${tot("team_kills")} team kills`],
      [`$${tot("nades_held_value").toLocaleString("en-GB")}`, `worth of grenades you died holding (${tot("nades_held_at_death")} grenades)`],
      [`${thrown.flash || 0} · ${thrown.smoke || 0} · ${thrown.he || 0} · ${thrown.molotov || 0}`, "flashes · smokes · HEs · molotovs thrown"],
      [`${tot("plants")} plants · ${tot("defuses")} defuses`, `${tot("ninja_defuses")} ninja defuses`],
      [`${tot("reload_deaths")} times`, "died while reloading"],
      [`${tot("weapons_dropped_to_enemies")} weapons`, "dropped when you died and picked up by an enemy"],
      [`${tot("fall_dmg")} HP`, "fall damage taken"],
      [`${tot("eco_kills")} eco kills`, "kills with ≤ $2,000 of gear against a full buy"],
      [`${tot("pistol_round_kills")} kills`, "in pistol rounds"],
      [`$${tot("money_spent").toLocaleString("en-GB")}`, "spent in total in demo matches"],
    ].filter(Boolean);
    h += `<section class="card c12"><h2>Demo: useful and useless</h2><p class="sub">From the demo matches in the filter.</p><ul class="facts">` + dFacts.map(([b, s]) => `<li><b>${b}</b><span>${s}</span></li>`).join("") + `</ul></section>`;
    h += `<section class="card c12"><h2>Demo matches</h2><p class="sub">Click a column to sort.</p>${table([
      {h: "Date", v: r => r.t, f: r => `<a href="#matches/${r.id}">${dtstr(r.t)}</a>`}, {h: "Map", v: r => r.map}, {h: "Result", v: r => r.us - r.them, f: r => `<span class="dres-${r.res}">${r.us}–${r.them}</span>`},
      {h: "K-A-D", v: r => r.k, f: r => `${r.k}-${r.a}-${r.d}`}, {h: "ADR", v: r => g(r, "damage") / (g(r, "rounds_played") || 1), f: r => fmt(g(r, "damage") / (g(r, "rounds_played") || 1), 0)},
      {h: "KAST", v: r => g(r, "kast_rounds") / (g(r, "rounds_played") || 1), f: r => pct(g(r, "kast_rounds") / (g(r, "rounds_played") || 1))},
      {h: "Rating", v: r => g(r, "rating"), f: r => fmt(g(r, "rating"))}, {h: "Openings", v: r => g(r, "opening_kills") - g(r, "opening_deaths"), f: r => `${g(r, "opening_kills")}–${g(r, "opening_deaths")}`},
      {h: "Trades", v: r => g(r, "trade_kills")}, {h: "Clutch", v: r => sum(Object.values(g(r, "clutch_wins") || {})), f: r => `${sum(Object.values(g(r, "clutch_wins") || {}))}/${sum(Object.values(g(r, "clutch_attempts") || {}))}`},
      {h: "Flash ast", v: r => g(r, "flash_assists")}, {h: "UD", v: r => g(r, "utility_dmg")}, {h: "Acc %", v: r => g(r, "hits") / (g(r, "shots") || 1), f: r => pct(g(r, "hits") / (g(r, "shots") || 1))},
      {h: "Best streak", v: r => g(r, "best_kill_streak")}], DR, {sort: 0})}</section>`;
  } else {
    h += `<section class="card c12"><h2>Demo stats</h2><p class="empty">No demo matches in this filter yet. Keep the tracker running when you play, and ADR, KAST, rating, clutches, utility and more show up here.</p></section>`;
  }

  /* Per month */
  const months = new Map();
  for (const x of R) { const d = dt(x.t), k = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`; if (!months.has(k)) months.set(k, []); months.get(k).push(x); }
  const mk = [...months.keys()].sort();
  if (mk.length) { // fill gaps
    const [y0, m0] = mk[0].split("-").map(Number), [y1, m1] = mk[mk.length - 1].split("-").map(Number);
    for (let y = y0, m = m0; y < y1 || (y === y1 && m <= m1); m === 12 ? (y++, m = 1) : m++) { const k = `${y}-${String(m).padStart(2, "0")}`; if (!months.has(k)) months.set(k, []); }
  }
  const monthRows = [...months.entries()].sort((x, y) => x[0].localeCompare(y[0])).map(([k, v]) => ({k, v, a: agg(v)}));
  const mlabel = k => { const [y, m] = k.split("-"); return ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+m - 1] + " " + y.slice(2); };
  h += `<section class="card c12"><h2>Per month</h2><p class="sub">Matches played per month. Hover a bar for the numbers.</p><div class="chart" id="ch-month"></div>
    <details><summary>Show as table</summary>${table([
      {h: "Month", v: r => r.k, f: r => mlabel(r.k)}, {h: "Matches", v: r => r.v.length}, {h: "Win %", v: r => r.a?.win, f: r => pct(r.a?.win)},
      {h: "K/D", v: r => r.a?.kd, f: r => fmt(r.a?.kd)}, {h: "Kills/round", v: r => r.a?.kpr, f: r => fmt(r.a?.kpr)}, {h: "HS %", v: r => r.a?.hs, f: r => pct(r.a?.hs)},
      {h: "MVPs/match", v: r => r.a?.mvp, f: r => fmt(r.a?.mvp, 1)}, {h: "Time played", v: r => r.a?.dur, f: r => hm(r.a?.dur)}], monthRows.filter(r => r.v.length), {sort: 0})}</details></section>`;

  /* Maps */
  const maps = {};
  for (const x of R) (maps[x.map] ||= []).push(x);
  const mapRows = Object.entries(maps).map(([mp, v]) => ({mp, a: agg(v)}));
  h += `<section class="card c8"><h2>Maps</h2><p class="sub">Click a column to sort.</p>${table([
    {h: "Map", v: r => r.mp}, {h: "Matches", v: r => r.a.n}, {h: "W-L-T", v: r => r.a.W, f: r => `${r.a.W}-${r.a.L}-${r.a.T}`},
    {h: "Win %", v: r => r.a.win, f: r => pct(r.a.win)}, {h: "K/D", v: r => r.a.kd, f: r => fmt(r.a.kd)}, {h: "Kills/r", v: r => r.a.kpr, f: r => fmt(r.a.kpr)},
    {h: "HS %", v: r => r.a.hs, f: r => pct(r.a.hs)}, {h: "MVP/m", v: r => r.a.mvp, f: r => fmt(r.a.mvp, 1)}, {h: "Round diff", v: r => r.a.rdiff, f: r => (r.a.rdiff > 0 ? "+" : "") + fmt(r.a.rdiff, 1)},
    {h: "Top %", v: r => r.a.top, f: r => pct(r.a.top)}], mapRows)}</section>`;
  h += `<section class="card c4"><h2>Win % per map</h2><p class="sub">Maps with at least 5 matches. Dashed line = 50 %.</p><div class="chart" id="ch-maps"></div></section>`;

  /* Teammates & rivals */
  const mates = new Map(), foes = new Map();
  for (const x of R) {
    for (const p of x.team) if (p[0] !== ME) { const o = mates.get(p[0]) || {id: p[0], n: 0, W: 0, myK: 0, myD: 0, K: 0, D: 0, last: 0}; o.n++; o.W += x.res === "W"; o.myK += x.k || 0; o.myD += x.d || 0; o.K += p[3] || 0; o.D += p[5] || 0; o.last = x.t; mates.set(p[0], o); }
    for (const p of x.foes) { const o = foes.get(p[0]) || {id: p[0], n: 0, W: 0, K: 0, D: 0, myK: 0, myD: 0, last: 0}; o.n++; o.W += x.res === "W"; o.K += p[3] || 0; o.D += p[5] || 0; o.myK += x.k || 0; o.myD += x.d || 0; o.last = x.t; foes.set(p[0], o); }
  }
  const mateRows = [...mates.values()].filter(o => o.n >= 3);
  const foeRows = [...foes.values()].filter(o => o.n >= 2);
  const nm = r => `<span title="${esc(NAMES.get(r.id))}">${esc(NAMES.get(r.id))}</span>`;
  h += `<section class="card c6"><h2>Teammates</h2><p class="sub">Players on your team in at least 3 matches. "Your K/D" is your own K/D in the matches with them.</p>${mateRows.length ? table([
    {h: "Name", v: r => NAMES.get(r.id) || "", f: nm, cls: "name"}, {h: "Matches", v: r => r.n}, {h: "Win %", v: r => r.W / r.n, f: r => pct(r.W / r.n)},
    {h: "Your K/D", v: r => r.myD ? r.myK / r.myD : null, f: r => fmt(r.myD ? r.myK / r.myD : null)}, {h: "Their K/D", v: r => r.D ? r.K / r.D : null, f: r => fmt(r.D ? r.K / r.D : null)},
    {h: "Last", v: r => r.last, f: r => dstr(r.last)}], mateRows) : `<p class="empty">No regular teammates in this selection.</p>`}</section>`;
  h += `<section class="card c6"><h2>Opponents you keep meeting</h2><p class="sub">At least 2 matches on the other team. "Your win %" is your win rate against them.</p>${foeRows.length ? table([
    {h: "Name", v: r => NAMES.get(r.id) || "", f: nm, cls: "name"}, {h: "Met", v: r => r.n}, {h: "Your win %", v: r => r.W / r.n, f: r => pct(r.W / r.n)},
    {h: "Their K/D", v: r => r.D ? r.K / r.D : null, f: r => fmt(r.D ? r.K / r.D : null)}, {h: "Your K/D", v: r => r.myD ? r.myK / r.myD : null, f: r => fmt(r.myD ? r.myK / r.myD : null)},
    {h: "Last", v: r => r.last, f: r => dstr(r.last)}], foeRows) : `<p class="empty">No opponent met more than once in this selection.</p>`}</section>`;

  /* Time of day / weekday */
  h += `<section class="card c6"><h2>When do you play best?</h2><p class="sub">Win rate by weekday and time of day (local time). Darker = more matches; the number in each cell is the win %.</p><div id="dash-heat"></div></section>`;
  h += `<section class="card c6"><h2>Match number in the session</h2><p class="sub">K/D by which match of the session it is (a new session starts after a 2-hour break). Warm-up or tilt?</p><div class="chart" id="ch-sess"></div></section>`;

  /* Records */
  const best = (fn, dir = 1) => R.filter(x => fn(x) != null).reduce((b, x) => b == null || (fn(x) - fn(b)) * dir > 0 ? x : b, null);
  const recs = [
    ["Most kills", best(x => x.k), x => `${x.k} kills`],
    ["Most deaths", best(x => x.d), x => `${x.d} deaths`],
    ["Most assists", best(x => x.a), x => `${x.a} assists`],
    ["Best K/D (10+ kills)", best(x => x.k >= 10 && x.d ? x.k / x.d : null), x => `${x.k}/${x.d} = ${fmt(x.k / x.d)}`],
    ["Worst K/D", best(x => x.d ? x.k / x.d : null, -1), x => `${x.k}/${x.d} = ${fmt(x.k / x.d)}`],
    ["Highest score", best(x => x.score), x => `${x.score} points`],
    ["Most MVPs", best(x => x.mvp), x => `★${x.mvp}`],
    ["Highest HS % (10+ kills)", best(x => x.k >= 10 ? x.hs : null), x => `${x.hs} % on ${x.k} kills`],
    ["Biggest win", best(x => x.us - x.them), x => `${x.us}–${x.them}`],
    ["Biggest loss", best(x => x.us - x.them, -1), x => `${x.us}–${x.them}`],
    ["Longest match", best(x => x.dur), x => hm(x.dur)],
    ["Shortest match", best(x => x.dur, -1), x => hm(x.dur)],
    ["Longest queue wait", best(x => x.wait), x => hm(x.wait)],
  ];
  h += `<section class="card c12"><h2>Records</h2><p class="sub">Within the filter.</p><ul class="facts">` +
    recs.filter(r => r[1]).map(([l, x, f]) => `<li><span>${l}</span><b>${f(x)}</b><span>${mline(x)}</span></li>`).join("") + `</ul></section>`;

  /* Fun / useless facts */
  const streaks = (arr, test) => { let best = 0, cur = 0, bi = -1; arr.forEach((x, i) => { cur = test(x) ? cur + 1 : 0; if (cur > best) { best = cur; bi = i; } }); return [best, bi]; };
  const [wStreak, wi] = streaks(R, x => x.res === "W"), [lStreak, li] = streaks(R, x => x.res === "L");
  let cur = 0, curRes = R[R.length - 1].res; for (let i = R.length - 1; i >= 0 && R[i].res === curRes; i--) cur++;
  const afterW = R.slice(1).filter((x, i) => R[i].res === "W"), afterL = R.slice(1).filter((x, i) => R[i].res === "L");
  const ot = R.filter(x => x.ot), zero = R.filter(x => x.k === 0), thirty = R.filter(x => x.k >= 30);
  const pos = R.filter(x => x.k > x.d).length, neg = R.filter(x => x.k < x.d).length;
  const dayCnt = Array(7).fill(0).map(() => []); for (const x of R) dayCnt[(dt(x.t).getDay() + 6) % 7].push(x);
  const dayA = dayCnt.map((v, i) => [DAYS[i], v.length, v.length ? v.filter(x => x.res === "W").length / v.length : null]).filter(d => d[1] >= 5);
  const bestDay = dayA.reduce((b, d) => !b || d[2] > b[2] ? d : b, null), worstDay = dayA.reduce((b, d) => !b || d[2] < b[2] ? d : b, null);
  const topMap = Object.entries(maps).sort((x, y) => y[1].length - x[1].length)[0];
  const bestMate = mateRows.filter(o => o.n >= 5).sort((x, y) => y.W / y.n - x.W / x.n)[0];
  const mostMate = [...mates.values()].sort((x, y) => y.n - x.n)[0];
  const lastPlace = R.filter(x => x.rank === x.teamSize).length;
  const lobbyTop = R.filter(x => x.lobbyRankK === 1).length;
  const carried = R.filter(x => x.res === "W" && x.rank === x.teamSize).length;
  const unlucky = R.filter(x => x.res === "L" && x.rank === 1).length;
  const facts = [
    [hm(a.dur), "total time in matches"],
    [hm(a.wait), `total time in the matchmaking queue (avg ${hm(a.wait / a.n)})`],
    [a.K.toLocaleString("en-GB") + " kills", `${a.D.toLocaleString("en-GB")} deaths and ${a.A.toLocaleString("en-GB")} assists`],
    [Math.round(sum(R.map(x => x.hsk))).toLocaleString("en-GB") + " headshots", "roughly, worked out from HS % per match"],
    [a.RD.toLocaleString("en-GB") + " rounds", `played in total (${fmt(a.RD / a.n, 1)} per match)`],
    [`${wStreak} wins in a row`, wi >= 0 ? `longest win streak, ended ${dstr(R[wi].t)}` : ""],
    [`${lStreak} losses in a row`, li >= 0 ? `longest losing streak, ended ${dstr(R[li].t)}` : ""],
    [`${cur} ${curRes === "W" ? (cur === 1 ? "win" : "wins") : curRes === "L" ? (cur === 1 ? "loss" : "losses") : (cur === 1 ? "tie" : "ties")} in a row`, "current streak"],
    [pct(afterW.filter(x => x.res === "W").length / (afterW.length || 1)), `win rate in the match after a win (after a loss: ${pct(afterL.filter(x => x.res === "W").length / (afterL.length || 1))})`],
    [`${ot.length} matches went to overtime`, ot.length ? `you won ${pct(ot.filter(x => x.res === "W").length / ot.length)} of them` : ""],
    [`${pos} positive · ${neg} negative`, `matches with more kills than deaths vs fewer (${R.length - pos - neg} exactly even)`],
    [`${zero.length} matches with 0 kills`, zero.length ? `latest: ${mline(zero[zero.length - 1])}` : "never, well done"],
    [`${thirty.length} matches with 30+ kills`, thirty.length ? `latest: ${mline(thirty[thirty.length - 1])}` : ""],
    [`${lobbyTop} times`, "most kills in the whole lobby (both teams)"],
    [`${lastPlace} times`, "bottom of your team's scoreboard"],
    [`${carried} times`, "won the match while bottom of your team (thanks, team)"],
    [`${unlucky} times`, "lost the match while top of your team"],
    bestDay ? [`${bestDay[0]}`, `best weekday: ${pct(bestDay[2])} wins (worst: ${worstDay[0]}, ${pct(worstDay[2])})`] : null,
    topMap ? [esc(topMap[0]), `most played map: ${topMap[1].length} matches (${pct(topMap[1].length / R.length)})`] : null,
    mostMate ? [esc(NAMES.get(mostMate.id)), `most often on your team: ${mostMate.n} matches`] : null,
    bestMate ? [esc(NAMES.get(bestMate.id)), `best win rate together (5+ matches): ${pct(bestMate.W / bestMate.n)} over ${bestMate.n} matches`] : null,
    [new Set(R.flatMap(x => [...x.team, ...x.foes].map(p => p[0]))).size.toLocaleString("en-GB") + " different players", "have been in a match with you"],
    [fmt(a.K / (a.dur / 60 || 1), 2) + " kills per minute", `and one death every ${fmt(a.dur / 60 / (a.D || 1), 1)} minutes`],
  ].filter(Boolean);
  h += `<section class="card c12"><h2>Useful and useless</h2><p class="sub">Within the filter.</p><ul class="facts">` + facts.map(([b, s]) => `<li><b>${b}</b><span>${s}</span></li>`).join("") + `</ul></section>`;

  /* Match list */
  h += `<section class="card c12"><h2>All matches</h2><p class="sub">${R.length} matches. Click a column to sort.</p>${table([
    {h: "Date", v: r => r.t, f: r => `<a href="#matches/${r.id}">${dtstr(r.t)}</a>`}, {h: "Mode", v: r => r.mode}, {h: "Map", v: r => r.map}, {h: "Result", v: r => r.us - r.them, f: r => `<span class="dres-${r.res}">${r.us}–${r.them}</span>`},
    {h: "K", v: r => r.k}, {h: "A", v: r => r.a}, {h: "D", v: r => r.d}, {h: "K/D", v: r => r.d ? r.k / r.d : r.k, f: r => fmt(r.d ? r.k / r.d : r.k)}, {h: "HS %", v: r => r.hs},
    {h: "MVP", v: r => r.mvp}, {h: "Score", v: r => r.score}, {h: "Place", v: r => r.rank, f: r => `${r.rank}/${r.teamSize}`}, {h: "Length", v: r => r.dur, f: r => r.dur != null ? hm(r.dur) : "–"}], R, {sort: 0})}</section>`;

  main.innerHTML = h;

  /* draw charts after layout */
  requestAnimationFrame(() => {
    lineChart($("#ch-kd"), [{name: "K/D", color: "var(--s1)", pts: rollPts(w => w.kd, v => fmt(v))}], {ref: 1, yfmt: v => fmt(v, 1)});
    lineChart($("#ch-win"), [{name: "Win", color: "var(--s1)", pts: rollPts(w => w.win, v => pct(v))}], {ref: 0.5, min: 0.3, max: 0.7, yfmt: v => Math.round(v * 100) + " %"});
    lineChart($("#ch-kpr"), [{name: "KPR", color: "var(--s1)", pts: rollPts(w => w.kpr, v => fmt(v))}], {yfmt: v => fmt(v, 2)});
    lineChart($("#ch-hs"), [{name: "HS", color: "var(--s1)", pts: rollPts(w => w.hs, v => pct(v, 1))}], {yfmt: v => Math.round(v * 100) + " %"});
    const DRc = R.filter(x => x.m.D);
    if (DRc.length) {
      const pts = (fn, f) => DRc.map(x => ({y: fn(x), xl: dstr(x.t), tip: `<b>${esc(x.map)}</b> · ${dstr(x.t)} · ${x.us}–${x.them}<br>${f(fn(x))}`}));
      lineChart($("#ch-adr"), [{name: "ADR", color: "var(--s1)", pts: pts(x => x.m.D.damage / (x.m.D.rounds_played || 1), v => "ADR " + fmt(v, 0))}], {ref: 80, dots: true, yfmt: v => fmt(v, 0)});
      lineChart($("#ch-rating"), [{name: "Rating", color: "var(--s1)", pts: pts(x => x.m.D.rating, v => "Rating " + fmt(v))}], {ref: 1, dots: true, yfmt: v => fmt(v, 1)});
    }
    barChart($("#ch-month"), monthRows.map(r => ({label: mlabel(r.k), v: r.v.length,
      tip: `<b>${mlabel(r.k)}</b><br>${r.v.length} matches` + (r.a ? `<br>Win ${pct(r.a.win)} · K/D ${fmt(r.a.kd)}<br>Kills/round ${fmt(r.a.kpr)} · HS ${pct(r.a.hs)}` : "")})));
    const mr = mapRows.filter(r => r.a.n >= 5).sort((x, y) => y.a.win - x.a.win);
    barChart($("#ch-maps"), mr.map(r => ({label: r.mp.replace("Dust II", "Dust2").slice(0, 7), v: r.a.win, tip: `<b>${esc(r.mp)}</b><br>${r.a.n} matches · ${pct(r.a.win)} wins<br>K/D ${fmt(r.a.kd)}`})),
      {h: 220, ref: 0.5, max: 0.6, yfmt: v => Math.round(v * 100) + " %"});
    // session position
    const sess = []; let pos = 0;
    R.forEach((x, i) => { const prev = R[i - 1]; pos = prev && x.t - (prev.t + (prev.dur || 2400)) < 7200 ? pos + 1 : 1; sess.push(Math.min(pos, 6)); });
    const bins = [1, 2, 3, 4, 5, 6].map(p => ({p, a: agg(R.filter((x, i) => sess[i] === p))})).filter(b => b.a && b.a.n >= 3);
    barChart($("#ch-sess"), bins.map(b => ({label: b.p === 6 ? "6+" : `${b.p}.`, v: b.a.kd, tip: `<b>Match ${b.p === 6 ? "6 or later" : b.p} of the session</b><br>${b.a.n} matches<br>K/D ${fmt(b.a.kd)} · win ${pct(b.a.win)}`})),
      {h: 220, ref: 1, yfmt: v => fmt(v, 1)});
    // heatmap weekday x 3-hour block
    const blocks = ["00–06", "06–12", "12–15", "15–18", "18–20", "20–22", "22–24"], bOf = hr => hr < 6 ? 0 : hr < 12 ? 1 : hr < 15 ? 2 : hr < 18 ? 3 : hr < 20 ? 4 : hr < 22 ? 5 : 6;
    const cell = Array(7).fill(0).map(() => blocks.map(() => []));
    for (const x of R) { const d = dt(x.t); cell[(d.getDay() + 6) % 7][bOf(d.getHours())].push(x); }
    const maxN = Math.max(1, ...cell.flat().map(c => c.length));
    const seq = ["var(--seq1)", "var(--seq2)", "var(--seq3)", "var(--seq4)", "var(--seq5)"];
    let t = `<table class="heat"><thead><tr><th></th>${blocks.map(b => `<th>${b}</th>`).join("")}</tr></thead><tbody>`;
    cell.forEach((row, di) => {
      t += `<tr><th>${DAYS[di]}</th>` + row.map((c, bi) => {
        if (!c.length) return `<td style="background:var(--surface2)"></td>`;
        const w = c.filter(x => x.res === "W").length / c.length, lvl = Math.min(4, Math.floor(c.length / maxN * 4.999));
        return `<td data-d="${di}" data-b="${bi}" style="background:${seq[lvl]};color:${lvl >= 3 ? "var(--sand-hi)" : "var(--ink)"}">${Math.round(w * 100)}</td>`;
      }).join("") + `</tr>`;
    });
    $("#dash-heat").innerHTML = t + `</tbody></table>`;
    $("#dash-heat").querySelectorAll("td[data-d]").forEach(td => {
      const c = cell[+td.dataset.d][+td.dataset.b], A = agg(c);
      td.addEventListener("mousemove", e => showTip(e, `<b>${DAYS[+td.dataset.d]} ${blocks[+td.dataset.b]}</b><br>${A.n} matches · win ${pct(A.win)}<br>K/D ${fmt(A.kd)}`));
      td.addEventListener("mouseleave", hideTip);
    });
  });
}

/* ---------- view ---------- */
const visible = () => !$("#view-dashboard").hidden;

export function initDashboard() {
  tip = $("#dash-tip");
  $("#dash-f-mode").addEventListener("change", () => { fillMaps(); render(); });
  for (const id of ["#dash-f-period", "#dash-f-map", "#dash-f-roll"]) $(id).addEventListener("change", render);
  let rt;
  addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { if (visible() && loaded) render(); }, 200); });
}

/** Load fresh data every time the tab opens (imports may have added matches). */
export async function renderDashboard() {
  try {
    await load();
  } catch (e) {
    $("#dash-main").innerHTML = `<div class="card c12 empty">${esc(e.message)}</div>`;
    return;
  }
  fillFilters();
  render();
}
