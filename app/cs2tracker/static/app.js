// CS2 Squad Tracker front end. Plain ES module, no framework, no build step.
// Every piece of data (player names especially) goes into the page via textContent.
// The Rivals tab lives in rivals.js; it imports the shared helpers exported here.

import { initRivals, renderRivals } from "/static/rivals.js";
import { initDashboard, renderDashboard } from "/static/dashboard.js";
import { advanceAfter, initWelcome, renderOnboarding, showWelcome, welcomeShown } from "/static/welcome.js";

const GAP_WARNING = "You've played more than 8 matches since the last Update — older demos may be missing.";
const POLL_MS = 1000;
const POLL_MAX_FAILURES = 30;
const UNREACHABLE = "Can't reach the tracker — is the black window still open?";

// ---------- small helpers ----------

export const $ = (sel, root = document) => root.querySelector(sel);

/** h("tag.class", {attrs/props/onX}, ...children). Strings become text nodes. */
export function h(spec, attrs = {}, ...children) {
  const [tag, ...classes] = spec.split(".");
  const el = document.createElement(tag || "div");
  if (classes.length) el.className = classes.join(" ");
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className += (el.className ? " " : "") + v;
    else if (["checked", "value", "disabled", "selected", "hidden", "open"].includes(k)) el[k] = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function clear(el, ...children) {
  el.replaceChildren();
  return append(el, children);
}

class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

function detailText(detail, fallback) {
  if (!detail) return fallback;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => (d && d.msg ? d.msg.replace(/^Value error, /, "") : String(d))).join("; ");
  }
  return fallback;
}

export async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch {
    throw new ApiError(0, "Can't reach the tracker. Is it still running?");
  }
  let data = null;
  try { data = await res.json(); } catch { /* empty or non-JSON body */ }
  if (res.status === 428 && data && data.code === "no_owner") backToWelcome();
  if (res.status === 409 && data && data.code === "newer_db") showOutdatedBanner(detailText(data && data.detail, "Your data was upgraded by a newer version."));
  if (!res.ok) throw new ApiError(res.status, detailText(data && data.detail, `${res.status} ${res.statusText}`));
  return data;
}

/** The Steam ID is gone (e.g. a restored backup without it): show the welcome screen again. */
async function backToWelcome() {
  if (welcomeShown) return;
  let owner = null;
  try { owner = await api("GET", "/api/owner"); } catch { /* show it without candidates */ }
  showWelcome(owner);
}

/** Persistent banner (not a toast) when a newer version has upgraded the data; locks the page. */
function showOutdatedBanner(msg) {
  let b = document.getElementById("outdated-banner");
  if (!b) {
    b = document.createElement("div");
    b.id = "outdated-banner";
    b.className = "banner";
    b.setAttribute("role", "alert");
    document.body.prepend(b);
  }
  b.textContent = msg;
  document.querySelectorAll("button").forEach((x) => { x.disabled = true; });
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let toastTimer;
export function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("error", isError);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, isError ? 6000 : 3000);
}

/** Path token for /api/import/{token}/...: urlsafe base64 of the UTF-8 path, unpadded. */
export function pathToken(path) {
  const bytes = new TextEncoder().encode(path);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

const baseName = (p) => String(p).split(/[\\/]/).pop();
const fmtGB = (b) => `${(b / 1e9).toFixed(1)} GB`;
const fmtMB = (b) => { const mb = b / 1e6; return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`; };

export function mapName(m) {
  if (!m) return "?";
  const s = m.replace(/^(de|cs|ar)_/, "");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

const DATE_FMT = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
const DATE_UTC_FMT = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const TIME_FMT = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });

/** Estimated starts are a calendar date only (stored as UTC midnight or import time). */
export function fmtDate(iso, estimated) {
  if (!iso) return "No date";
  const d = new Date(iso);
  return estimated ? DATE_UTC_FMT.format(d) : DATE_FMT.format(d);
}
export function fmtTime(iso, estimated) {
  if (!iso || estimated) return "";
  return TIME_FMT.format(new Date(iso));
}

export const fmt2 = (v) => (v === null || v === undefined ? "–" : Number(v).toFixed(2));
const fmt0 = (v) => (v === null || v === undefined ? "–" : String(Math.round(v)));
const dash = (v) => (v === null || v === undefined ? "–" : String(v));

export function resultBadge(r) {
  const code = r || "?";
  const label = { W: "Win", L: "Loss", D: "Draw" }[code] || "Unknown";
  return h(`span.res.res-${code}`, { title: label }, code);
}

function scoreText(m) {
  return m.my_team_score === null || m.my_team_score === undefined ? "?" : `${m.my_team_score}–${m.enemy_score}`;
}

const SOURCE_LABEL = { demo: "Demo", leetify: "Leetify", steam: "Steam", both: "Demo + summary" };

const MODE_TAG = { competitive: "Comp", premier: "Premier" };

/** The small "Comp" / "Premier" tag for a match's game mode (null when unknown). */
export function modeTag(gameMode) {
  const label = MODE_TAG[gameMode];
  return label ? h(`span.mode-tag.mode-${gameMode}`, { title: gameMode === "premier" ? "Premier" : "Competitive" }, label) : null;
}

/** A match's game mode: game_mode, else Leetify's mode text when it names one. */
export function gameModeOf(m) {
  if (!m) return null;
  if (MODE_TAG[m.game_mode]) return m.game_mode;
  const t = String(m.mode || "").toLowerCase();
  return MODE_TAG[t] ? t : null;
}

export function steamLink(url, text) {
  return h("a", { href: url, target: "_blank", rel: "noopener noreferrer", title: "Open Steam profile" }, text);
}

// ---------- shared state ----------

export const state = {
  settings: null,
  people: null,       // last /api/people (all time)
  groups: [],         // last /api/groups: [{id, name, members}]
  jobRunning: false,
};

export async function loadSettings() {
  state.settings = await api("GET", "/api/settings");
  return state.settings;
}

async function loadGroups() {
  state.groups = await api("GET", "/api/groups");
  return state.groups;
}

/** "½/6", "4½/6", "5/6": a partial match counts ½. */
function fmtPlayed(played, total) {
  const whole = Math.floor(played + 1e-9);
  const half = played - whole >= 0.5 - 1e-9;
  return `${whole || !half ? whole : ""}${half ? "½" : ""}/${total}`;
}

function groupCheckboxes(selected) {
  return state.groups.map((g) => h("label.check", {},
    h("input", { type: "checkbox", value: g.id, checked: selected.has(g.id) }),
    h("span", {}, g.name)));
}

const checkedIds = (root) => [...root.querySelectorAll("input[type=checkbox]:checked")].map((c) => Number(c.value));

// ---------- jobs ----------

const STAGE_TEXT = {
  steam: () => "Asking Steam for your recent matches…",
  download: (d, t) => `Downloading demos — ${d} of ${t}`,
  import: (d, t) => `Reading demos — ${d} of ${t}`,
};

function jobLabel(job) {
  if (job.stage && STAGE_TEXT[job.stage]) return STAGE_TEXT[job.stage](job.done, job.total);
  if (job.kind === "reparse") return job.total ? `Re-reading demos — ${job.done} of ${job.total}` : "Re-reading demos…";
  if (job.kind === "import") return job.total ? `Reading demos — ${job.done} of ${job.total}` : "Reading demos…";
  if (job.kind === "steam_history") return job.total ? `Importing Steam matches — ${job.done} of ${job.total}` : "Importing Steam matches…";
  return "Starting…";
}

/** Poll /api/jobs/{id} every second until it ends; onTick(job) on each poll.
 *  Gives up after POLL_MAX_FAILURES network failures in a row. */
async function pollJob(id, onTick) {
  let failures = 0;
  for (;;) {
    let job;
    try {
      job = await api("GET", `/api/jobs/${id}`);
    } catch (e) {
      if (e.status !== 0) throw e;
      failures += 1;
      if (failures >= POLL_MAX_FAILURES) throw new ApiError(0, UNREACHABLE);
      await sleep(POLL_MS);
      continue;
    }
    failures = 0;
    onTick(job);
    if (job.state !== "running") return job;
    await sleep(POLL_MS);
  }
}

function showProgress(wrap, label, fill, job) {
  wrap.hidden = false;
  label.textContent = jobLabel(job);
  const pct = job.total ? Math.round((100 * job.done) / job.total) : 0;
  fill.classList.toggle("indeterminate", !job.total || job.stage === "steam");
  fill.style.width = `${pct}%`;
}

function setBusy(busy) {
  state.jobRunning = busy;
  $("#update-btn").disabled = busy;
  $("#update-btn").textContent = busy ? "Working…" : "Update";
  $("#drop-zone").classList.toggle("disabled", busy);
  $("#file-input").disabled = busy;
  $("#reparse-btn").disabled = busy;
  $("#steam-import-btn").disabled = busy;
  for (const b of document.querySelectorAll(".reparse-now")) b.disabled = busy;
}

// ---------- Home ----------

async function runHomeJob(jobId) {
  setBusy(true);
  const wrap = $("#update-progress");
  try {
    const job = await pollJob(jobId, (j) => showProgress(wrap, $("#update-stage"), $("#update-fill"), j));
    renderJobOutcome(job);
  } catch (e) {
    clear($("#update-messages"), notice("error", "Couldn't follow the job", e.message));
  } finally {
    wrap.hidden = true;
    setBusy(false);
    state.people = null;
    renderLastSession();
  }
}

export function notice(kind, title, ...body) {
  return h(`div.notice.${kind}`, { role: kind === "error" ? "alert" : null }, h("h3", {}, title), ...body);
}

function renderJobOutcome(job) {
  const msgs = $("#update-messages");
  clear(msgs);
  if (job.state === "error") {
    msgs.append(notice("error", "Something went wrong", h("p", {}, job.error || "Unknown error")));
  }
  const rep = job.report;
  if (rep) {
    if (rep.steam_error) msgs.append(notice("error", "Steam didn't answer", h("p", {}, rep.steam_error)));
    if (rep.scan_error) msgs.append(notice("error", "Couldn't read the replays folder", h("p", {}, rep.scan_error)));
    if (rep.scan_warnings && rep.scan_warnings.length) {
      msgs.append(notice("warn", `${rep.scan_warnings.length} demo${rep.scan_warnings.length === 1 ? "" : "s"} skipped`,
        h("ul", {}, rep.scan_warnings.map((w) => h("li", {}, w))),
        h("p.muted", {}, "Another program may be using them. Press Update again later.")));
    }
    if (rep.download_errors && rep.download_errors.length) {
      msgs.append(notice("error", `${rep.download_errors.length} demo download${rep.download_errors.length === 1 ? "" : "s"} failed`,
        h("ul", {}, rep.download_errors.map((e) => h("li", {}, e)))));
    }
    if (rep.history_error) msgs.append(notice("error", "Couldn't read your match history", h("p", {}, rep.history_error)));
    if (rep.history_hint) msgs.append(notice("warn", "Match history not started yet", h("p", {}, rep.history_hint)));
    if (rep.cs2_running && rep.cs2_running_message) msgs.append(notice("warn", "CS2 is running", h("p", {}, rep.cs2_running_message)));
    if (rep.low_disk && rep.low_disk_message) msgs.append(notice("error", "Not enough disk space", h("p", {}, rep.low_disk_message)));
    if (rep.expired && rep.expired.length) {
      msgs.append(notice("warn", `${rep.expired.length} demo${rep.expired.length === 1 ? "" : "s"} no longer available`,
        h("ul", {}, rep.expired.map((e) => h("li", {}, e)))));
    }
    if (rep.skipped && rep.skipped.length) {
      msgs.append(notice("warn", `${rep.skipped.length} match${rep.skipped.length === 1 ? "" : "es"} skipped`,
        h("ul", {}, rep.skipped.map((e) => h("li", {}, e)))));
    }
    const waiting = rep.waiting || [];
    if (rep.queued > 0 || waiting.length) {
      msgs.append(notice("warn", `${rep.queued} match${rep.queued === 1 ? "" : "es"} waiting`,
        waiting.length ? h("ul", {}, waiting.map((w) => h("li", {}, w))) : null,
        h("p.muted", {}, "They'll be fetched on the next Update.")));
    }
    if (rep.freed_bytes > 0) msgs.append(notice("ok", `Freed ${fmtGB(rep.freed_bytes)}`, h("p", {}, "Old demos were deleted. Their stats are kept.")));
    if (rep.gap_warning) msgs.append(notice("warn", "Some matches may be missing", h("p", {}, GAP_WARNING)));
    if (rep.downloaded && rep.downloaded.length) {
      msgs.append(notice("ok", `Downloaded ${rep.downloaded.length} new demo${rep.downloaded.length === 1 ? "" : "s"}`,
        h("ul", {}, rep.downloaded.map((d) => h("li", {}, baseName(d))))));
    }
  }
  const results = rep ? rep.imported || [] : job.results || [];
  if (job.kind === "reparse") return;
  // "You're up to date" only when nothing is waiting or blocked
  const blocked = rep && (rep.steam_error || rep.scan_error || rep.history_error || rep.cs2_running ||
    rep.low_disk || rep.queued > 0);
  renderImportReport(results, job.state !== "error" && !blocked);
}

const REPORT_GROUPS = [
  ["possible_duplicate", "Possible duplicates — choose what to do"],
  ["added", "Added"],
  ["attached", "Attached to an existing match"],
  ["skipped", "Already imported"],
  ["not_ready", "Not ready yet"],
  ["failed", "Failed"],
];

function renderImportReport(results, showEmpty) {
  const box = $("#import-report");
  clear(box);
  if (!results.length) {
    if (showEmpty) box.append(notice("ok", "You're up to date", h("p", {}, "No new demos to import.")));
    return;
  }
  const panel = h("div.panel", {}, h("h2", {}, "Import report"));
  for (const [status, title] of REPORT_GROUPS) {
    const rows = results.filter((r) => r.status === status);
    if (!rows.length) continue;
    panel.append(h("div.report-group", {},
      h("h3", {}, title, h("span.count", {}, rows.length)),
      rows.map(reportRow)));
  }
  box.append(panel);
}

function reportRow(r) {
  const row = h("div.report-row", {}, h("span.file", {}, baseName(r.path)));
  if (r.match_id) row.append(h("a", { href: `#matches/${r.match_id}` }, "View match"));
  else row.append(h("span"));
  if (r.reason) row.append(h("span.reason", {}, r.reason));
  if (r.status === "possible_duplicate") row.append(duplicateChooser(r, row));
  return row;
}

function duplicateChooser(r, row) {
  const box = h("div.dup-options", {}, h("span.muted", {}, "This demo looks like a match you already have:"));
  const token = pathToken(r.path);
  const buttons = [];
  const settle = async (doCall) => {
    buttons.forEach((b) => { b.disabled = true; });
    try {
      const res = await doCall();
      const done = h("div.report-row", {},
        h("span.file", {}, baseName(r.path)),
        res.match_id ? h("a", { href: `#matches/${res.match_id}` }, "View match") : h("span"),
        h("span.reason", {}, res.status === "attached" ? "Attached to the existing match." :
          res.status === "added" ? "Added as a new match." : `${res.status}: ${res.reason || ""}`));
      row.replaceWith(done);
      renderLastSession();
    } catch (e) {
      buttons.forEach((b) => { b.disabled = false; });
      toast(e.message, true);
    }
  };
  for (const mid of r.candidates || []) {
    const desc = h("span", {}, `Match #${mid}`);
    const btn = h("button.btn.btn-small", { type: "button", onclick: () => settle(() => api("POST", `/api/import/${token}/attach/${mid}`)) }, "Attach to this match");
    buttons.push(btn);
    box.append(h("div.dup-option", {}, btn, desc));
    api("GET", `/api/matches/${mid}`).then(({ match: m }) => {
      clear(desc, `${fmtDate(m.started_at, m.start_estimated)} ${fmtTime(m.started_at, m.start_estimated)} · ${mapName(m.map)} ${scoreText(m)} · ${SOURCE_LABEL[m.source] || m.source}`);
    }).catch(() => {});
  }
  const add = h("button.btn-quiet.btn-small", { type: "button", onclick: () => settle(() => api("POST", `/api/import/${token}/new`)) }, "Add as a new match");
  buttons.push(add);
  box.append(h("div.dup-option", {}, add));
  return box;
}

async function startUpdate() {
  if (state.jobRunning) return;
  clear($("#update-messages"));
  clear($("#import-report"));
  try {
    const { job_id } = await api("POST", "/api/update");
    await runHomeJob(job_id);
  } catch (e) {
    clear($("#update-messages"), notice("error", "Update didn't start", h("p", {}, e.message)));
  }
}

async function uploadFiles(fileList) {
  // Steam's .dem.info sidecar files often come along when a folder is selected: skip them.
  const files = [...fileList].filter((f) => !/\.info$/i.test(f.name));
  if (!files.length || state.jobRunning) return;
  const bad = files.filter((f) => !/\.(dem|zip|gz|bz2)$/i.test(f.name));
  if (bad.length) {
    clear($("#update-messages"), notice("error", "Not a demo file",
      h("p", {}, `Only .dem, .zip, .gz and .bz2 files can be imported: ${bad.map((f) => f.name).join(", ")}`)));
    return;
  }
  clear($("#update-messages"));
  clear($("#import-report"));
  const fd = new FormData();
  files.forEach((f, i) => {
    fd.append("files", f, f.name);
    // The file's own date: the upload time would make the match look new (and unmatched).
    if (f.lastModified) fd.append(`mtime_${i}`, String(f.lastModified));
  });
  setBusy(true);
  const wrap = $("#update-progress");
  wrap.hidden = false;
  $("#update-stage").textContent = `Uploading ${files.length} file${files.length === 1 ? "" : "s"}…`;
  $("#update-fill").classList.add("indeterminate");
  let jobId;
  try {
    ({ job_id: jobId } = await api("POST", "/api/import", fd));
  } catch (e) {
    wrap.hidden = true;
    setBusy(false);
    clear($("#update-messages"), notice(e.status === 409 ? "warn" : "error",
      e.status === 409 ? "Busy" : "Import didn't start", h("p", {}, e.message)));
    return;
  }
  await runHomeJob(jobId);
}

function initHome() {
  $("#update-btn").addEventListener("click", startUpdate);
  const zone = $("#drop-zone");
  const input = $("#file-input");
  input.addEventListener("change", () => { uploadFiles(input.files); input.value = ""; });
  zone.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && !state.jobRunning) { e.preventDefault(); input.click(); }
  });
  zone.addEventListener("dragover", (e) => { e.preventDefault(); if (!state.jobRunning) zone.classList.add("over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("over");
    if (e.dataTransfer && e.dataTransfer.files) uploadFiles(e.dataTransfer.files);
  });
  // A file dropped outside the zone must not navigate away from the app.
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => e.preventDefault());
}

