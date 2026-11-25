// Rivals tab (v1.3): the players I keep meeting, on my team, against me, or both.
// Same rules as app.js: every piece of data goes into the page via textContent.

import {
  $, h, clear, api, toast, mapName, fmtDate, fmtTime, steamLink, state, loadSettings, modeTag, prefillAddPerson,
} from "/static/app.js";

const FILTERS = {
  all: () => true,
  with: (r, min) => r.with_n >= min,
  against: (r, min) => r.against_n >= min,
  both: (r) => r.both_sides,
};

const wl = (n, w, l) => (n ? `${n} (${w}–${l})` : "–");

/** [key, label, numeric, sort value(row)]. Null values always sort last. */
const RIVAL_COLS = [
  ["name", "Name", false, (r) => r.name],
  ["total", "Games", true, (r) => r.total],
  ["with", "With (W–L)", true, (r) => r.with_n],
  ["against", "Against (W–L)", true, (r) => r.against_n],
  ["last_seen", "Last seen", true, (r) => (r.last_seen ? Date.parse(r.last_seen) : null)],
  ["both", "Both sides", true, (r) => (r.both_sides ? 1 : 0)],
];

const rv = {
  rows: [],
  filter: "all",
  sort: null,            // null = the server's order (total, then most recent)
  open: new Set(),       // keys whose detail is open
  seq: 0,
};

function sortRivals(rows) {
  if (!rv.sort) return rows;
  const col = RIVAL_COLS.find(([k]) => k === rv.sort.key);
  const get = col[3];
  const sign = rv.sort.dir === "asc" ? 1 : -1;
  return rows.slice().sort((a, b) => {
    const va = get(a) ?? null, vb = get(b) ?? null;
    if (va === null && vb !== null) return 1;
    if (vb === null && va !== null) return -1;
    let c = 0;
    if (va !== null) c = typeof va === "string" ? va.localeCompare(vb, undefined, { sensitivity: "base" }) : va - vb;
    return c * sign || b.total - a.total || String(a.name).localeCompare(String(b.name));
  });
}

function minGames() {
  const n = state.settings && state.settings.rivals_min_games;
  return Number.isInteger(n) ? n : 2;
}

export async function renderRivals() {
  const tbody = $("#rivals-table tbody");
  const seq = ++rv.seq;
  renderRivalsHead();
  const tracked = $("#rivals-tracked").checked ? 1 : 0;
  let rows;
  try {
    if (!state.settings) await loadSettings();
    rows = await api("GET", `/api/rivals?tracked=${tracked}`);
  } catch (e) {
    clear(tbody, h("tr", {}, h("td.empty", { colspan: RIVAL_COLS.length }, e.message)));
    return;
  }
  if (seq !== rv.seq) return;
  rv.rows = rows;
  const min = minGames();
  $("#rivals-hint").textContent = `Players you've met in at least ${min} matches, from your Steam history, demos and full Leetify lineups. ` +
    "You can change the minimum in Settings.";
  renderRivalRows();
}

function renderRivalsHead() {
  const tr = $("#rivals-table thead tr");
  clear(tr, RIVAL_COLS.map(([k, label, numeric], i) => {
    const active = rv.sort ? rv.sort.key === k : k === "total";
    const dir = rv.sort ? rv.sort.dir : "desc";
    const btn = h("button.sort-btn", { type: "button" }, label,
      h("span.sort-mark", { "aria-hidden": "true" }, active ? (dir === "asc" ? "▲" : "▼") : ""));
    btn.addEventListener("click", () => {
      // Same column: reverse. New column: numbers start high-first, text A–Z.
      rv.sort = active ? { key: k, dir: dir === "asc" ? "desc" : "asc" } : { key: k, dir: numeric ? "desc" : "asc" };
      renderRivalsHead();
      renderRivalRows();
      const again = $("#rivals-table thead").querySelectorAll("button.sort-btn")[i];
      if (again) again.focus();
    });
    return h(`th${numeric && k !== "both" ? ".num" : ""}`, {
      "aria-sort": active ? (dir === "asc" ? "ascending" : "descending") : "none",
    }, btn);
  }));
}

