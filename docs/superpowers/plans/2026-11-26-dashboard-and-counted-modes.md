# Dashboard tab + counted modes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Only Competitive/Premier/FACEIT matches count anywhere in the app, the Matches tab gets an All · Competitive · Premier switch, and a new Dashboard tab shows the owner's stats (ported from a friend's page).

**Architecture:** A SQL view `counted_match(id)` (schema v5) is the single rule; every screen-facing query restricts to it, housekeeping queries don't. `GET /api/dashboard` returns the owner's counted matches in the friend's compact format; `static/dashboard.js` is his rendering code, restyled to the app.

**Tech Stack:** Python 3.13, FastAPI, SQLite, vanilla ES modules, hand-written SVG charts. Tests: pytest (`tests/`, fixtures `make_db`, `make_app` in `conftest.py`).

**Spec:** `docs/superpowers/specs/2026-11-26-counted-modes-design.md`, `docs/superpowers/specs/2026-11-26-dashboard-tab-design.md`

## Global Constraints
- No personal/match data from the friend's file anywhere in the repo or zip.
- Import, dedupe/merge, reparse, retention, owner inference and Steam-history chain queries are NOT filtered.
- Nothing is deleted; uncounted matches stay in the DB and still open by `/api/matches/{id}`.
- Counted mode texts (lower-cased, trimmed): `competitive`, `premier`, `matchmaking`, `faceit`. Counted `game_mode`: `competitive`, `premier`.
- 5v5 = 4–5 players on each side (`team='mine'` and `team='enemy'`).
- All page text via `textContent` / the app's `h()` helper (existing rule in `app.js`).
- Version → 1.3.3.

## Review Focus
- Leetify rows with a mode label like `matchmaking_wingman` or `Wingman` must not count; a Leetify row with no mode and only my team must count. → Task 1 rule tests.
- A Wingman game between two Premier games must not split one session into two or join two sessions. → Task 2 `test_sessions_ignore_uncounted`.
- A Steam history import filling `game_mode` for an unknown demo match must flip it with no extra step. → Task 1 `test_flip_when_mode_known`.
- Dashboard with an owner but zero counted matches must render an empty state, not throw. → Task 5 `test_dashboard_empty` + manual check in Task 6.
- Player names with `<`, `&`, tall Unicode in the Dashboard tables must render as text. → Task 6 manual check with a fixture name `<b>x</b>`.

---

### Task 1: The `counted_match` rule (schema v5)

**Files:** Modify `app/cs2tracker/schema.sql`, `app/cs2tracker/db.py` (USER_VERSION 5, `_migrate_4_to_5`, `COUNTED` constant). Test: `tests/test_counted.py`.

**Interfaces — Produces:**
- View `counted_match(id)`.
- `db.COUNTED_SQL = "SELECT id FROM counted_match"`; `db.counted(conn, match_id) -> bool`.
- Migration 4→5: backup `cs2tracker-pre-v5`, `CREATE VIEW`, `PRAGMA user_version=5`, then `records.rebuild(conn)` (import inside the function to avoid a cycle).

View body (the algorithm the spec pins):
```sql
CREATE VIEW IF NOT EXISTS counted_match AS
SELECT m.id FROM match m WHERE CASE
  WHEN m.game_mode IS NOT NULL THEN m.game_mode IN ('competitive','premier')
  WHEN TRIM(COALESCE(m.mode,'')) <> '' THEN LOWER(TRIM(m.mode)) IN ('competitive','premier','matchmaking','faceit')
  ELSE (SELECT CASE WHEN COALESCE(SUM(team='enemy'),0)=0 THEN 1
               ELSE SUM(team='mine') BETWEEN 4 AND 5 AND SUM(team='enemy') BETWEEN 4 AND 5 END
        FROM match_player mp WHERE mp.match_id=m.id)
END;
```

- [ ] Step 1: tests (helper `_match(conn, game_mode, mode, mine, enemy)` inserting a match + N players): parametrised table —
  `('premier',None,5,5)→T`, `('competitive',None,0,0)→T`, `(None,'FACEIT',5,5)→T`, `(None,'matchmaking',0,0)→T`, `(None,'Wingman',2,2)→F`, `(None,'matchmaking_wingman',5,5)→F`, `(None,'Casual',10,10)→F`, `(None,None,5,5)→T`, `(None,None,5,4)→T`, `(None,None,2,2)→F`, `(None,None,10,10)→F`, `(None,None,5,0)→T`, `(None,None,0,0)→T`;
  `test_flip_when_mode_known` (unknown 2v2 → set game_mode='premier' → counted);
  `test_migrates_v4_db` (fresh DB, set user_version=4 and drop the view, reconnect → view exists, version 5).
- [ ] Step 2: `pytest tests/test_counted.py` → FAIL. Step 3: implement. Step 4: PASS + full suite green. Step 5: commit.

### Task 2: Stats, Summary, People, Records use the rule

**Files:** Modify `stats.py` (`sessions`, `person_stats` when `match_ids is None`, `_fav_weapons` same, `recurring_teammates`, `games_together`), `records.py` (`_Data.__init__` match + player + fun_stats loads; `backfill` check; `rebuild_for` skips uncounted ids), `routes_matches.py` `summary` (drop uncounted ids from the body before building). Test: `tests/test_counted_screens.py`.

**Interfaces — Consumes:** `db.COUNTED_SQL`. Pattern: add `AND m.id IN (SELECT id FROM counted_match)` (or `match_id IN (...)`).