async function renderLastSession() {
  const body = $("#last-session .body");
  let sessions;
  try {
    sessions = await api("GET", "/api/sessions");
  } catch (e) {
    clear(body, h("p.status.error", {}, e.message));
    return;
  }
  if (!sessions.length) {
    clear(body, h("p.muted", {}, "No matches yet. Press Update or drop a demo to get started."));
    return;
  }
  const s = sessions[0];
  const ms = s.matches;
  const first = ms[0];
  const tally = ["W", "L", "D"].map((c) => [c, ms.filter((m) => m.result === c).length]).filter(([, n]) => n);
  const people = [...new Set(ms.flatMap((m) => m.people))].sort((a, b) => a.localeCompare(b));
  const times = ms.filter((m) => !m.start_estimated).map((m) => fmtTime(m.started_at, false));
  clear(body,
    h("div.session-date", {}, fmtDate(first.started_at, first.start_estimated)),
    h("div.session-meta", {},
      `${ms.length} match${ms.length === 1 ? "" : "es"}`,
      tally.length ? ` · ${tally.map(([c, n]) => `${n}${c}`).join(" ")}` : "",
      times.length ? ` · from ${times[0]}` : ""),
    h("div.match-strip", {}, ms.map((m) => h("a.mchip", { href: `#matches/${m.id}` },
      h("b", {}, mapName(m.map)), scoreText(m), resultBadge(m.result)))),
    people.length ? h("div.people-line", {}, "With ", people.join(", ")) : null,
    h("a.btn.btn-ct", { href: "#summary", role: "button", style: "display:inline-block;text-decoration:none" }, "Build summary"));
}

// ---------- Matches ----------

let matchesLoaded = false;
let quietReset = false;

function matchFilters() {
  const f = new FormData($("#match-filters"));
  const q = new URLSearchParams();
  for (const k of ["since", "until", "group", "map", "mode"]) if (f.get(k)) q.set(k, f.get(k));
  return q;
}

async function initMatchFilterOptions() {
  const all = await api("GET", "/api/matches");
  const maps = [...new Set(all.map((m) => m.map).filter(Boolean))].sort();
  const sel = $("#match-filters select[name=map]");
  const cur = sel.value;
  clear(sel, h("option", { value: "" }, "Any"), maps.map((m) => h("option", { value: m }, mapName(m))));
  sel.value = cur;
  await loadGroups();
  const gsel = $("#match-filters select[name=group]");
  const gcur = gsel.value;
  clear(gsel, h("option", { value: "" }, "All"), state.groups.map((g) => h("option", { value: g.id }, g.name)));
  // A group deleted since the last visit falls back to All.
  gsel.value = state.groups.some((g) => String(g.id) === gcur) ? gcur : "";
}

async function renderMatches(openId) {
  const tbody = $("#matches-table tbody");
  if (!matchesLoaded) {
    matchesLoaded = true;
    try { await initMatchFilterOptions(); } catch (e) { toast(e.message, true); }
  }
  let rows;
  try {
    rows = await api("GET", `/api/matches?${matchFilters()}`);
  } catch (e) {
    clear(tbody, h("tr", {}, h("td.empty", { colspan: 7 }, e.message)));
    return;
  }
  clear(tbody);
  if (!rows.length) {
    tbody.append(h("tr", {}, h("td.empty", { colspan: 7 }, "No matches fit these filters.")));
    return;
  }
  for (const m of rows) {
    const tr = h("tr.mrow", { tabindex: 0, "aria-expanded": "false", dataset: { id: m.id } },
      h("td.date-cell", {}, fmtDate(m.started_at, m.start_estimated), " ",
        h("small", { title: m.start_estimated ? "Start time unknown — date estimated" : null },
          m.start_estimated ? "≈" : fmtTime(m.started_at, false))),
      h("td", {}, h("span.mapname", {}, mapName(m.map))),
      h("td", {}, modeTag(gameModeOf(m)) || m.mode || "5v5 (mode not known yet)"),
      h("td.num", {}, scoreText(m)),
      h("td", {}, resultBadge(m.result)),
      h("td.people-cell", {}, m.people.join(", ") || "–"),
      h("td", {}, SOURCE_LABEL[m.source] || m.source));
    const toggle = () => toggleMatchDetail(tr, m.id);
    tr.addEventListener("click", (e) => { if (!e.target.closest("a")) toggle(); });
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
    tbody.append(tr);
  }
  if (openId) {
    const tr = tbody.querySelector(`tr.mrow[data-id="${CSS.escape(String(openId))}"]`);
    if (tr) { await toggleMatchDetail(tr, openId); tr.scrollIntoView({ block: "center" }); }
  }
}

async function toggleMatchDetail(tr, id) {
  const next = tr.nextElementSibling;
  if (next && next.classList.contains("detail")) {
    next.remove();
    tr.classList.remove("open");
    tr.setAttribute("aria-expanded", "false");
    return;
  }
  tr.classList.add("open");
  tr.setAttribute("aria-expanded", "true");
  const cell = h("td", { colspan: 7 }, h("p.muted", {}, "Loading…"));
  tr.after(h("tr.detail", {}, cell));
  try {
    const d = await api("GET", `/api/matches/${id}`);
    clear(cell, matchDetail(d));
  } catch (e) {
    clear(cell, h("p.status.error", {}, e.message));
  }
}

