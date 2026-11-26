# Dashboard tab — design

_2026-11-26. Target version: v1.3.3 (or folded into v1.4)._

## Intent
A friend built a personal stats page (`CS2_dashboard.html`, dark theme, ~1.4 MB) on top of a copy of
this app's data: overview tiles, better/worse deltas, K/D, win rate, KPR and HS % over time, per-month,
maps, teammates, opponents, time-of-day and session-position breakdowns, records, "useful and useless"
facts, an all-matches table, and a demo section (ADR, rating, clutches, utility, money, openings).
The owner wants it in the Squad Tracker as a **Dashboard** tab.

## Goals
- A new **Dashboard** tab in the top nav (between Stats and Rivals) with all of the friend's sections and filters.
- It always shows the **app owner's** stats (the Steam ID saved in the database), never anyone else's.
- Live: it reflects whatever is in the database now (Steam history imports, demos, auto update).

## Non-goals
- No player picker. Owner only (decided 2026-11-26).
- No changes to how matches are imported or stats computed elsewhere.

## Anti-goals
- **No embedded match data in the zip or the repo.** The friend's file contains his full history with
  hundreds of other players' names and IDs. None of it ships (clean-build rule from v1.3.1).
- Numbers must mean what they meant on his page. The port is not an excuse to re-invent formulas.
- It must not look like a different app glued in.

## Approaches
1. **Port his page into the app, fed live by a new API endpoint.** _(Picked.)_ `/api/dashboard` returns
   the owner's matches in the compact format his code reads; his chart/table JS moves into
   `static/dashboard.js` mostly unchanged and is restyled to the app's look. Lowest risk of changing what
   numbers mean; always current.
2. Rewrite all stats in Python, page only draws. Same result, much more work, more drift risk.
3. Show his page as-is in an iframe with our data injected. Fastest, but it's visibly a second app (own
   header, dark theme, own scrolling), and rows couldn't link to the app's match pages.

## Design

### Server: `GET /api/dashboard` (owner required)
New module `dashboard.py`, route registered in `app.py` with the existing `need_owner` dependency.
Returns `{"me": "<owner steam64>", "matches": [...]}`, oldest first, one entry per match the owner played,
in the friend's format:

| Key | From |
|---|---|
| `id` | `match.id` (new; used to link rows to the app's match page) |
| `t` | `match.started_at` → Unix seconds |
| `mo`, `mp` | `match.mode` (game mode label), `match.map` (display name) |
| `w`, `du` | `match.wait_seconds`, `match.duration` |
| `s` | `[mine score, enemy score]` from `my_team_score`/`enemy_score` |
| `T` | `[myTeam, enemyTeam]`, each player `[steam64, name, ping, k, a, d, mvps, hs_percent, scoreboard_score]` from `match_player` (`team` = mine/enemy) |
| `D` | owner's `fun_stats.data` (parsed JSON) merged with `match_player` ADR/rating, only when a demo was read; omitted otherwise |

- Player IDs are sent as Steam64 strings (his page used 32-bit account numbers). The JS compares to `me`.
- Names: a tracked person's display name where the account belongs to a person (so a friend's two
  accounts show as one name); otherwise `name_in_match`/`last_seen_name`.
- `ping` is `null` — the app doesn't store it (see Builder's calls).

### Client: `static/dashboard.js`
- His code, wrapped as a view module like `rivals.js`; loads `/api/dashboard` each time the tab opens.
- Removed: the embedded JSON, `parseSteam`, the "Add Steam file(s)" and "Reset added" buttons,
  `localStorage`, and the "most matches = me" guess (`ME` = `me` from the API).
- Kept: Mode / Period / Map / rolling-average filters and every section, in his order.
- Restyled to the app: app CSS variables (`--sand*`, `--ink*`, `--ct` blue as series 1, `--t` orange as
  series 2, `--bad`), Barlow / Barlow Condensed, `.panel` cards. Chart code stays his hand-written SVG.
- Rows in "All matches" and "Demo matches" link to the app's existing match view.
- Empty state (owner set, no matches): a short line pointing to Steam history import on the Home page.

### Tests
- `tests/test_dashboard.py`: builds a small DB, checks the endpoint's shape, team orientation, owner-only
  filtering, alt-account name merge, `D` present only for demo matches, 4xx without an owner.
- Privacy check: the built zip contains no friend data (compare file list/diff against v1.3.2 as usual).

## Builder's calls (mine, flag if wrong)
- **Ping hidden.** The app doesn't store ping, so "Avg ping", "Highest/Lowest ping" and the Ping column are
  dropped rather than adding a column only new imports would fill.
- Tab placed between Stats and Rivals; route `#dashboard`.
- Any section that ends up with no data in the current filter shows his existing "nothing yet" text, not an error.

## Left to the builder
Exact CSS mapping per element, chart sizing at narrow widths, and code layout inside `dashboard.js`.
