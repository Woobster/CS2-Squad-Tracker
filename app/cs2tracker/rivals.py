"""Rivals tab queries (spec v1.3 §8): the players I keep meeting.

Counting rules:
- Only matches with the full lineup count: a match with at least one enemy row (Steam,
  demo and both; a Leetify-only match without enemies, or a Leetify row that got a Steam
  scoreboard attached, is judged by its rows the same way).
- Me (``me`` and every other account of my person) and bots (Steam ID ``0`` or a name
  starting ``[BOT]``) are never listed.
- A row is one account, or one person merged across their accounts (``person:<id>``,
  shown under the person's name). Accounts that belong to any person ("my people", the
  People tab covers them) are hidden unless ``show_tracked``; the People ``tracked`` flag
  plays no part.
- ``with_*`` = matches on my team, ``against_*`` = on the enemy team; W/L are my results
  (a tie counts in N only). ``total`` = distinct matches.
- A row is listed when ``total >= min_games``. So "Against me" with the minimum is
  ``against_n >= min_games``, "On my team" ``with_n >= min_games``, and "Both sides" any
  listed row with both (``both_sides``): the spec's 36 / 67 / 19 at 2 games.
"""
from __future__ import annotations

import builtins
import json
import math

from cs2tracker import db


MIN_GAMES_KEY = "rivals_min_games"
MIN_GAMES_DEFAULT = 2
MIN_GAMES_RANGE = range(2, 11)     # the setting allows 2-10


def min_games(conn) -> int:
    """The ``rivals_min_games`` setting (default 2; a stored value outside 2-10 counts as
    the default)."""
    try:
        n = int(db.get_setting(conn, MIN_GAMES_KEY, str(MIN_GAMES_DEFAULT)))
    except (TypeError, ValueError):
        return MIN_GAMES_DEFAULT
    return n if n in MIN_GAMES_RANGE else MIN_GAMES_DEFAULT


def exists(conn, key: str) -> bool:
    """Whether a rival key names a known person or account."""
    kind, _, ident = key.partition(":")
    if kind == "person" and ident.isdigit():
        return conn.execute("SELECT 1 FROM person WHERE id=?", (int(ident),)).fetchone() is not None
    if kind == "account" and ident:
        return conn.execute("SELECT 1 FROM account WHERE steam_id=?",
                            (ident,)).fetchone() is not None
    return False


NOT_AIMED = frozenset({"hegrenade", "inferno", "molotov", "incgrenade", "flashbang",
                       "smokegrenade", "decoy", "world", "planted_c4", "c4", ""})
UNIT_M = 0.0254

_ROWS = """
WITH mine(sid) AS (
    SELECT :me UNION
    SELECT steam_id FROM account WHERE person_id IS NOT NULL
      AND person_id = (SELECT person_id FROM account WHERE steam_id = :me)),
full(match_id) AS (SELECT DISTINCT match_id FROM match_player WHERE team = 'enemy'
                   AND match_id IN (SELECT id FROM counted_match))
SELECT CASE WHEN a.person_id IS NOT NULL THEN 'person:' || a.person_id
            ELSE 'account:' || mp.steam_id END AS k,
       mp.steam_id, mp.match_id, mp.team, m.result, m.started_at,
       COALESCE(mp.name_in_match, a.last_seen_name) AS name, p.display_name
FROM match_player mp
JOIN full f ON f.match_id = mp.match_id
JOIN match m ON m.id = mp.match_id
LEFT JOIN account a ON a.steam_id = mp.steam_id
LEFT JOIN person p ON p.id = a.person_id
WHERE mp.steam_id NOT IN (SELECT sid FROM mine)
  AND mp.steam_id != '0'
  AND COALESCE(mp.name_in_match, a.last_seen_name, '') NOT LIKE '[BOT]%'
  AND (:show = 1 OR a.person_id IS NULL)
"""