function matchDetail({ match: m, players }) {
  const me = state.settings && state.settings.me_steam_id;
  const meta = h("div.meta", {},
    h("span", {}, `Source: ${SOURCE_LABEL[m.source] || m.source}`),
    m.duration ? h("span", {}, `Duration: ${Math.round(m.duration / 60)} min`) : null,
    m.server ? h("span", {}, `Server: ${m.server}`) : null,
    m.demo_path ? h("span.path", {}, `Demo: ${baseName(m.demo_path)}`) : null);
  const hasRating = players.some((p) => p.hltv_rating !== null || p.leetify_rating !== null);
  const team = (key, title) => {
    const ps = players.filter((p) => p.team === key).sort((a, b) => (b.kills ?? -1) - (a.kills ?? -1));
    const score = key === "mine" ? m.my_team_score : m.enemy_score;
    return h(`div.team-block.${key}`, {},
      h("h3", {}, h("span", {}, title), h("span", {}, score ?? "")),
      h("table.data", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Player"),
          ["K", "A", "D", "K/D", "ADR", "HS %", "2K", "3K", "4K", "Aces"].map((c) => h("th.num", {}, c)),
          hasRating ? h("th.num", { title: "HLTV rating, or Leetify rating when that's all there is" }, "Rating") : null,
          h("th", {}, "Fav weapon"))),
        h("tbody", {}, ps.map((p) => {
          const name = p.name_in_match || p.steam_id;
          const kd = p.kills !== null && p.deaths ? p.kills / p.deaths : null;
          const rating = p.hltv_rating ?? p.leetify_rating;
          return h(`tr${p.steam_id === me ? ".is-me" : ""}`, {},
            h("td.pname", {}, steamLink(p.steam_url, name),
              p.person_name && p.person_name !== name ? h("span.as", {}, p.person_name) : null),
            h("td.num", {}, dash(p.kills)), h("td.num", {}, dash(p.assists)), h("td.num", {}, dash(p.deaths)),
            h("td.num", {}, fmt2(kd)), h("td.num", {}, fmt0(p.adr)), h("td.num", {}, fmt0(p.hs_pct)),
            h("td.num", {}, dash(p.k2)), h("td.num", {}, dash(p.k3)), h("td.num", {}, dash(p.k4)), h("td.num", {}, dash(p.k5)),
            hasRating ? h("td.num", {}, rating === null || rating === undefined ? "–" : fmt2(rating)) : null,
            h("td", {}, p.kills_summary.fav_weapon || "–"));
        }))));
  };
  return h("div.match-detail", {}, meta, team("mine", "My team"), team("enemy", "Enemy team"),
    deleteMatchControls(m));
}

/** "Delete match" with a confirm step inside the page (no window.confirm). */
function deleteMatchControls(m) {
  const box = h("div.delete-match");
  const ask = () => {
    const yes = h("button.btn.btn-small.btn-danger", { type: "button" }, "Yes, delete it");
    const no = h("button.btn-quiet.btn-small", { type: "button", onclick: idle }, "Cancel");
    yes.addEventListener("click", async () => {
      yes.disabled = true;
      no.disabled = true;
      try {
        await api("DELETE", `/api/matches/${m.id}`);
        toast("Match deleted.");
        const detail = box.closest("tr.detail");
        if (detail) {
          const row = detail.previousElementSibling;
          if (row && row.classList.contains("mrow")) row.remove();
          detail.remove();
        }
        matchesLoaded = false;
        state.people = null;
        renderLastSession();
      } catch (e) {
        yes.disabled = false;
        no.disabled = false;
        toast(e.message, true);
      }
    });
    clear(box, h("span.warn-text", {}, "Delete this match and all its stats? A backup is made first."), yes, no);
    no.focus();
  };
  const idle = () => clear(box, h("button.btn-quiet.btn-small", { type: "button", onclick: ask }, "Delete match"));
  idle();
  return box;
}

function initMatches() {
  const form = $("#match-filters");
  form.addEventListener("change", () => renderMatches());
  const seg = $("#matches-mode");
  const pressMode = (mode) => {
    for (const b of seg.querySelectorAll("button[data-mode]")) b.setAttribute("aria-pressed", String(b.dataset.mode === mode));
  };
  seg.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-mode]");
    if (!b) return;
    form.elements.mode.value = b.dataset.mode;
    pressMode(b.dataset.mode);
    renderMatches();
  });
  form.addEventListener("reset", () => {
    form.elements.mode.value = "";   // hidden input: reset() alone would keep the last mode
    pressMode("");
    if (!quietReset) setTimeout(() => renderMatches(), 0);
  });
  form.addEventListener("submit", (e) => e.preventDefault());
}

// ---------- People ----------

const openAccounts = new Set();
const SORT_KEY = "cs2t.peopleSort";

/** [key, label, numeric, value(p)]. Every column sorts; null values always go last. */
const PEOPLE_COLS = [
  ["tracked", "Tracked", false, (p) => (p.tracked ? 1 : 0)],
  ["name", "Name", false, (p) => p.name],
  ["groups", "Groups", false, (p) => (p.groups.length ? p.groups.map((g) => g.name).join(", ") : null)],
  ["games_together", "Games together", true, (p) => p.games_together],
  ["matches", "Matches in app", true, (p) => (p.stats ? p.stats.matches : null)],
  ["win", "Win %", true, (p) => (p.stats && p.stats.matches ? p.stats.wins / p.stats.matches : null)],
  ["kd", "K/D", true, (p) => (p.stats ? p.stats.kd : null)],
  ["adr", "ADR", true, (p) => (p.stats ? p.stats.adr : null)],
  ["k2", "2K", true, (p) => (p.stats ? p.stats.k2 : null)],
  ["k3", "3K", true, (p) => (p.stats ? p.stats.k3 : null)],
  ["k4", "4K", true, (p) => (p.stats ? p.stats.k4 : null)],
  ["k5", "Aces", true, (p) => (p.stats ? p.stats.k5 : null)],
  ["fav", "Fav weapon", false, (p) => (p.stats && p.stats.fav_weapon ? weaponName(p.stats.fav_weapon) : null)],
  ["accounts", "Accounts", true, (p) => p.accounts.length],
];
const PEOPLE_NCOLS = PEOPLE_COLS.length;
const DEFAULT_SORT = { key: "games_together", dir: "desc" };

function loadPeopleSort() {
  try {
    const v = JSON.parse(localStorage.getItem(SORT_KEY));
    if (v && PEOPLE_COLS.some(([k]) => k === v.key) && (v.dir === "asc" || v.dir === "desc")) return v;
  } catch { /* storage blocked or bad JSON */ }
  return { ...DEFAULT_SORT };
}

function savePeopleSort(sort) {
  try { localStorage.setItem(SORT_KEY, JSON.stringify(sort)); } catch { /* storage blocked */ }
}

const people = { rows: [], sort: loadPeopleSort() };

function sortPeople(rows, { key, dir }) {
  const col = PEOPLE_COLS.find(([k]) => k === key) || PEOPLE_COLS[3];
  const get = col[3];
  const sign = dir === "asc" ? 1 : -1;
  return rows.slice().sort((a, b) => {
    const va = get(a) ?? null, vb = get(b) ?? null;
    if (va === null && vb !== null) return 1;
    if (vb === null && va !== null) return -1;
    let c = 0;
    if (va !== null) c = typeof va === "string" ? va.localeCompare(vb, undefined, { sensitivity: "base" }) : va - vb;
    return c * sign || a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
  });
}

function renderPeopleHead() {
  const tr = $("#people-table thead tr");
  const { key, dir } = people.sort;
  clear(tr, PEOPLE_COLS.map(([k, label, numeric]) => {
    const active = k === key;
    const btn = h("button.sort-btn", { type: "button" }, label,
      h("span.sort-mark", { "aria-hidden": "true" }, active ? (dir === "asc" ? "▲" : "▼") : ""));
    btn.addEventListener("click", () => {
      // Same column: reverse. New column: numbers start high-first, text A–Z.
      people.sort = active ? { key: k, dir: dir === "asc" ? "desc" : "asc" } : { key: k, dir: numeric ? "desc" : "asc" };
      savePeopleSort(people.sort);
      renderPeopleHead();
      renderPeopleRows();
      const again = $("#people-table thead").querySelectorAll("button.sort-btn")[PEOPLE_COLS.findIndex(([c]) => c === k)];
      if (again) again.focus();
    });
    return h(`th${numeric ? ".num" : ""}`, {
      "aria-sort": active ? (dir === "asc" ? "ascending" : "descending") : "none",
      title: k === "tracked" ? "Tracked people are pre-ticked in summaries" : null,
    }, btn);
  }));
}

async function renderPeople() {
  const f = new FormData($("#people-range"));
  const q = new URLSearchParams();
  for (const k of ["since", "until"]) if (f.get(k)) q.set(k, f.get(k));
  const tbody = $("#people-table tbody");
  renderPeopleHead();
  try {
    const [rows] = await Promise.all([api("GET", `/api/people?${q}`), loadGroups()]);
    people.rows = rows;
    if (!q.toString()) state.people = rows;
    else if (!state.people) state.people = await api("GET", "/api/people");
  } catch (e) {
    clear(tbody, h("tr", {}, h("td.empty", { colspan: PEOPLE_NCOLS }, e.message)));
    return;
  }
  clear($("#add-person-groups"), groupCheckboxes(new Set()));
  renderPeopleRows();
  renderManageGroups();
  renderRecurring();
}

function renderPeopleRows() {
  const tbody = $("#people-table tbody");
  clear(tbody);
  if (!people.rows.length) tbody.append(h("tr", {}, h("td.empty", { colspan: PEOPLE_NCOLS }, "No people yet. Add one below.")));
  for (const p of sortPeople(people.rows, people.sort)) {
    const s = p.stats;
    const tracked = h("input", { type: "checkbox", checked: p.tracked, "aria-label": `Track ${p.name}` });
    tracked.addEventListener("change", async () => {
      tr.classList.toggle("untracked", !tracked.checked);
      if (await patchPerson(p.id, { tracked: tracked.checked })) p.tracked = tracked.checked;
      else tracked.checked = !tracked.checked;
    });
    const n = p.accounts.length;
    const toggle = h("button.acct-toggle", { type: "button", "aria-expanded": String(openAccounts.has(p.id)) },
      `${n} account${n === 1 ? "" : "s"}`);
    const tr = h(`tr${p.tracked ? "" : ".untracked"}`, {},
      h("td", {}, tracked), h("td.name", {}, p.name), h("td.groups-cell", {}, groupCell(p)),
      h("td.num", {}, p.games_together == null ? "–" : p.games_together),
      h("td.num", {}, s ? s.matches : "–"),
      h("td.num", {}, s && s.matches ? `${Math.round((100 * s.wins) / s.matches)}` : "–"),
      h("td.num", {}, s ? fmt2(s.kd) : "–"), h("td.num", {}, s ? fmt0(s.adr) : "–"),
      h("td.num", {}, s ? s.k2 : "–"), h("td.num", {}, s ? s.k3 : "–"), h("td.num", {}, s ? s.k4 : "–"),
      h("td.num", {}, s ? s.k5 : "–"),
      h("td", {}, s && s.fav_weapon ? weaponName(s.fav_weapon) : "–"),
      h("td", {}, toggle));
    tbody.append(tr);
    const detail = h("tr.detail", { hidden: !openAccounts.has(p.id) }, h("td", { colspan: PEOPLE_NCOLS }, accountsPanel(p)));
    tbody.append(detail);
    toggle.addEventListener("click", () => {
      const open = detail.hidden;
      detail.hidden = !open;
      toggle.setAttribute("aria-expanded", String(open));
      if (open) openAccounts.add(p.id); else openAccounts.delete(p.id);
    });
  }
}

/** Group chips plus an Edit button that opens a checkbox list in place. */
function groupCell(p) {
  const box = h("div.group-cell");
  const idle = () => {
    const edit = h("button.btn-quiet.btn-small", { type: "button", "aria-expanded": "false", "aria-label": `Edit groups for ${p.name}` }, "Edit");
    edit.addEventListener("click", open);
    clear(box,
      p.groups.length ? h("span.chips", {}, p.groups.map((g) => h("span.chip", {}, g.name))) : h("span.muted", {}, "–"),
      edit);
    return edit;
  };
  const open = () => {
    const err = h("p.status.error", { role: "alert", hidden: true });
    const list = h("div.checks", {}, state.groups.length ? groupCheckboxes(new Set(p.groups.map((g) => g.id))) :
      h("span.muted", {}, "No groups yet — use Add group below."));
    const save = h("button.btn.btn-small", { type: "submit" }, "Save");
    const cancel = h("button.btn-quiet.btn-small", { type: "button" }, "Cancel");
    const form = h("form.group-editor", { "aria-label": `Groups for ${p.name}` },
      h("div.editor-title", {}, `Groups for ${p.name}`), list, h("div.row", {}, save, cancel), err);
    cancel.addEventListener("click", () => idle().focus());
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      save.disabled = true;
      cancel.disabled = true;
      try {
        await api("PATCH", `/api/people/${p.id}`, { group_ids: checkedIds(list) });
        state.people = null;
        toast("Groups saved");
        renderPeople();
      } catch (ex) {
        save.disabled = false;
        cancel.disabled = false;
        err.hidden = false;
        err.textContent = ex.message;
      }
    });
    clear(box, form);
    const first = list.querySelector("input");
    (first || save).focus();
  };
  idle();
  return box;
}

function renderManageGroups() {
  const body = $("#manage-groups .body");
  if (!state.groups.length) {
    clear(body, h("p.muted", {}, "No groups yet. Use Add group to make one."));
    return;
  }
  clear(body, h("ul.group-list", {}, state.groups.map(manageGroupRow)));
}

