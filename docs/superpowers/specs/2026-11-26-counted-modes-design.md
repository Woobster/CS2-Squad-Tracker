# Only Competitive, Premier (and FACEIT) count — design

_2026-11-26. Ships together with the Dashboard tab (`2026-11-26-dashboard-tab-design.md`)._

## Intent
The owner doesn't want Casual, Deathmatch, Wingman or other modes in any stats. Only Premier, Competitive,
and FACEIT if he ever plays it. This applies to the **whole app**: Summary (awards, roasts, best-of,
lineups), Records, Stats, People, Rivals, Matches and the new Dashboard.

## Goals
- One rule decides whether a match **counts**; every stats screen uses it.
- Matches tab gets a switch: **All · Competitive · Premier** ("All" = all counted matches).
- Nothing is deleted. A match that starts uncounted (mode unknown) starts counting by itself once its mode
  becomes known (e.g. after a Steam history import), and the other way round.

## Non-goals
- No FACEIT import or FACEIT option on the Matches switch (no FACEIT data yet; add when it exists).
- No setting to choose modes. The rule is fixed.

## Anti-goals
- Losing data: uncounted matches stay in the database, untouched by retention changes.
- Housekeeping going blind: import, dedupe/merge, reparse, retention, owner inference and the Steam
  history chain keep seeing **every** match.
- Silently dropping the Leetify-era history.

## Approaches
1. **One shared rule in the database, used by every stats query.** _(Picked.)_ A SQL view `counted_match`
   lists ids of matches that count; stats queries join it. Always in sync, nothing stored twice.
2. A stored `counts` flag set at import. Faster, but must be re-set everywhere a mode is filled in later;
   a missed spot gives wrong stats silently. Speed doesn't matter at this database size.
3. Stop importing other modes and delete existing ones. Simplest queries; irreversibly loses data, and
   Valve deletes old demos, so they can't be re-fetched.

## The rule
A match counts when the first of these that applies says yes:
1. `game_mode` is set (from Steam history) → counts if `competitive` or `premier`.
2. `mode` text is set (Leetify rows, anything else that labels a mode) → counts if it is, case-insensitive,
   Competitive, Premier, Matchmaking or FACEIT; not if it names another mode (Wingman, Casual, Deathmatch,
   Danger Zone, Arms Race, Retake, Custom…).
3. No mode known and the match has a player list → counts if it's 5v5: at most 5 and at least 4 players
   on each side (allows one leaver) — this covers demo matches not yet seen in Steam history and FACEIT demos.
4. No mode and no usable player list (e.g. a Leetify summary row with only my team) → **counts**, so old
   history isn't dropped.

Lives in one place: a `CREATE VIEW counted_match AS …` added by a schema migration (and in `schema.sql`),
plus `db.COUNTED_MODES` / a tiny Python helper so tests and code share the mode list.

### Matches tab
- Switch **All · Competitive · Premier** next to the existing filters (same `seg` buttons as Rivals).
  Default All; remembered for the session like the other filters.
- All = counted matches. Competitive/Premier = `game_mode`, or rule-2 mode text, equals that mode. Unknown
  5v5 matches show under All only, with the Mode cell reading "5v5 (mode not known yet)".
- Uncounted matches don't appear. The match page itself still opens by direct link/id.

### Everything else
Every query that feeds a screen joins `counted_match`: `stats.py` (sessions, summary, best-of, roles,
lineups, games together, most played with), `awards.py`, `roasts.py`, `records.py` (rebuild and per-match
update), `rivals.py`, `routes_people.py`, `routes_matches.py`, `routes_groups.py` where it shows stats,
and `dashboard.py`.
- **Sessions** are built from counted matches only (a Wingman game no longer splits or joins a session).
- **Records** are rebuilt once in the migration, so records set in other modes disappear.

### Tests
- Rule table test: each branch (game_mode yes/no; mode text yes/no incl. FACEIT and Wingman; 5v5, 2v2,
  10v10, 5v4; no player list).
- A Wingman demo and a Casual Steam row in a fixture DB don't show in Summary, Records, Stats, People,
  Rivals, Matches or Dashboard; a Premier and an unknown 5v5 do.
- Mode filled in later flips a match from uncounted to counted with no other action.
- Import/dedupe tests still pass unchanged (housekeeping unaffected).

## Builder's calls (mine, flag if wrong)
- 4–5 players per side counts as 5v5 (one leaver); 10v10 Casual and 2v2 Wingman don't.
- Rule 4 keeps Leetify rows with no mode and no full scoreboard.
- Uncounted matches still open via a direct link (e.g. an old event file pointing at one) instead of 404.