- [ ] Step 1: fixture DB with owner + one tracked friend: Premier A (t0), Wingman demo W (t0+30 min, 2v2, no mode), Premier B (t0+1 h), an unknown-mode 5v5 demo U (next day), and a Casual Steam row C. Tests:
  `test_sessions_ignore_uncounted` → `stats.sessions(conn)` contains `[A,B]` only;
  `test_people_totals_ignore_uncounted` → friend's matches = 2 in `person_stats(conn)`;
  `test_records_ignore_uncounted` → record built only from A/B (give W a 30-kill line; most-kills record ≠ 30);
  `test_summary_drops_uncounted` → POST `/api/summary` with `[A,W]` covers 1 match.
- [ ] Steps 2–5: fail, implement, pass + full suite, commit.

### Task 3: Rivals use the rule

**Files:** `rivals.py` (`_ROWS` `full` CTE joins `counted_match`; `detail` query; `seen_again`). Test: add to `tests/test_counted_screens.py`.
- [ ] `test_rivals_ignore_uncounted`: a stranger met in W and in C only → not listed at `min_games=1`; met in A → listed with total 1. Fail → implement → pass → commit.

### Task 4: Matches tab switch

**Files:** `routes_matches.py` (`list_matches(..., mode: Literal['competitive','premier'] | None = None)`; always restrict to counted; mode filter = `COALESCE(m.game_mode, LOWER(TRIM(m.mode))) = :mode`), `static/index.html` (seg buttons `#matches-mode` All/Competitive/Premier before Clear), `static/app.js` (`matchFilters` adds `mode`; seg wiring like Rivals; Mode cell shows "5v5 (mode not known yet)" when `gameModeOf(m)` is null). Test: `tests/test_counted_screens.py`.
- [ ] `test_matches_list_counted_and_mode`: `/api/matches` → A,B,(unknown 5v5 U); `?mode=premier` → A,B; `?mode=competitive` → []; `/api/matches/{W}` → 200. `?mode=wingman` → 422.
- [ ] fail → implement → pass → commit. Manual: toggle in browser later (Task 7).

### Task 5: `GET /api/dashboard`

**Files:** Create `app/cs2tracker/dashboard.py` (`build(conn, me: str) -> dict`), route in `app.py` with `need_owner`. Test: `tests/test_dashboard.py`.

**Produces:** `{"me": str, "matches": [ {id, t, mo, mp, w, du, s:[mine,enemy], T:[[p...],[p...]], D?} ]}`, oldest first; player `[steam64:str, name, null, k, a, d, mvps, hs_percent, scoreboard_score]`; `mo` = "Premier"/"Competitive" from `game_mode`, else the original mode text title-cased, else `"Unknown 5v5"`; `mp` = display map name (reuse `records.map_name`); name = person.display_name if linked else `COALESCE(name_in_match,last_seen_name)`; owner row found via owner + alt accounts of owner's person; `D` = owner's `fun_stats.data` JSON plus `rating` (= `match_player.hltv_rating`) and `adr`, only if a fun_stats row exists.
- [ ] Tests: `test_dashboard_shape` (A appears with my team first, scores oriented), `test_dashboard_counted_only` (W, C absent), `test_dashboard_alt_names` (friend with 2 accounts → same name), `test_dashboard_demo_block` (D only on match with fun_stats), `test_dashboard_needs_owner` (no owner → 409/whatever `need_owner` returns, assert not 200), `test_dashboard_empty` (owner, no matches → `matches == []`).
- [ ] fail → implement → pass → commit.

### Task 6: Dashboard tab UI

**Files:** Create `static/dashboard.js` (port of the friend's script lines 86–697: keep `rows/agg/fillFilters/filtered/lineChart/barChart/table/delta/render` and all sections; remove embedded JSON, `parseSteam`, file input, Reset, `localStorage`, `rebuild`'s ME guess, ping items; `ME` = API `me`; IDs compared as strings; match rows link `#matches/<id>`; `innerHTML` built from data must go through `esc()` — keep his `esc` everywhere data is interpolated). Exports `initDashboard()`, `renderDashboard()`. Modify `index.html` (nav link `#dashboard` between Stats and Rivals; `<section id="view-dashboard">` with filters bar + `#dash-main`), `app.js` (import, `VIEWS.dashboard`, `initDashboard()` in `init`), `style.css` (his rules scoped under `#view-dashboard`, mapped: `--surface→--sand-hi`, `--surface2→--sand-lo`, `--ink2→--ink-2`, `--s1→--ct`, `--s2→--t`, `--good→--ct`, `--bad→--bad`, `--grid/--axis→--line`, fonts `--body`/`--cond` for headings). Test: `tests/test_static.py` gains `test_dashboard_assets` (nav link, section, module import present; no `localStorage` and no `parseSteam` in dashboard.js; no 76561 literal in dashboard.js).
- [ ] Test → fail → implement → pass → commit.
- [ ] Manual: run the app on a fixture DB, open `#dashboard` in a browser, check every section renders, filters work, empty state, a `<b>x</b>` name renders as text.

### Task 7: Release wiring + verification

**Files:** version 1.3.3 wherever 1.3.2 is set (`__init__.py`, README.txt changelog lines), README.txt (Dashboard tab; only Competitive/Premier/FACEIT count).
- [ ] Full `pytest -q` green; grep the repo for names and account numbers from the friend's file (list kept outside the repo) → none.
- [ ] Browser check of Matches switch, Rivals, Summary, Dashboard on a fixture DB.
- [ ] Push branch, open PR.