function manageGroupRow(g) {
  const li = h("li.group-row");
  const err = h("p.status.error", { role: "alert", hidden: true });
  const fail = (e) => { err.hidden = false; err.textContent = e.message; };
  const idle = () => {
    err.hidden = true;
    const rename = h("button.btn-quiet.btn-small", { type: "button", "aria-label": `Rename ${g.name}` }, "Rename");
    const del = h("button.btn-quiet.btn-small.danger", { type: "button", "aria-label": `Delete ${g.name}` }, "Delete");
    rename.addEventListener("click", renaming);
    del.addEventListener("click", confirming);
    clear(li, h("span.who", {}, h("b", {}, g.name), h("small", {}, ` · ${g.members} ${g.members === 1 ? "person" : "people"}`)),
      h("span.row", {}, rename, del), err);
  };
  const renaming = () => {
    const input = h("input", { value: g.name, maxlength: 40, required: true, "aria-label": `New name for ${g.name}` });
    const cancel = h("button.btn-quiet.btn-small", { type: "button", onclick: idle }, "Cancel");
    const form = h("form.inline-form", {}, input, h("button.btn.btn-small", { type: "submit" }, "Save"), cancel);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const name = input.value.trim();
      if (!name) return;
      try {
        await api("PATCH", `/api/groups/${g.id}`, { name });
        state.people = null;
        toast("Group renamed");
        renderPeople();
      } catch (ex) { fail(ex); }
    });
    clear(li, form, err);
    input.focus();
    input.select();
  };
  const confirming = () => {
    const yes = h("button.btn.btn-small.btn-danger", { type: "button" }, "Yes, delete it");
    const no = h("button.btn-quiet.btn-small", { type: "button", onclick: idle }, "Cancel");
    yes.addEventListener("click", async () => {
      yes.disabled = true;
      no.disabled = true;
      try {
        await api("DELETE", `/api/groups/${g.id}`);
        state.people = null;
        toast("Group deleted");
        renderPeople();
      } catch (ex) { yes.disabled = false; no.disabled = false; fail(ex); }
    });
    clear(li, h("span.warn-text", {}, `Delete the group “${g.name}”? Its ${g.members} ${g.members === 1 ? "person stays" : "people stay"}, just without this group.`),
      h("span.row", {}, yes, no), err);
    no.focus();
  };
  idle();
  return li;
}

const WEAPONS = { ak47: "AK-47", m4a1_silencer: "M4A1-S", m4a1: "M4A4", awp: "AWP" };
const weaponName = (w) => WEAPONS[w] || w.charAt(0).toUpperCase() + w.slice(1);

function accountsPanel(p) {
  const input = h("input", { inputmode: "numeric", pattern: "\\d{17}", placeholder: "Steam ID (17 digits)", "aria-label": "Steam ID to link" });
  const linkForm = h("form.inline-form", {}, input, h("button.btn.btn-small", { type: "submit" }, "Link account"));
  linkForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const sid = input.value.trim();
    if (!/^\d{17}$/.test(sid)) { toast("A Steam ID is 17 digits.", true); return; }
    try {
      await api("POST", `/api/people/${p.id}/accounts`, { steam_id: sid });
      openAccounts.add(p.id);
      toast("Account linked");
      renderPeople();
    } catch (err) { toast(err.message, true); }
  });
  const rename = h("input", { value: p.name, "aria-label": "Name", maxlength: 80 });
  const renameForm = h("form.inline-form", {}, rename, h("button.btn-quiet.btn-small", { type: "submit" }, "Rename"));
  renameForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!rename.value.trim()) { toast("Name can't be empty.", true); return; }
    if (await patchPerson(p.id, { name: rename.value.trim() })) { toast("Renamed"); renderPeople(); }
  });
  const del = h("button.btn-quiet.btn-small.danger", { type: "button" }, "Remove person");
  del.addEventListener("click", async () => {
    if (!confirm(`Remove ${p.name}? Their Steam accounts and match history stay; they just won't be grouped as a person.`)) return;
    try {
      await api("DELETE", `/api/people/${p.id}`);
      openAccounts.delete(p.id);
      toast("Person removed");
      renderPeople();
    } catch (err) { toast(err.message, true); }
  });
  return h("div.accounts", {},
    h("div", {}, h("h3", {}, "Steam accounts"),
      p.accounts.length ? h("ul", {}, p.accounts.map((a) => h("li", {},
        steamLink(a.steam_url, a.last_seen_name || a.steam_id), h("span.sid", {}, a.steam_id)))) :
        h("p.muted", {}, "No accounts linked."),
      linkForm),
    h("div", {}, h("h3", {}, "Edit"), renameForm, h("div.inline-form", {}, del)));
}

async function patchPerson(id, body) {
  try {
    await api("PATCH", `/api/people/${id}`, body);
    state.people = null;
    return true;
  } catch (e) {
    toast(e.message, true);
    return false;
  }
}

async function renderRecurring() {
  const body = $("#recurring .body");
  let rows;
  try {
    rows = await api("GET", "/api/recurring");
  } catch (e) {
    clear(body, h("p.status.error", {}, e.message));
    return;
  }
  if (!rows.length) {
    clear(body, h("p.muted", {}, "Nobody new — everyone you play with regularly is already one of your people."));
    return;
  }
  const people = (state.people || []).slice().sort((a, b) => a.name.localeCompare(b.name));
  clear(body, rows.map((r) => {
    const name = r.name || r.steam_id;
    const addBtn = h("button.btn.btn-small", { type: "button" }, "Add as person");
    addBtn.addEventListener("click", async () => {
      addBtn.disabled = true;
      try {
        const { id } = await api("POST", "/api/people", { name, tracked: false });
        await api("POST", `/api/people/${id}/accounts`, { steam_id: r.steam_id });
        toast(`Added ${name}`);
        renderPeople();
      } catch (e) { addBtn.disabled = false; toast(e.message, true); }
    });
    const sel = h("select", { "aria-label": `Merge ${name} into` },
      h("option", { value: "" }, "Merge into…"), people.map((p) => h("option", { value: p.id }, p.name)));
    const mergeBtn = h("button.btn-quiet.btn-small", { type: "button", disabled: true }, "Merge");
    sel.addEventListener("change", () => { mergeBtn.disabled = !sel.value; });
    mergeBtn.addEventListener("click", async () => {
      const target = people.find((p) => String(p.id) === sel.value);
      if (!target) return;
      mergeBtn.disabled = true;
      try {
        await api("POST", `/api/people/${target.id}/accounts`, { steam_id: r.steam_id });
        toast(`Linked ${name} to ${target.name}`);
        renderPeople();
      } catch (e) { mergeBtn.disabled = false; toast(e.message, true); }
    });
    return h("div.recurring-row", {},
      h("div.who", {}, h("b", {}, steamLink(`https://steamcommunity.com/profiles/${r.steam_id}`, name)),
        h("small", {}, `On your team in ${r.count} matches · ${r.steam_id}`)),
      addBtn, sel, mergeBtn);
  }));
}

function initPeople() {
  const range = $("#people-range");
  range.addEventListener("change", () => renderPeople());
  range.addEventListener("reset", () => setTimeout(() => renderPeople(), 0));
  range.addEventListener("submit", (e) => e.preventDefault());
  const add = $("#add-person");
  add.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(add);
    const name = String(f.get("name") || "").trim();
    const sid = String(f.get("steam_id") || "").trim();
    if (!name) return;
    if (sid && !/^\d{17}$/.test(sid)) { toast("A Steam ID is 17 digits.", true); return; }
    try {
      const { id } = await api("POST", "/api/people", { name, group_ids: checkedIds($("#add-person-groups")), tracked: false });
      if (sid) await api("POST", `/api/people/${id}/accounts`, { steam_id: sid });
      add.reset();
      toast(`Added ${name}`);
      renderPeople();
    } catch (err) { toast(err.message, true); }
  });
  const groupBtn = $("#add-group-btn");
  const groupForm = $("#add-group-form");
  const groupStatus = $("#add-group-status");
  const groupName = $("input[name=name]", groupForm);
  const closeGroupForm = () => {
    groupForm.hidden = true;
    groupForm.reset();
    status(groupStatus, "");
    groupBtn.setAttribute("aria-expanded", "false");
  };
  groupBtn.addEventListener("click", () => {
    if (!groupForm.hidden) { closeGroupForm(); return; }
    groupForm.hidden = false;
    groupBtn.setAttribute("aria-expanded", "true");
    groupName.focus();
  });
  $("#add-group-cancel").addEventListener("click", () => { closeGroupForm(); groupBtn.focus(); });
  groupForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = groupName.value.trim();
    if (!name) return;
    try {
      await api("POST", "/api/groups", { name });
      closeGroupForm();
      toast(`Added group ${name}`);
      renderPeople();
    } catch (err) { status(groupStatus, err.message, true); }
  });
}

/** "Track as person" from Rivals: open People with the Add a person form filled in. */
export function prefillAddPerson(name, steamId) {
  location.hash = "#people";
  const add = $("#add-person");
  add.reset();
  const nameInput = $("input[name=name]", add);
  nameInput.value = name || "";
  $("input[name=steam_id]", add).value = steamId || "";
  // the People view is shown by the hashchange that follows
  setTimeout(() => { add.scrollIntoView({ block: "center" }); nameInput.focus(); }, 50);
  toast("Pick their groups if you like, then press Add person.");
}

// ---------- Stats ----------

async function renderStats() {
  const body = $("#view-stats .stats-panel .body");
  let rows;
  try {
    rows = await api("GET", "/api/stats/best-mates");
  } catch (e) {
    clear(body, h("p.status.error", {}, e.message));
    return;
  }
  rows = rows.filter((r) => r.games > 0);
  if (!rows.length) {
    clear(body, h("p.muted", {}, "Nobody yet — once you've played with your people, they show up here."));
    return;
  }
  // Equal counts share a rank: 1, 2, 2, 4.
  let rank = 0;
  clear(body, h("ol.mates", {}, rows.map((r, i) => {
    if (!i || r.games !== rows[i - 1].games) rank = i + 1;
    return h("li", {}, h("span.rank", {}, `${rank}.`), h("span.mate", {}, r.name),
      h("span.games", { "aria-label": `${r.games} games` }, `— ${r.games}`));
  })));
}

// ---------- Summary ----------

const sum = {
  sessions: [],
  session: null,
  matchIds: new Set(),
  players: [],          // [{id, name, played}] people on my team in the chosen matches
  total: 0,             // number of chosen matches
  ticked: new Set(),
  data: null,
  pseq: 0,             // request counters: ignore stale responses
  cseq: 0,
};

// Roast level: kept in this browser only.
const ROAST_KEY = "cs2t.roastLevel";
const ROAST_LEVELS = [["friendly", "Friendly teasing"], ["trash", "Trash talk"], ["off", "Off"]];

function loadRoastLevel() {
  try { return checkRoast(localStorage.getItem(ROAST_KEY)); } catch { return "trash"; }   // storage blocked
}
function checkRoast(v) { return ROAST_LEVELS.some(([k]) => k === v) ? v : "trash"; }
function saveRoastLevel(v) {
  try { localStorage.setItem(ROAST_KEY, v); } catch { /* storage blocked */ }
}

function sessionLabel(s) {
  const first = s.matches[0];
  const tally = ["W", "L", "D"].map((c) => [c, s.matches.filter((m) => m.result === c).length]).filter(([, n]) => n);
  return `${fmtDate(first.started_at, first.start_estimated)} — ${s.matches.length} match${s.matches.length === 1 ? "" : "es"}` +
    (tally.length ? ` (${tally.map(([c, n]) => `${n}${c}`).join(" ")})` : "");
}

async function renderSummaryView() {
  try {
    [sum.sessions, state.people] = await Promise.all([api("GET", "/api/sessions"), api("GET", "/api/people")]);
  } catch (e) {
    clear($("#summary-card"), h("p.status.error", { style: "padding:16px" }, e.message));
    return;
  }
  const sel = $("#session-select");
  clear(sel, sum.sessions.map((s, i) => h("option", { value: i }, sessionLabel(s))));
  if (!sum.sessions.length) {
    clear($("#summary-matches"), h("p.muted", {}, "No matches yet."));
    clear($("#summary-people"));
    clear($("#summary-card"), h("p.muted", { style: "padding:16px" }, "Import some matches first, then come back here."));
    return;
  }
  sel.value = "0";
  await pickSession(0);
}

async function pickSession(i) {
  sum.session = sum.sessions[i];
  sum.matchIds = new Set(sum.session.match_ids);
  const box = $("#summary-matches");
  clear(box, sum.session.matches.map((m) => {
    const cb = h("input", { type: "checkbox", checked: true, value: m.id });
    cb.addEventListener("change", () => {
      if (cb.checked) sum.matchIds.add(m.id); else sum.matchIds.delete(m.id);
      refreshPlayers();
    });
    return h("label", {}, cb, h("span", {}, h("b", {}, mapName(m.map)), " ", scoreText(m), " "), resultBadge(m.result),
      modeTag(gameModeOf(m)),
      h("span.sub", {}, fmtTime(m.started_at, m.start_estimated)));
  }));
  sum.ticked = new Set();
  await refreshPlayers(true);
}