function renderRivalRows() {
  const tbody = $("#rivals-table tbody");
  const min = minGames();
  const rows = sortRivals(rv.rows.filter((r) => FILTERS[rv.filter](r, min)));
  clear(tbody);
  if (!rows.length) {
    tbody.append(h("tr", {}, h("td.empty", { colspan: RIVAL_COLS.length },
      rv.rows.length ? "Nobody fits this filter." :
        "Nobody yet. Import your Steam match history in Settings to see who you keep meeting.")));
    return;
  }
  for (const r of rows) {
    const tr = h("tr.mrow.rival-row", { tabindex: 0, "aria-expanded": "false", dataset: { key: r.key } },
      h("td.name", {}, r.name || "?"),
      h("td.num", {}, r.total),
      h("td.num.with", {}, wl(r.with_n, r.with_w, r.with_l)),
      h("td.num.against", {}, wl(r.against_n, r.against_w, r.against_l)),
      h("td.num.date-cell", {}, r.last_seen ? fmtDate(r.last_seen) : "–"),
      h("td", {}, r.both_sides ? h("span.both-badge", {}, "Both sides") : null));
    const toggle = () => toggleRivalDetail(tr, r);
    tr.addEventListener("click", (e) => { if (!e.target.closest("a, button")) toggle(); });
    tr.addEventListener("keydown", (e) => {
      if (e.target === tr && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); toggle(); }
    });
    tbody.append(tr);
    if (rv.open.has(r.key)) toggleRivalDetail(tr, r);
  }
}

async function toggleRivalDetail(tr, r) {
  const next = tr.nextElementSibling;
  if (next && next.classList.contains("detail")) {
    next.remove();
    tr.classList.remove("open");
    tr.setAttribute("aria-expanded", "false");
    rv.open.delete(r.key);
    return;
  }
  rv.open.add(r.key);
  tr.classList.add("open");
  tr.setAttribute("aria-expanded", "true");
  const cell = h("td", { colspan: RIVAL_COLS.length }, h("p.muted", {}, "Loading…"));
  tr.after(h("tr.detail", {}, cell));
  try {
    const d = await api("GET", `/api/rivals/${encodeURIComponent(r.key)}`);
    clear(cell, rivalDetail(r, d));
  } catch (e) {
    clear(cell, h("p.status.error", {}, e.message));
  }
}

const RESULT = { win: ["W", "Win"], loss: ["L", "Loss"], tie: ["D", "Draw"] };

function rivalDetail(r, d) {
  const sid = d.steam_ids && d.steam_ids[0];
  const isAccount = r.key.startsWith("account:");
  const actions = h("div.rival-actions", {},
    sid ? steamLink(`https://steamcommunity.com/profiles/${sid}`, "Steam profile") : null,
    isAccount ? h("button.btn.btn-small", { type: "button", onclick: () => prefillAddPerson(d.name, sid) }, "Track as person") :
      h("span.muted", {}, "Already one of your people"));
  const hh = d.head_to_head;
  const h2h = hh ? h("div.h2h", { "aria-label": "Head-to-head" },
    h("div.h2h-cell", {}, h("b", {}, String(hh.i_killed)), h("span", {}, `times you killed ${d.name}`)),
    h("div.h2h-cell", {}, h("b", {}, String(hh.they_killed)), h("span", {}, `times ${d.name} killed you`)),
    hh.longest_m !== null && hh.longest_m !== undefined ?
      h("div.h2h-cell", {}, h("b", {}, `${Number(hh.longest_m).toFixed(1)} m`), h("span", {}, "longest kill between you")) : null) :
    h("p.muted.small", {}, "No head-to-head yet — it needs a demo of a match against them.");
  const matches = d.matches || [];
  return h("div.rival-detail", {},
    h("div.rival-top", {}, h("h3", {}, d.name), actions),
    h("h4", {}, "Head-to-head"), h2h,
    h("h4", {}, `Shared matches (${matches.length})`),
    h("table.data.rival-matches", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Date"), h("th", {}, "Side"), h("th", {}, "Map"), h("th", {}, "Result"),
        h("th.num", { title: "Their kills / deaths / assists" }, `${d.name} K/D/A`), h("th.num", {}, "My K/D/A"))),
      h("tbody", {}, matches.map((m) => {
        const [code, word] = RESULT[m.result_for_me] || ["?", "Unknown"];
        return h("tr", {},
          h("td.date-cell", {}, h("a", { href: `#matches/${m.match_id}` }, fmtDate(m.date)), " ", h("small", {}, fmtTime(m.date))),
          h("td", {}, h(`span.side.side-${m.side === "with" ? "with" : "against"}`, {}, m.side === "with" ? "With" : "Against")),
          h("td", {}, h("span.mapname", {}, mapName(m.map)), " ", modeTag(m.game_mode)),
          h("td", {}, h(`span.res.res-${code}`, { title: word }, code)),
          h("td.num", {}, m.their_kda || "–"),
          h("td.num", {}, m.my_kda || "–"));
      }))));
}

export function initRivals() {
  const seg = $("#rivals-filter");
  for (const b of seg.querySelectorAll("button[data-filter]")) {
    b.addEventListener("click", () => {
      rv.filter = FILTERS[b.dataset.filter] ? b.dataset.filter : "all";
      for (const x of seg.querySelectorAll("button[data-filter]")) x.setAttribute("aria-pressed", String(x === b));
      renderRivalRows();
    });
  }
  $("#rivals-tracked").addEventListener("change", () => { rv.open.clear(); renderRivals().catch((e) => toast(e.message, true)); });
}