# `shown` is a bare column next to the single MAX() aggregate: SQLite documents that such a
# column takes its value from the row holding the maximum, so it is the name from the
# latest match (the COUNTs are not min/max aggregates and don't change that).
# https://www.sqlite.org/lang_select.html#bare_columns_in_an_aggregate_query
_LIST = f"""
WITH r AS ({_ROWS})
SELECT k, COALESCE(display_name, name) AS shown, MAX(COALESCE(started_at, '')) AS last_seen,
       COUNT(DISTINCT match_id) AS total,
       COUNT(DISTINCT CASE WHEN team = 'mine' THEN match_id END),
       COUNT(DISTINCT CASE WHEN team = 'mine' AND result = 'win' THEN match_id END),
       COUNT(DISTINCT CASE WHEN team = 'mine' AND result = 'loss' THEN match_id END),
       COUNT(DISTINCT CASE WHEN team = 'enemy' THEN match_id END),
       COUNT(DISTINCT CASE WHEN team = 'enemy' AND result = 'win' THEN match_id END),
       COUNT(DISTINCT CASE WHEN team = 'enemy' AND result = 'loss' THEN match_id END)
FROM r GROUP BY k HAVING total >= :min
ORDER BY total DESC, last_seen DESC, k
"""


def ordinal(n: int) -> str:
    """1 -> "1st", 12 -> "12th", 22 -> "22nd", 1000 -> "1,000th"."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n:,}{suffix}"


def list(conn, me: str, min_games: int, show_tracked: bool = False) -> builtins.list[dict]:
    """The Rivals rows (see module docstring), sorted by total then most recent."""
    out = []
    for k, name, last, total, wn, ww, wl, an, aw, al in conn.execute(
            _LIST, {"me": str(me), "show": int(bool(show_tracked)), "min": int(min_games)}):
        out.append({"key": k, "name": name or k.split(":", 1)[1], "total": total,
                    "with_n": wn, "with_w": ww, "with_l": wl,
                    "against_n": an, "against_w": aw, "against_l": al,
                    "last_seen": last or None, "both_sides": bool(wn and an)})
    return out


def _steam_ids(conn, key: str) -> builtins.list[str]:
    kind, _, ident = key.partition(":")
    if kind == "person":
        return [r[0] for r in conn.execute(
            "SELECT steam_id FROM account WHERE person_id=? ORDER BY steam_id", (int(ident),))]
    if kind == "account":
        return [ident]
    raise ValueError(f"bad rival key: {key!r}")


def _mine(conn, me: str) -> builtins.list[str]:
    rows = [r[0] for r in conn.execute(
        "SELECT steam_id FROM account WHERE person_id IS NOT NULL AND person_id = "
        "(SELECT person_id FROM account WHERE steam_id=?)", (me,))]
    return sorted(set(rows) | {me})


def _kda(k, d, a) -> str:
    return "/".join("-" if v is None else str(v) for v in (k, d, a))


def _ph(n: int) -> str:
    return ",".join("?" * n)


def _dist(a: str | None, b: str | None) -> float | None:
    try:
        pa, pb = json.loads(a), json.loads(b)
        return math.dist(pa, pb) * UNIT_M
    except (TypeError, ValueError):
        return None


def detail(conn, me: str, key: str) -> dict:
    """One rival: every shared match newest first, and head-to-head from kill data."""
    me = str(me)
    sids = [s for s in _steam_ids(conn, key) if s not in _mine(conn, me)]
    kind, _, ident = key.partition(":")
    if kind == "person":
        r = conn.execute("SELECT display_name FROM person WHERE id=?", (int(ident),)).fetchone()
        name = r[0] if r else key
    else:
        name = None
    mine = _mine(conn, me)
    mq = _ph(len(mine))  # my row: any of my accounts (`me` first)
    matches, seen = [], set()
    if sids:
        rows = conn.execute(
            "SELECT m.id, m.started_at, m.map, m.result, mp.team, mp.kills, mp.deaths, "
            "mp.assists, COALESCE(mp.name_in_match, a.last_seen_name), "
            f"(SELECT kills FROM match_player x WHERE x.match_id=m.id AND x.steam_id IN ({mq}) "
            "ORDER BY x.steam_id != ? LIMIT 1), "
            f"(SELECT deaths FROM match_player x WHERE x.match_id=m.id AND x.steam_id IN ({mq}) "
            "ORDER BY x.steam_id != ? LIMIT 1), "
            f"(SELECT assists FROM match_player x WHERE x.match_id=m.id AND x.steam_id IN ({mq}) "
            "ORDER BY x.steam_id != ? LIMIT 1) "
            "FROM match_player mp JOIN match m ON m.id=mp.match_id "
            "LEFT JOIN account a ON a.steam_id=mp.steam_id "
            f"WHERE mp.steam_id IN ({_ph(len(sids))}) AND EXISTS (SELECT 1 FROM match_player e "
            "WHERE e.match_id=m.id AND e.team='enemy') AND m.id IN (SELECT id FROM counted_match) "
            "ORDER BY m.started_at IS NULL, m.started_at DESC, m.id DESC",
            (*mine, me, *mine, me, *mine, me, *sids)).fetchall()
        for mid, started, mp, res, team, k, d, a, nm, mk, md, ma in rows:
            if mid in seen:
                continue
            seen.add(mid)
            if name is None and nm:
                name = nm
            matches.append({"match_id": mid, "date": started, "map": mp, "result_for_me": res,
                            "side": "with" if team == "mine" else "against",
                            "their_kda": _kda(k, d, a), "my_kda": _kda(mk, md, ma)})
    against = [m["match_id"] for m in matches if m["side"] == "against"]
    h2h = None
    if against:
        with_kills = [r[0] for r in conn.execute(
            f"SELECT DISTINCT match_id FROM kill WHERE match_id IN ({_ph(len(against))})",
            against)]
        if with_kills:
            h2h = {"i_killed": 0, "they_killed": 0, "longest_m": None}
            theirs, me_set = set(sids), set(mine)
            for att, vic, weapon, axyz, vxyz in conn.execute(
                    "SELECT attacker_steam_id, victim_steam_id, weapon, attacker_xyz, victim_xyz "
                    f"FROM kill WHERE team_kill=0 AND match_id IN ({_ph(len(with_kills))})",
                    with_kills):
                if att in me_set and vic in theirs:
                    h2h["i_killed"] += 1
                elif att in theirs and vic in me_set:
                    h2h["they_killed"] += 1
                else:
                    continue
                w = (weapon or "").removeprefix("weapon_")
                if w in NOT_AIMED:
                    continue
                dist = _dist(axyz, vxyz)
                if dist is not None and (h2h["longest_m"] is None or dist > h2h["longest_m"]):
                    h2h["longest_m"] = dist
            if h2h["longest_m"] is not None:
                h2h["longest_m"] = round(h2h["longest_m"], 1)
    return {"name": name or key.split(":", 1)[1], "steam_ids": sids, "matches": matches,
            "head_to_head": h2h}


def seen_again(conn, me: str, match_ids, min_games: int) -> builtins.list[str]:
    """"Seen again" lines for the listed rivals (accounts of my people hidden) in these matches:
    meetings counted up to and including their last match in the session."""
    ids = {int(m) for m in match_ids}
    if not ids:
        return []
    rows = conn.execute(f"WITH r AS ({_ROWS}) SELECT k, match_id, team, started_at, name, "
                        "display_name FROM r",
                        {"me": str(me), "show": 0}).fetchall()
    in_session = {k for k, mid, *_ in rows if mid in ids}
    per: dict[str, dict[int, tuple]] = {}
    for k, mid, team, started, name, pname in rows:
        if k in in_session:
            per.setdefault(k, {}).setdefault(mid, (started, team, pname or name))
    out = []
    for k, meets in per.items():
        order = sorted(meets, key=lambda m: (meets[m][0] is None, meets[m][0] or "", m))
        last = max(i for i, m in enumerate(order) if m in ids)
        upto = order[:last + 1]
        n = len(upto)
        if n < min_games:
            continue
        side = meets[upto[-1]][1]
        same = sum(meets[m][1] == side for m in upto)
        name = meets[upto[-1]][2] or k.split(":", 1)[1]
        where = "against you" if side == "enemy" else "on your team"
        line = f"{name} ({ordinal(n)} time, {ordinal(same)} {where})"
        out.append((-n, name.casefold(), line))
    out.sort()
    return [x[-1] for x in out]