/** Who played: /api/summary returns rows only for people on my team in the matches. */
async function refreshPlayers(reset = false) {
  const ids = [...sum.matchIds];
  const seq = ++sum.pseq;
  let players = [];
  let total = ids.length;
  if (ids.length && state.people.length) {
    try {
      const all = await api("POST", "/api/summary", { match_ids: ids, person_ids: state.people.map((p) => p.id), roast_level: "off" });
      players = all.rows.map((r) => ({ id: r.person_id, name: r.name, played: r.played }));
      total = all.total;
    } catch (e) { toast(e.message, true); }
  }
  if (seq !== sum.pseq) return;
  sum.total = total;
  const tracked = new Set(state.people.filter((p) => p.tracked).map((p) => p.id));
  const prev = sum.ticked;
  const known = new Set(sum.players.map((p) => p.id));
  sum.players = players.sort((a, b) => a.name.localeCompare(b.name));
  // players_in ∩ tracked are pre-ticked; manual choices survive changes to the match picks.
  sum.ticked = new Set(sum.players.filter((p) =>
    reset || !known.has(p.id) ? tracked.has(p.id) : prev.has(p.id)).map((p) => p.id));
  const box = $("#summary-people");
  clear(box);
  if (!sum.players.length) box.append(h("p.muted", {}, ids.length ? "None of your people played these matches." : "Pick at least one match."));
  const trackedPlayers = sum.players.filter((p) => tracked.has(p.id));
  const others = sum.players.filter((p) => !tracked.has(p.id));
  const row = (p) => {
    const cb = h("input", { type: "checkbox", checked: sum.ticked.has(p.id) });
    cb.addEventListener("change", () => {
      if (cb.checked) sum.ticked.add(p.id); else sum.ticked.delete(p.id);
      renderSummaryCard();
    });
    const played = fmtPlayed(p.played, sum.total);
    return h("label", {}, cb, h("span", {}, p.name),
      h("span.sub", { title: `Played ${played} of the chosen matches` }, played));
  };
  box.append(...trackedPlayers.map(row));
  if (others.length) box.append(h("div.divider", {}, trackedPlayers.length ? "Also played" : "Played (not tracked)"), ...others.map(row));
  await renderSummaryCard();
}

const sessionMatch = (id) => (sum.session ? sum.session.matches.find((x) => x.id === id) : null);

/** "Entry King" → "EK", "The Rat" → "R": the round badge on an award card. */
function awardCode(title) {
  const words = String(title).split(/\s+/).filter((w) => w && !/^the$/i.test(w));
  return words.slice(0, 2).map((w) => w.charAt(0).toUpperCase()).join("") || "?";
}

/** The "Awards from N of M matches" note, when some chosen matches have no event file. */
function awardsNote(data) {
  return data.awards.length && data.fun_matches < data.total ? `Awards from ${data.fun_matches} of ${data.total} matches` : "";
}


/** The info boxes under the awards: [title, lines], empty ones left out. */
function summaryBoxes(data) {
  return [
    ["Records broken tonight", [...(data.records_broken || []), ...(data.milestones || [])]],
    ["Duos & nemeses", data.duos || []],
    ["Seen again", data.seen_again || []],
  ].filter(([, lines]) => lines.length);
}

const pct = (v) => (v === null || v === undefined ? "–" : `${Math.round(v)}%`);

/** Scoreboard columns: [label, numeric, value(row, board)]. Shared by the page and the PNG. */
const BOARD_COLS = [
  ["Player", false, (r) => r.name],
  ["Played", true, (r, b, data) => fmtPlayed(r.played, data.total)],
  ["K / D / A", true, (r) => `${dash(r.kills)} / ${dash(r.deaths)} / ${dash(r.assists)}`],
  ["ADR", true, (r) => fmt0(r.adr)],
  ["HS%", true, (r, b) => pct(b.hs_pct)],
  ["KAST", true, (r, b) => pct(b.kast_pct)],
  ["Opening W–L", true, (r, b) => (b.opening_w === null || b.opening_w === undefined ? "–" : `${b.opening_w}–${b.opening_l}`)],
  ["Clutches", true, (r, b) => dash(b.clutches_won)],
  ["Blinded", true, (r, b) => dash(b.blinded)],
  ["Rating", true, (r, b) => fmt2(b.rating)],
  ["Role tonight", false, (r, b) => dash(b.role)],
  ["Fav weapon", false, (r) => favText(r)],
  ["2K", true, (r) => dash(r.k2)],
  ["3K", true, (r) => dash(r.k3)],
  ["4K", true, (r) => dash(r.k4)],
  ["Aces", true, (r) => dash(r.k5)],
];

const boardOf = (data, r) => (data.scoreboard && data.scoreboard[String(r.person_id)]) || {};
const boardValues = (data, r) => BOARD_COLS.map(([, , get]) => get(r, boardOf(data, r), data));

function summaryHeadMeta(data) {
  const ms = data.matches;
  const tally = ["W", "L", "D"].map((c) => [c, ms.filter((m) => m.result === c).length]).filter(([, n]) => n);
  return `${ms.length} match${ms.length === 1 ? "" : "es"}` +
    (tally.length ? ` · ${tally.map(([c, n]) => `${n}${c}`).join(" ")}` : "") +
    (data.rows.length ? ` · ${data.rows.map((r) => r.name).join(", ")}` : "");
}

async function renderSummaryCard() {
  const card = $("#summary-card");
  const seq = ++sum.cseq;
  const ids = [...sum.matchIds];
  if (!ids.length) {
    sum.data = null;
    clear(card, h("p.muted", { style: "padding:16px" }, "Pick at least one match."));
    updateSummaryButtons();
    return;
  }
  let data;
  try {
    data = await api("POST", "/api/summary", { match_ids: ids, person_ids: [...sum.ticked], roast_level: loadRoastLevel() });
  } catch (e) {
    clear(card, h("p.status.error", { style: "padding:16px" }, e.message));
    return;
  }
  if (seq !== sum.cseq) return;
  sum.data = data;
  $("#summary-text").value = data.text;
  const note = awardsNote(data);
  const boxes = summaryBoxes(data);
  clear(card,
    h("div.card-head", {},
      h("div", {}, h("h2", {}, `CS2 session — ${summaryDay(data)}`), h("p", {}, summaryHeadMeta(data))),
      h("div.chips-row", { "aria-label": "Match results" }, data.matches.map((m) => {
        const sm = sessionMatch(m.id);
        return h(`span.mres.mres-${RESULT_WORD[m.result] ? m.result : "D"}`, {},
          `${m.map} ${m.score.replace("-", "–")} ${m.result}`, modeTag(gameModeOf(sm)));
      }))),
    h("div.card-body", {},
      data.roasts.length ? h("section.roast", { "aria-label": "Roast of the night" },
        h("span.roast-icon", { "aria-hidden": "true" }),
        h("div", {}, h("div.roast-label", {}, "Roast of the night"), data.roasts.map((t) => h("p", {}, t)))) : null,
      data.awards.length ? h("section.awards", {},
        h("div.section-head", {}, h("h3", {}, "Awards of the night"), note ? h("span.note", {}, note) : null),
        h("div.award-grid", {}, data.awards.map((a) => h(`article.award.award-${a.kind === "shame" ? "shame" : "skill"}`, {},
          h("div.award-top", {}, h("span.badge", { "aria-hidden": "true" }, awardCode(a.title)), h("h4", {}, a.title)),
          h("div.award-who", {}, a.name),
          h("div.award-value", {}, a.value_line),
          h("div.award-blurb", {}, a.blurb))))) :
        (data.rows.length && data.fun_matches === 0 ? h("p.muted.small", {}, "No awards: none of these matches has a demo with events.") : null),
      boxes.length ? h("div.info-boxes", {}, boxes.map(([title, lines]) =>
        h("section.info-box", {}, h("h3", {}, title), h("ul", {}, lines.map((l) => h("li", {}, l)))))) : null,
      data.rows.length ? h("section.scoreboard", {}, h("h3", {}, "Scoreboard"),
        h("div.scroll-x", {}, h("table.data.summary-table", {},
          h("thead", {}, h("tr", {}, BOARD_COLS.map(([label, num], i) =>
            h(`th${num ? ".num" : ""}${i === 10 ? ".role" : ""}`, { scope: "col" }, label)))),
          h("tbody", {}, data.rows.map((r) => h("tr", {}, boardValues(data, r).map((v, i) => {
            if (i === 0) return h("th.name", { scope: "row" }, v);
            const cls = i === 1 ? ".num.played" : i === 9 ? ".num.rating" : i === 10 ? ".role" : i === 11 ? ".fav" : BOARD_COLS[i][1] ? ".num" : "";
            return h(`td${cls}`, {}, v);
          }))))))) :
        h("p.muted", {}, "Tick some people to add their stats."),
      lineupsGrid(data),
      data.best.length ? h("div.best", {}, h("h3", {}, "Best of the night"),
        h("ul", {}, data.best.map((b) => h("li", {}, b)))) : null));
  updateSummaryButtons();
}

/** "AK-47 (38)": favourite weapon and its kills in the chosen matches. */
function favText(r) {
  if (!r.fav_weapon) return "–";
  return r.fav_weapon_kills ? `${weaponName(r.fav_weapon)} (${r.fav_weapon_kills})` : weaponName(r.fav_weapon);
}

const LINEUP_LEGEND = "Blue = played and won · orange = played and lost · ◐ = left or joined part-way (counts as ½) · – = didn't play";
const RESULT_WORD = { W: "won", L: "lost", D: "drew" };

function lineupTime(l) {
  const m = sessionMatch(l.match_id);
  return fmtTime(l.started_at, m ? m.start_estimated : !l.started_at);
}

function lineupMode(l) {
  const m = sessionMatch(l.match_id);
  return MODE_TAG[gameModeOf(m)] || "";
}

/** One cell of the lineups grid: a dot coloured by result, ◐ for part of the match, – when absent. */
function lineupCell(kind, result) {
  const res = RESULT_WORD[result] ? result : "D";
  const word = RESULT_WORD[result] || "result unknown";
  if (kind === "full") return h(`span.dot.dot-${res}`, { role: "img", "aria-label": `Played, ${word}`, title: `Played, ${word}` });
  if (kind === "part") {
    // Drawn as a half-filled dot (the ◐ glyph is missing from many fonts).
    return h(`span.dot.part.dot-${res}`, { role: "img", "aria-label": `Played part of it, ${word}`, title: `Left or joined part-way, ${word}` });
  }
  return h("span.absent", { role: "img", "aria-label": "Didn't play", title: "Didn't play" }, "–");
}

function lineupsGrid(data) {
  const ls = data.lineups || [];
  if (!ls.length) return null;
  return h("section.lineups", { "aria-label": "Lineups" },
    h("h3", {}, "Lineups"),
    h("div.scroll-x", {}, h("table.lineups-grid", {},
      h("thead", {}, h("tr", {}, h("th.who", { scope: "col" }, h("span.sr-only", {}, "Player")),
        ls.map((l) => {
          const sm = sessionMatch(l.match_id);
          return h("th.match", { scope: "col" },
            h("span.lmap", {}, l.map),
            h("span.lscore", {}, l.score, " ", resultBadge(l.result)),
            h("span.ltime", {}, lineupTime(l) || " "),
            gameModeOf(sm) ? h("span.lmode", {}, modeTag(gameModeOf(sm))) : null);
        }),
        h("th.played", { scope: "col" }, "Played"))),
      h("tbody", {},
        data.rows.map((r) => h("tr", {},
          h("th.who", { scope: "row" }, r.name),
          ls.map((l) => h("td", {}, lineupCell(l.cells[String(r.person_id)] || "none", l.result))),
          h("td.played", {}, fmtPlayed(r.played, data.total)))),
        h("tr.others", {},
          h("th.who", { scope: "row" }, "Others on our team"),
          ls.map((l) => h("td", {}, l.others ? `+${l.others}` : "–")),
          h("td.played"))))),
    h("p.legend", {}, LINEUP_LEGEND.split("◐").map((t, i) => [
      i ? h("span.dot.part.dot-D.legend-dot", { role: "img", "aria-label": "◐ half dot" }) : null, t])));
}

function summaryDay(data) {
  const d = data.matches.find((m) => m.date);
  if (!d) return "?";
  return DATE_UTC_FMT.format(new Date(`${d.date}T00:00:00Z`));
}

function updateSummaryButtons() {
  $("#copy-text").disabled = !sum.data;
  $("#save-image").disabled = !sum.data;
}

function summaryStatus(msg, isError = false) {
  const s = $("#summary-status");
  s.textContent = msg;
  s.classList.toggle("error", isError);
}

async function copySummaryText() {
  if (!sum.data) return;
  const ta = $("#summary-text");
  try {
    if (!navigator.clipboard || !window.isSecureContext) throw new Error("no clipboard");
    await navigator.clipboard.writeText(sum.data.text);
    ta.hidden = true;
    summaryStatus("Copied — paste it into the chat.");
  } catch {
    ta.hidden = false;
    ta.focus();
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { /* not supported */ }
    summaryStatus(ok ? "Copied — paste it into the chat." : "Text selected below — press Ctrl+C to copy.");
  }
}

// ---- Summary image (canvas) ----

const INK = "#2b1d0e", SAND = "#efe2c4", PAPER = "#f7efdc", T = "#d9822b", T_DEEP = "#b5651a", CT = "#2f5f94", CT_DEEP = "#244a75", INK2 = "#6a5536";
const CARD = "#f6ecd6", CARD_EDGE = "#d6c29b", BOX = "#f3dfbf", BLURB = "#4a3820";
const COND = '"Barlow Condensed", "Arial Narrow", sans-serif';
const BODY = '"Barlow", "Segoe UI", sans-serif';

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function fitText(ctx, text, max) {
  if (ctx.measureText(text).width <= max) return text;
  let t = text;
  while (t.length > 1 && ctx.measureText(`${t}…`).width > max) t = t.slice(0, -1);
  return `${t}…`;
}

/** Name in the PNG: shrink the font before cutting, and never cut below 12 characters. */
function fitName(ctx, text, max, size, weight = 600) {
  for (let s = size; s >= 13; s -= 1) {
    ctx.font = `${weight} ${s}px ${BODY}`;
    if (ctx.measureText(text).width <= max) return text;
  }
  let t = text;
  while (t.length > 12 && ctx.measureText(`${t}…`).width > max) t = t.slice(0, -1);
  return t === text ? text : `${t}…`;
}

/** Word-wrap for the canvas (in the current font). A single over-long word stays whole. */
function wrapText(ctx, text, max) {
  const lines = [];
  let cur = "";
  for (const w of String(text).split(/\s+/).filter(Boolean)) {
    const t = cur ? `${cur} ${w}` : w;
    if (cur && ctx.measureText(t).width > max) { lines.push(cur); cur = w; } else cur = t;
  }
  if (cur) lines.push(cur);
  return lines.length ? lines : [""];
}

function drawDot(ctx, cx, cy, r, kind, result) {
  const fill = result === "W" ? CT : result === "L" ? T : INK2;
  const edge = result === "W" ? CT_DEEP : result === "L" ? T_DEEP : INK;
  if (kind === "none") {
    ctx.fillStyle = INK2;
    ctx.font = `600 18px ${BODY}`;
    ctx.textAlign = "center";
    ctx.fillText("–", cx, cy + 6);
    ctx.textAlign = "left";
    return;
  }
  ctx.lineWidth = 2;
  ctx.strokeStyle = edge;
  if (kind === "part") {
    // ◐: left half filled, right half open
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.fillStyle = PAPER; ctx.fill();
    ctx.beginPath(); ctx.arc(cx, cy, r, Math.PI / 2, Math.PI * 1.5); ctx.closePath(); ctx.fillStyle = fill; ctx.fill();
  } else {
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.fillStyle = fill; ctx.fill();
  }
  ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke();
  ctx.lineWidth = 1;
}

const PNG_PAD = 40;
const PNG_NAME_W = 190, PNG_PLAYED_W = 80, PNG_MIN_COL = 64;
/** Scoreboard column widths in the PNG (same order as BOARD_COLS); the Player column takes the rest. */
const PNG_BOARD_W = [0, 58, 106, 50, 54, 56, 86, 68, 62, 58, 96, 130, 36, 36, 36, 44];

function summaryImageWidth(data) {
  const ls = data.lineups || [];
  // The grid gets wider than the page only for very long sessions.
  return Math.max(1280, PNG_PAD * 2 + 40 + PNG_NAME_W + PNG_PLAYED_W + ls.length * PNG_MIN_COL);
}

/** Paints the whole summary image from the top and returns its height. Called twice: once
 *  on a scratch canvas to measure, then on the real one with the measured height H. */
function paintSummary(ctx, data, W, H) {
  const PAD = PNG_PAD;
  const inner = W - PAD * 2;
  const ms = data.matches;
  const ls = data.lineups || [];
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "left";

  // ground with planning grid
  ctx.fillStyle = SAND;
  ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = "rgba(43,29,14,0.05)";
  ctx.lineWidth = 1;
  for (let x = 0; x <= W; x += 32) { ctx.beginPath(); ctx.moveTo(x + 0.5, 0); ctx.lineTo(x + 0.5, H); ctx.stroke(); }
  for (let y = 0; y <= H; y += 32) { ctx.beginPath(); ctx.moveTo(0, y + 0.5); ctx.lineTo(W, y + 0.5); ctx.stroke(); }

  // header band
  const headH = 112;
  ctx.fillStyle = INK;
  ctx.fillRect(0, 0, W, headH);
  ctx.fillStyle = T;
  ctx.fillRect(0, headH, W, 6);
  ctx.save();
  ctx.translate(PAD, 34);
  ctx.transform(1, 0, -0.25, 1, 0, 0);
  ctx.fillStyle = T; ctx.fillRect(4, 0, 12, 26);
  ctx.fillStyle = CT; ctx.fillRect(20, 0, 12, 26);
  ctx.restore();
  ctx.fillStyle = SAND;
  ctx.font = `700 40px ${COND}`;
  ctx.fillText(fitText(ctx, `CS2 session — ${summaryDay(data)}`, inner - 48), PAD + 48, 60);
  ctx.font = `400 18px ${BODY}`;
  ctx.globalAlpha = 0.8;
  ctx.fillText(fitText(ctx, summaryHeadMeta(data), inner - 48), PAD + 48, 90);
  ctx.globalAlpha = 1;

  let y = headH + 26;

  // match chips
  ctx.font = `700 16px ${COND}`;
  let cx = PAD;
  for (const m of ms) {
    const label = `${m.map} ${m.score.replace("-", "–")} ${m.result}`;
    const w = ctx.measureText(label).width + 18;
    if (cx > PAD && cx + w > PAD + inner) { cx = PAD; y += 34; }
    roundRect(ctx, cx, y, w, 26, 5);
    ctx.fillStyle = m.result === "W" ? CT : m.result === "L" ? T : INK2;
    ctx.fill();
    ctx.fillStyle = m.result === "L" ? INK : "#ffffff";
    ctx.fillText(label, cx + 9, y + 19);
    cx += w + 8;
  }
  y += 26 + 24;

  // roast of the night
  if (data.roasts.length) {
    ctx.font = `400 19px ${BODY}`;
    const textX = PAD + 64, textW = inner - 64 - 24;
    const paras = data.roasts.map((r) => wrapText(ctx, r, textW));
    const nLines = paras.reduce((n, p) => n + p.length, 0);
    const boxH = 18 + 20 + nLines * 27 + (paras.length - 1) * 6 + 16;
    roundRect(ctx, PAD, y, inner, boxH, 10);
    ctx.fillStyle = INK; ctx.fill();
    // speech bubble
    ctx.strokeStyle = T; ctx.lineWidth = 2.2;
    roundRect(ctx, PAD + 22, y + 20, 26, 20, 3); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(PAD + 27, y + 40); ctx.lineTo(PAD + 24, y + 48); ctx.lineTo(PAD + 34, y + 40); ctx.stroke();
    ctx.lineWidth = 1;
    ctx.fillStyle = T;
    ctx.font = `600 15px ${COND}`;
    ctx.fillText("ROAST OF THE NIGHT", textX, y + 30);
    ctx.fillStyle = SAND;
    ctx.font = `400 19px ${BODY}`;
    let ty = y + 38 + 20;
    paras.forEach((lines) => {
      lines.forEach((l) => { ctx.fillText(l, textX, ty); ty += 27; });
      ty += 6;
    });
    y += boxH + 24;
  }

  // awards of the night
  if (data.awards.length) {
    ctx.fillStyle = INK;
    ctx.font = `700 25px ${COND}`;
    ctx.fillText("Awards of the night", PAD, y + 22);
    const note = awardsNote(data);
    if (note) {
      ctx.textAlign = "right";
      ctx.fillStyle = INK2;
      ctx.font = `400 15px ${BODY}`;
      ctx.fillText(note, PAD + inner, y + 22);
      ctx.textAlign = "left";
    }
    y += 36;
    const gap = 12, perRow = 4;
    const cw = (inner - gap * (perRow - 1)) / perRow;
    const tw = cw - 28;
    const cards = data.awards.map((a) => {
      ctx.font = `700 19px ${COND}`;
      const title = wrapText(ctx, a.title, tw - 48).slice(0, 2);
      ctx.font = `600 17px ${BODY}`;
      const who = wrapText(ctx, a.name, tw);
      ctx.font = `700 25px ${COND}`;
      const value = wrapText(ctx, a.value_line, tw);
      ctx.font = `400 13px ${BODY}`;
      const blurb = wrapText(ctx, a.blurb || "", tw);
      const hgt = 14 + Math.max(38, title.length * 21) + 10 + who.length * 22 + 4 + value.length * 26 + 6 + blurb.length * 17 + 12;
      return { a, title, who, value, blurb, hgt: Math.max(150, hgt) };
    });
    for (let i = 0; i < cards.length; i += perRow) {
      const row = cards.slice(i, i + perRow);
      const rowH = Math.max(...row.map((c) => c.hgt));
      row.forEach((c, j) => {
        const x = PAD + j * (cw + gap);
        roundRect(ctx, x, y, cw, rowH, 10);
        ctx.fillStyle = CARD; ctx.fill();
        ctx.strokeStyle = CARD_EDGE; ctx.stroke();
        const shame = c.a.kind === "shame";
        ctx.beginPath(); ctx.arc(x + 14 + 19, y + 14 + 19, 19, 0, Math.PI * 2);
        ctx.fillStyle = shame ? T : CT; ctx.fill();
        ctx.fillStyle = shame ? INK : "#ffffff";
        ctx.font = `700 15px ${COND}`;
        ctx.textAlign = "center";
        ctx.fillText(awardCode(c.a.title), x + 33, y + 38);
        ctx.textAlign = "left";
        ctx.fillStyle = INK;
        ctx.font = `700 19px ${COND}`;
        const th = c.title.length * 21;
        const titleTop = y + 14 + Math.max(0, (38 - th) / 2);
        c.title.forEach((l, k) => ctx.fillText(l, x + 14 + 48, titleTop + 16 + k * 21));
        let cy = y + 14 + Math.max(38, th) + 10;
        c.who.forEach((l) => {
          ctx.fillStyle = INK;
          ctx.fillText(fitName(ctx, l, tw, 17), x + 14, cy + 16);
          cy += 22;
        });
        cy += 4;
        ctx.fillStyle = INK;
        ctx.font = `700 25px ${COND}`;
        c.value.forEach((l) => { ctx.fillText(l, x + 14, cy + 21); cy += 26; });
        cy += 6;
        ctx.fillStyle = BLURB;
        ctx.font = `400 13px ${BODY}`;
        c.blurb.forEach((l) => { ctx.fillText(l, x + 14, cy + 12); cy += 17; });
      });
      y += rowH + gap;
    }
    y += 12;
  }

  // records / duos / seen again: two boxes per row (one alone takes the full width)
  const boxes = summaryBoxes(data);
  for (let i = 0; i < boxes.length; i += 2) {
    const row = boxes.slice(i, i + 2);
    const bw = row.length === 2 ? (inner - 16) / 2 : inner;
    ctx.font = `400 16px ${BODY}`;
    const laid = row.map(([title, lines]) => ({ title, lines: lines.map((l) => wrapText(ctx, l, bw - 36 - 14)) }));
    const rowH = Math.max(...laid.map((b) => 14 + 30 + b.lines.reduce((n, l) => n + l.length, 0) * 24 + 12));
    laid.forEach((b, j) => {
      const x = PAD + j * (bw + 16);
      roundRect(ctx, x, y, bw, rowH, 10);
      ctx.fillStyle = BOX; ctx.fill();
      ctx.fillStyle = INK;
      ctx.font = `700 21px ${COND}`;
      ctx.fillText(b.title, x + 18, y + 34);
      let ly = y + 44;
      ctx.font = `400 16px ${BODY}`;
      b.lines.forEach((wrapped) => {
        ctx.fillStyle = T_DEEP;
        ctx.beginPath(); ctx.arc(x + 24, ly + 12, 3, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = INK;
        wrapped.forEach((l) => { ctx.fillText(l, x + 36, ly + 18); ly += 24; });
      });
    });
    y += rowH + 20;
  }

  // scoreboard
  ctx.fillStyle = INK;
  ctx.font = `700 21px ${COND}`;
  ctx.fillText("Scoreboard", PAD, y + 20);
  y += 32;
  const rowH = 38;
  const tableH = 38 + Math.max(1, data.rows.length) * rowH + 6;
  roundRect(ctx, PAD, y, inner, tableH, 10);
  ctx.fillStyle = PAPER; ctx.fill();
  ctx.strokeStyle = "rgba(43,29,14,0.3)"; ctx.stroke();
  const tx = PAD + 18, tw = inner - 36;
  const widths = PNG_BOARD_W.slice();
  widths[0] = tw - widths.reduce((a, b) => a + b, 0);
  const starts = widths.map((_, i) => tx + widths.slice(0, i).reduce((a, b) => a + b, 0));
  const textX = (i) => (BOARD_COLS[i][1] ? starts[i] + widths[i] - 4 : starts[i] + (i >= 10 ? 14 : 0));
  ctx.font = `600 16px ${COND}`;
  ctx.fillStyle = INK2;
  BOARD_COLS.forEach(([label, num], i) => {
    ctx.textAlign = num ? "right" : "left";
    ctx.fillText(fitText(ctx, label, widths[i] - (i >= 10 ? 14 : 4)), textX(i), y + 26);
  });
  ctx.fillStyle = INK;
  ctx.fillRect(tx - 4, y + 36, tw + 8, 2);
  let ry = y + 38;
  if (!data.rows.length) {
    ctx.textAlign = "left"; ctx.font = `400 17px ${BODY}`; ctx.fillStyle = INK2;
    ctx.fillText("No players ticked.", tx, ry + 26);
  }
  data.rows.forEach((r, ri) => {
    if (ri) { ctx.fillStyle = "rgba(43,29,14,0.15)"; ctx.fillRect(tx - 4, ry, tw + 8, 1); }
    boardValues(data, r).forEach((v, i) => {
      ctx.fillStyle = INK;
      ctx.textAlign = BOARD_COLS[i][1] ? "right" : "left";
      if (i === 0) {
        ctx.fillText(fitName(ctx, String(v), widths[0] - 8, 17), textX(0), ry + 25);
        return;
      }
      ctx.font = `${i === 9 ? 600 : 400} 16px ${BODY}`;
      ctx.fillText(fitText(ctx, String(v), widths[i] - (i >= 10 ? 14 : 4)), textX(i), ry + 25);
    });
    ry += rowH;
  });
  ctx.textAlign = "left";
  y += tableH + 20;

  // lineups grid
  if (ls.length) {
    const gridHeadH = 70, gridRowH = 34;
    ctx.font = `400 15px ${BODY}`;
    const legendLines = ctx.measureText(LINEUP_LEGEND).width > inner - 40 ? 2 : 1;
    const gridH = 44 + gridHeadH + (data.rows.length + 1) * gridRowH + 14 + legendLines * 20 + 16;
    roundRect(ctx, PAD, y, inner, gridH, 10);
    ctx.fillStyle = PAPER; ctx.fill();
    ctx.strokeStyle = "rgba(43,29,14,0.3)"; ctx.stroke();
    ctx.fillStyle = INK;
    ctx.font = `700 21px ${COND}`;
    ctx.fillText("Lineups", PAD + 20, y + 30);
    const gx = PAD + 20, gw = inner - 40;
    const colW = (gw - PNG_NAME_W - PNG_PLAYED_W) / ls.length;
    const colX = (i) => gx + PNG_NAME_W + colW * i + colW / 2;
    const playedX = gx + gw;
    let gy = y + 44;
    ctx.textAlign = "center";
    ls.forEach((l, i) => {
      const c = colX(i);
      ctx.fillStyle = INK;
      ctx.font = `700 18px ${COND}`;
      ctx.fillText(fitText(ctx, l.map, colW - 6), c, gy + 18);
      ctx.font = `600 15px ${BODY}`;
      ctx.fillText(fitText(ctx, `${l.score} ${l.result || "?"}`, colW - 6), c, gy + 38);
      ctx.fillStyle = INK2;
      ctx.font = `400 14px ${BODY}`;
      const when = [lineupTime(l), lineupMode(l)].filter(Boolean).join(" · ");
      ctx.fillText(fitText(ctx, when, colW - 6), c, gy + 57);
    });
    ctx.textAlign = "right";
    ctx.fillStyle = INK2;
    ctx.font = `600 17px ${COND}`;
    ctx.fillText("Played", playedX, gy + 38);
    ctx.fillStyle = INK;
    ctx.fillRect(gx, gy + gridHeadH - 4, gw, 2);
    gy += gridHeadH;
    data.rows.forEach((r, ri) => {
      const ly = gy + ri * gridRowH;
      if (ri) { ctx.fillStyle = "rgba(43,29,14,0.12)"; ctx.fillRect(gx, ly, gw, 1); }
      ctx.textAlign = "left";
      ctx.fillStyle = INK;
      ctx.fillText(fitName(ctx, r.name, PNG_NAME_W - 10, 17), gx, ly + 23);
      ls.forEach((l, i) => drawDot(ctx, colX(i), ly + gridRowH / 2, 9, l.cells[String(r.person_id)] || "none", l.result));
      ctx.textAlign = "right";
      ctx.fillStyle = INK;
      ctx.font = `600 17px ${BODY}`;
      ctx.fillText(fmtPlayed(r.played, data.total), playedX, ly + 23);
    });
    // others row, dashed top
    const oy = gy + data.rows.length * gridRowH;
    ctx.save();
    ctx.setLineDash([5, 4]);
    ctx.strokeStyle = "rgba(43,29,14,0.45)";
    ctx.beginPath(); ctx.moveTo(gx, oy + 0.5); ctx.lineTo(gx + gw, oy + 0.5); ctx.stroke();
    ctx.restore();
    ctx.textAlign = "left";
    ctx.fillStyle = INK2;
    ctx.font = `italic 400 16px ${BODY}`;
    ctx.fillText("Others on our team", gx, oy + 23);
    ctx.textAlign = "center";
    ctx.font = `600 16px ${BODY}`;
    ls.forEach((l, i) => ctx.fillText(l.others ? `+${l.others}` : "–", colX(i), oy + 23));
    // legend
    ctx.textAlign = "left";
    const legY = oy + gridRowH + 18;
    const legendLine = (text, x, yy) => {
      text.split("◐").forEach((t, i) => {
        if (i) { drawDot(ctx, x + 7, yy - 5, 6, "part", "D"); x += 16; }
        ctx.fillStyle = INK2;
        ctx.font = `400 15px ${BODY}`;
        ctx.textAlign = "left";
        ctx.fillText(t, x, yy);
        x += ctx.measureText(t).width;
      });
    };
    if (legendLines === 1) legendLine(LINEUP_LEGEND, gx, legY);
    else {
      const parts = LINEUP_LEGEND.split(" · ");
      legendLine(parts.slice(0, 2).join(" · "), gx, legY);
      legendLine(parts.slice(2).join(" · "), gx, legY + 20);
    }
    y += gridH + 20;
  }

  // best of the night
  if (data.best.length) {
    const bestH = 34 + data.best.length * 28 + 16;
    roundRect(ctx, PAD, y, inner, bestH, 10);
    ctx.fillStyle = "rgba(217,130,43,0.2)"; ctx.fill();
    ctx.fillStyle = INK;
    ctx.font = `700 21px ${COND}`;
    ctx.fillText("Best of the night", PAD + 20, y + 30);
    data.best.forEach((b, i) => {
      ctx.fillStyle = T_DEEP;
      ctx.fillRect(PAD + 22, y + 46 + i * 28, 7, 7);
      ctx.fillStyle = INK;
      ctx.font = `400 18px ${BODY}`;
      // long lines (Best K/D with its note) shrink before they are cut
      let size = 18;
      while (size > 14 && ctx.measureText(b).width > inner - 60) { size -= 1; ctx.font = `400 ${size}px ${BODY}`; }
      ctx.fillText(fitText(ctx, b, inner - 60), PAD + 40, y + 54 + i * 28);
    });
    y += bestH + 20;
  }

  y += 24;
  ctx.fillStyle = INK2;
  ctx.font = `600 15px ${COND}`;
  ctx.textAlign = "left";
  ctx.fillText("CS2 Squad Tracker · Dust2 crew", PAD, y);
  return y + 24;
}

async function drawSummaryCanvas(data) {
  await Promise.all([
    document.fonts.load(`700 40px ${COND}`), document.fonts.load(`600 20px ${COND}`),
    document.fonts.load(`400 18px ${BODY}`), document.fonts.load(`600 18px ${BODY}`),
  ]).catch(() => {});
  await document.fonts.ready;
  const scale = 2;
  const W = summaryImageWidth(data);
  const H = Math.ceil(paintSummary(document.createElement("canvas").getContext("2d"), data, W, 0));
  const canvas = document.createElement("canvas");
  canvas.width = W * scale;
  canvas.height = H * scale;
  const ctx = canvas.getContext("2d");
  ctx.scale(scale, scale);
  paintSummary(ctx, data, W, H);
  return canvas;
}

function pad2(n) { return String(n).padStart(2, "0"); }

function summaryFileName(data) {
  const d = data.matches.find((m) => m.date);
  let day = d ? d.date : null;
  if (!day) { const t = new Date(); day = `${t.getFullYear()}-${pad2(t.getMonth() + 1)}-${pad2(t.getDate())}`; }
  return `cs2-summary-${day}.png`;
}

async function saveSummaryImage() {
  if (!sum.data) return;
  const btn = $("#save-image");
  btn.disabled = true;
  try {
    const canvas = await drawSummaryCanvas(sum.data);
    const blob = await new Promise((res) => canvas.toBlob(res, "image/png"));
    if (!blob) throw new Error("Couldn't draw the image.");
    const url = URL.createObjectURL(blob);
    const a = h("a", { href: url, download: summaryFileName(sum.data), hidden: true });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    summaryStatus(`Saved ${summaryFileName(sum.data)}`);
  } catch (e) {
    summaryStatus(e.message, true);
  } finally {
    btn.disabled = false;
  }
}

function initSummary() {
  const roast = $("#roast-select");
  clear(roast, ROAST_LEVELS.map(([k, label]) => h("option", { value: k }, label)));
  roast.value = loadRoastLevel();
  roast.addEventListener("change", () => {
    saveRoastLevel(checkRoast(roast.value));
    if (sum.matchIds.size) renderSummaryCard();
  });
  $("#session-select").addEventListener("change", (e) => pickSession(Number(e.target.value)));
  $("#copy-text").addEventListener("click", copySummaryText);
  $("#save-image").addEventListener("click", saveSummaryImage);
  updateSummaryButtons();
}

// ---------- Settings ----------

async function renderSettings() {
  const form = $("#settings-form");
  $("#settings-status").textContent = "";
  try {
    const [s, about] = await Promise.all([loadSettings(), api("GET", "/api/about")]);
    form.me_steam_id.value = s.me_steam_id || "";
    form.replays_dir.value = s.replays_dir || "";
    renderHistorySettings(s);
    $("#rivals-min-input").value = String(s.rivals_min_games ?? 2);
    $("#about-data-dir").textContent = about.data_dir || "–";
    $("#about-parser").textContent = about.parser_version;
    $("#about-app").textContent = about.app_version;
    renderReparseBanners(about);
  } catch (e) {
    status($("#settings-status"), e.message, true);
  }
}

/** The "Match history access" and "Demos" boxes. Secrets come back masked only. */
function renderHistorySettings(s) {
  const form = $("#history-form");
  for (const k of ["steam_api_key", "match_auth_code"]) {
    form[k].value = "";
    const masked = s[`${k}_masked`];
    form[k].placeholder = masked ? "Type a new one to replace it" : (k === "match_auth_code" ? "AAAA-AAAAA-AAAA" : "");
    const line = $(`#${k}-saved`);
    line.hidden = !masked;
    if (masked) {
      clear(line, h("span", {}, `Saved: ${masked}`), " ",
        h("button.btn-quiet.btn-small", { type: "button", onclick: () => saveHistory({ [k]: "" }, "Removed.") }, "Remove"));
    }
  }
  form.start_share_code.value = "";
  const last = $("#last-share-code");
  last.hidden = !s.last_share_code;
  last.textContent = s.last_share_code ? `Last known match: ${s.last_share_code}` : "";
  $("#pending-count").textContent = `Pending matches: ${s.pending_count || 0}`;
  $("#retention-select").value = s.demo_retention_days ? String(s.demo_retention_days) : "";
  $("#auto-update-check").checked = !!s.auto_update;
  const d = s.disk || { demos: 0, demo_bytes: 0, bundles: 0, bundle_bytes: 0 };
  $("#disk-summary").textContent = `Demos on disk: ${d.demos} (${fmtGB(d.demo_bytes)}) · Event files: ${d.bundles} (${fmtMB(d.bundle_bytes)})`;
}

/** The typed (unsaved) history values; empty ones are left out so the saved value stays. */
function typedHistory() {
  const form = $("#history-form");
  const body = {};
  for (const k of ["steam_api_key", "match_auth_code", "start_share_code"]) {
    const v = form[k].value.trim();
    if (v) body[k] = v;
  }
  return body;
}

async function saveHistory(body, okMsg = "Saved.") {
  const st = $("#history-status");
  try {
    state.settings = await api("PUT", "/api/settings", body);
    renderHistorySettings(state.settings);
    status(st, okMsg);
  } catch (err) { status(st, err.message, true); }
}

async function saveDemoSetting(body) {
  const st = $("#demos-status");
  try {
    state.settings = await api("PUT", "/api/settings", body);
    renderHistorySettings(state.settings);
    status(st, "Saved.");
  } catch (err) { status(st, err.message, true); }
}

export function status(el, msg, isError = false) {
  el.textContent = msg;
  el.classList.toggle("error", isError);
}

function initSettings() {
  const form = $("#settings-form");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      state.settings = await api("PUT", "/api/settings", {
        me_steam_id: form.me_steam_id.value.trim(), replays_dir: form.replays_dir.value.trim(),
      });
      status($("#settings-status"), "Saved.");
    } catch (err) { status($("#settings-status"), err.message, true); }
  });
  $("#history-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const body = typedHistory();
    if (!Object.keys(body).length) { status($("#history-status"), "Nothing new to save."); return; }
    saveHistory(body);
  });
  $("#test-history-btn").addEventListener("click", async () => {
    const btn = $("#test-history-btn");
    const st = $("#history-status");
    btn.disabled = true;
    status(st, "Asking Steam…");
    try {
      const res = await api("POST", "/api/settings/test-history", typedHistory());
      status(st, res.message, !res.ok);
      if (res.ok) advanceAfter("test");
    } catch (err) { status(st, err.message, true); }
    btn.disabled = false;
  });
  $("#retention-select").addEventListener("change", (e) => {
    saveDemoSetting({ demo_retention_days: e.target.value ? Number(e.target.value) : null });
  });
  $("#auto-update-check").addEventListener("change", (e) => {
    saveDemoSetting({ auto_update: e.target.checked });
  });
  $("#detect-replays-btn").addEventListener("click", async () => {
    const btn = $("#detect-replays-btn");
    btn.disabled = true;
    try {
      const { replays_dir: found } = await api("GET", "/api/settings/detect-replays");
      if (found) {
        form.replays_dir.value = found;
        status($("#settings-status"), "Found CS2's replays folder. Press Save settings to keep it.");
      } else {
        status($("#settings-status"), "Couldn't find CS2's replays folder. Paste its path instead.", true);
      }
    } catch (err) { status($("#settings-status"), err.message, true); }
    btn.disabled = false;
  });
  $("#backup-btn").addEventListener("click", async () => {
    const btn = $("#backup-btn");
    btn.disabled = true;
    try {
      const { path } = await api("POST", "/api/backup");
      status($("#backup-status"), `Backed up to ${path}`);
    } catch (err) { status($("#backup-status"), err.message, true); }
    btn.disabled = false;
  });
  $("#steam-import-form").addEventListener("submit", (e) => { e.preventDefault(); importSteamHistory(); });
  $("#rivals-min-input").addEventListener("change", saveRivalsMin);
  $("#reparse-btn").addEventListener("click", () => {
    if (state.jobRunning) return;
    if (!confirmReparse()) return;
    runReparse(SETTINGS_REPARSE_UI);
  });
}

/** Settings → "Import Steam match history": upload the file, then follow the job. */
async function importSteamHistory() {
  const input = $("#steam-import-file");
  const st = $("#steam-import-status");
  const file = input.files && input.files[0];
  if (!file) { status(st, "Pick the saved Steam page (.html) or an exported .json file first.", true); return; }
  if (!/\.(json|html?)$/i.test(file.name)) { status(st, "Only .json, .html and .htm files can be imported.", true); return; }
  if (state.jobRunning) return;
  const wrap = $("#steam-import-progress");
  const fd = new FormData();
  fd.append("file", file, file.name);
  setBusy(true);
  status(st, "");
  wrap.hidden = false;
  $("#steam-import-stage").textContent = "Reading the file…";
  $("#steam-import-fill").classList.add("indeterminate");
  try {
    const { job_id } = await api("POST", "/api/steam-history", fd);
    const job = await pollJob(job_id, (j) => showProgress(wrap, $("#steam-import-stage"), $("#steam-import-fill"), j));
    const rep = job.report;
    if (job.state === "error") status(st, job.error || "The import failed.", true);
    else {
      if (rep && rep.message) {
        status(st, rep.message);
        toast(rep.message);
        input.value = "";
      } else status(st, "Done.");
      advanceAfter("import");
    }
    state.people = null;
    matchesLoaded = false;
  } catch (err) {
    status(st, err.message, true);
  } finally {
    wrap.hidden = true;
    setBusy(false);
  }
}

async function saveRivalsMin() {
  const input = $("#rivals-min-input");
  const st = $("#rivals-min-status");
  const n = Number(input.value);
  if (!Number.isInteger(n) || n < 2 || n > 10) {
    status(st, "Rivals minimum games must be between 2 and 10.", true);
    return;
  }
  try {
    state.settings = await api("PUT", "/api/settings", { rivals_min_games: n });
    input.value = String(state.settings.rivals_min_games ?? n);
    status(st, "Saved.");
  } catch (err) { status(st, err.message, true); }
}

/** The shared "are you sure" for every Re-read button (Settings and the banners). */
function confirmReparse() {
  return confirm("Re-read every stored demo? This can take a while (about 20 seconds per demo).");
}

const SETTINGS_REPARSE_UI = {
  wrap: "#reparse-progress", stage: "#reparse-stage", fill: "#reparse-fill", status: "#reparse-status",
};
const REPARSE_NOTICE = "New stats are available for your older demos — press Re-read all demos in Settings.";

/** Runs the reparse job, showing progress in ui's elements (selectors). */
async function runReparse(ui) {
  if (state.jobRunning) return;
  const st = $(ui.status);
  status(st, "");
  setBusy(true);
  const wrap = $(ui.wrap);
  let ok = false;
  try {
    const { job_id } = await api("POST", "/api/reparse");
    const job = await pollJob(job_id, (j) => showProgress(wrap, $(ui.stage), $(ui.fill), j));
    if (job.kind !== "reparse") {
      status(st, "Another job was running, so nothing was re-read. Try again when it finishes.", true);
    } else if (job.state === "error") {
      status(st, job.error || "Re-reading failed.", true);
    } else {
      ok = true;
      const fromEvents = job.results.filter((r) => r.status === "from-events").length;
      const done = job.results.filter((r) => r.status === "reparsed").length + fromEvents;
      const failed = job.results.filter((r) => r.status === "failed");
      const missing = job.results.filter((r) => r.status === "missing").length;
      // a match read from its event bundle alone has no demo path
      const what = (r) => (r.path ? baseName(r.path) : `match #${r.match_id}`);
      status(st, job.total === 0 && !missing ? "No stored demos to re-read." :
        `Re-read ${done} match${done === 1 ? "" : "es"}` +
        (fromEvents ? ` (${fromEvents} from saved events)` : "") +
        (missing ? ` · ${missing} with no demo or event file` : "") +
        (failed.length ? ` · ${failed.length} failed: ${failed.map((f) => `${what(f)} (${f.reason})`).join("; ")}` : ""),
        failed.length > 0);
    }
  } catch (err) {
    status(st, err.message, true);
  } finally {
    wrap.hidden = true;
    setBusy(false);
  }
  if (ok) {
    if (st.textContent) toast(st.textContent, st.classList.contains("error"));
    state.people = null;
    renderReparseBanners();
  }
}

/** The "new stats are available" banner on Home and Settings, when the server suggests a re-read. */
async function renderReparseBanners(about) {
  const boxes = [["#home-reparse-banner", "home"], ["#settings-reparse-banner", "settings"]];
  if (!about) {
    try { about = await api("GET", "/api/about"); } catch { return; }
  }
  for (const [sel, where] of boxes) {
    const box = $(sel);
    if (!about.reparse_suggested) { clear(box); continue; }
    if (box.firstChild && state.jobRunning) continue;   // keep the progress of a running re-read
    const ui = where === "settings" ? SETTINGS_REPARSE_UI : {
      wrap: "#home-reparse-progress", stage: "#home-reparse-stage", fill: "#home-reparse-fill", status: "#home-reparse-status",
    };
    const btn = h("button.btn.btn-small.reparse-now", { type: "button", disabled: state.jobRunning }, "Re-read all demos");
    btn.addEventListener("click", () => { if (!state.jobRunning && confirmReparse()) runReparse(ui); });
    clear(box, notice("warn", "New stats are available", h("p", {}, REPARSE_NOTICE),
      h("div.notice-actions", {}, btn),
      where === "home" ? h("div.progress", { id: "home-reparse-progress", hidden: true },
        h("div.progress-label", { id: "home-reparse-stage" }),
        h("div.progress-track", {}, h("div.progress-fill", { id: "home-reparse-fill" }))) : null,
      where === "home" ? h("p.status", { id: "home-reparse-status", role: "status" }) : null));
  }
}

// ---------- auto update ----------

const AUTO_UPDATE_EVERY_MS = 2 * 60 * 60 * 1000;
const AUTO_UPDATE_STALE_MS = 30 * 60 * 1000;

/** Starts an Update when auto update is on and no job is running. On page load
 *  (onlyIfStale) only when the last Update was more than 30 minutes ago. */
async function maybeAutoUpdate(onlyIfStale) {
  if (state.jobRunning) return;
  let about;
  try { about = await api("GET", "/api/about"); } catch { return; }
  if (!about.auto_update || about.job_running || state.jobRunning) return;
  if (onlyIfStale && about.last_update_at) {
    const last = Date.parse(about.last_update_at);
    if (Number.isFinite(last) && Date.now() - last <= AUTO_UPDATE_STALE_MS) return;
  }
  startUpdate();
}

// ---------- router ----------

const VIEWS = {
  home: () => { renderLastSession(); renderReparseBanners(); },
  matches: (arg) => renderMatches(arg),
  people: () => renderPeople(),
  stats: () => renderStats(),
  dashboard: () => renderDashboard(),
  rivals: () => renderRivals(),
  summary: () => renderSummaryView(),
  settings: () => renderSettings(),
};

function route() {
  if (welcomeShown) return;   // nothing else to show until the Steam ID is known
  const [name, arg] = (location.hash.replace(/^#/, "") || "home").split("/");
  const view = VIEWS[name] ? name : "home";
  for (const sec of document.querySelectorAll(".view")) sec.hidden = sec.id !== `view-${view}`;
  for (const a of document.querySelectorAll("nav a")) {
    if (a.dataset.view === view) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  if (view === "matches") {
    matchesLoaded = false;   // refresh filter options with new imports
    if (arg) { quietReset = true; $("#match-filters").reset(); quietReset = false; }   // don't hide a linked match
  }
  VIEWS[view](arg);
}

let autoUpdateStarted = false;

/** Normal start once the Steam ID is known: pages, step banners and auto update. */
function startApp(owner) {
  route();
  renderOnboarding(owner);
  if (autoUpdateStarted) return;
  autoUpdateStarted = true;
  maybeAutoUpdate(true);
  setInterval(() => maybeAutoUpdate(false), AUTO_UPDATE_EVERY_MS);
}

async function init() {
  initHome();
  initMatches();
  initPeople();
  initSummary();
  initSettings();
  initRivals();
  initDashboard();
  initWelcome({
    onOwnerSet: async () => {
      await loadSettings().catch(() => {});
      if (location.hash !== "#settings") location.hash = "#settings";   // step 2: match history access
      startApp();
    },
  });
  window.addEventListener("hashchange", route);
  let owner = null;
  try { owner = await api("GET", "/api/owner"); } catch { /* older server or offline: carry on */ }
  if (owner && owner.status !== "set") { showWelcome(owner); return; }
  loadSettings().catch(() => {});
  if (owner && owner.onboarding_step === "history" && location.hash !== "#settings") location.hash = "#settings";
  startApp(owner);
}

init();
