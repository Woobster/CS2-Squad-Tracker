"""All-time records, milestones and duos & nemeses (spec v1.3 §6).

Records
-------
``KEYS`` lists the records. Each has a label, a display unit, a direction (``max``: higher
is better, ``min``: lower is better) and a source: ``fun:<stat>`` (from the ``fun_stats``
rows, so only matches with an event file) or ``match:<what>`` (from ``match`` /
``match_player`` / ``round`` rows).

The ``record`` table holds each person's personal best per key (``PRIMARY KEY(key,
person_id)``); the all-time record of a key is the best of those rows. "Tracked" in the spec
means "one of your people": every row in ``person`` can hold records and milestones (the
People ``tracked`` flag plays no part), while accounts without a person (randoms, enemies)
never can. Only a person's own rows on my team count (``match_player.team='mine'``), summed
over their accounts. Ties go to whoever
got there first (earlier ``started_at``, then lower match id). Matches without a start time
sort last. Values are rounded to 1 decimal (distances, seconds, ADR) or whole numbers, and
an equal value never beats a record. A ``max`` record needs a value above 0.

- ``longest_*`` / ``closest``: ``longest[cat].m`` / ``closest.m`` (weapon and round kept in
  ``detail``).
- ``most_kills`` / ``most_damage`` / ``most_headshots``: ``kills`` / ``damage`` /
  ``hs_kills`` of one match. ``best_adr``: ``damage / rounds_played``, only when the person
  played at least half the match's rounds.
- ``fastest_ace`` / ``fastest_opening``: ``fastest_ace_s`` / ``fastest_opening_s``.
- ``biggest_clutch``: the largest N with ``clutch_wins[N] > 0`` (shown "1vN").
- ``kill_streak``: ``best_kill_streak``.
- ``knife_kills_total``: all-time sum of ``knife_kills``; its match is the one where the
  person's total last went up.
- ``comeback``: the largest round deficit in a won demo match. Each round's winner side comes
  from the ``round`` table; my side per round follows MR12 (halftime after round 12,
  overtime blocks of 6 swapping after 3). The starting side (and whether overtime starts
  swapped) is the one whose round wins add up to the stored score; a match where the
  candidates disagree, a round winner is unknown, or the round count differs from the
  score is skipped.
- ``win_streak``: per person (not the owner), the most wins in a row among the
  matches with them on my team, all sources, in time order; a loss or a tie ends a streak.
  Its match is the streak's last match.
- ``longest_match``: ``match.duration`` in whole minutes (Steam duration or demo length).

``rebuild(conn)`` recomputes every row; ``rebuild_for(conn, match_ids)`` recomputes the
rows of the people with an account on my team in those matches, or with a record
pointing at one. Both are idempotent and don't commit.

Milestones
----------
Per person, in time order over every match with them on my team: kills (500,
1,000, 2,500, 5,000; ``match_player.kills`` of all sources), matches together with the owner
(100, 250, 500; not for the owner) and aces (10, 25, 50; ``match_player.k5`` where it
is known). A milestone is reached in a match when the running total crosses it there.

Duos & nemeses
--------------
Over the session's matches with an event file (a ``fun_stats`` row), for the ticked people:
best duo = best win rate of a pair over the matches both were on my team (at least
``DUO_MIN`` matches, and at least one win: a pair that lost every match together is never
the "best duo"; ties: more matches, then names); trade partner = the pair
where one most often avenged the other (``avenged``, at least ``TRADE_MIN``); nemesis = the
enemy who killed one ticked person most (``killers``, at least ``NEMESIS_MIN``; ties: lower
Steam ID), named by their latest in-game name.
"""
from __future__ import annotations

import json
from collections import defaultdict

from cs2tracker import db

DUO_MIN = 2
TRADE_MIN = 2
NEMESIS_MIN = 4
KILL_MILESTONES = (500, 1000, 2500, 5000)
TOGETHER_MILESTONES = (100, 250, 500)
ACE_MILESTONES = (10, 25, 50)


def _k(label, unit, direction, source):
    return {"label": label, "unit": unit, "direction": direction, "source": source}


KEYS: dict[str, dict] = {
    "longest_any": _k("Longest kill", "m", "max", "fun:longest.any"),
    "longest_pistol": _k("Longest pistol kill", "m", "max", "fun:longest.pistol"),
    "longest_awp": _k("Longest AWP kill", "m", "max", "fun:longest.awp"),
    "longest_zeus": _k("Longest Zeus kill", "m", "max", "fun:longest.zeus"),
    "longest_knife": _k("Longest knife kill", "m", "max", "fun:longest.knife"),
    "closest": _k("Closest kill", "m", "min", "fun:closest"),
    "most_kills": _k("Most kills in a match", "kills", "max", "fun:kills"),
    "most_damage": _k("Most damage in a match", "damage", "max", "fun:damage"),
    "most_headshots": _k("Most headshots in a match", "headshots", "max", "fun:hs_kills"),
    "best_adr": _k("Highest ADR in a match", "ADR", "max", "fun:damage/rounds_played"),
    "fastest_ace": _k("Fastest ace", "s", "min", "fun:fastest_ace_s"),
    "fastest_opening": _k("Fastest opening kill", "s", "min", "fun:fastest_opening_s"),
    "biggest_clutch": _k("Biggest clutch won", "1vN", "max", "fun:clutch_wins"),
    "kill_streak": _k("Longest kill streak", "kills", "max", "fun:best_kill_streak"),
    "knife_kills_total": _k("Most knife kills", "knife kills", "max", "fun:knife_kills (sum)"),
    "comeback": _k("Biggest comeback", "rounds", "max", "match:rounds"),
    "win_streak": _k("Longest win streak", "wins", "max", "match:result"),
    "longest_match": _k("Longest match", "min", "max", "match:duration"),
}
_NO_MAP = {"knife_kills_total", "win_streak"}
_ONE = {"kills": "kill", "headshots": "headshot", "knife kills": "knife kill", "wins": "win",
        "rounds": "round"}


# ---------------------------------------------------------------- helpers

def _me(conn) -> str:
    return db.get_setting(conn, "me_steam_id") or None


def _my_person(conn) -> int | None:
    r = conn.execute("SELECT person_id FROM account WHERE steam_id=?", (_me(conn),)).fetchone()
    return r[0] if r else None


def _ph(n: int) -> str:
    return ",".join("?" * n)


def _order(started: str | None, mid: int) -> tuple:
    return (started is None, started or "", mid)


def map_name(m: str | None) -> str:
    if not m:
        return "?"
    m = m[3:] if m.startswith(("de_", "cs_")) else m
    return m[:1].upper() + m[1:]


def value_text(key: str, v) -> str:
    unit = KEYS[key]["unit"]
    if unit in ("m", "s", "ADR"):
        return f"{v:.1f} {unit}"
    if unit == "1vN":
        return f"1v{int(v)}"
    n = int(v)
    if key == "win_streak":
        return f"{n:,} {'win' if n == 1 else 'wins'} in a row"
    if key == "comeback":
        return f"won from {n:,} {'round' if n == 1 else 'rounds'} down"
    if key == "longest_match":
        return f"{n:,} min"
    return f"{n:,} {_ONE.get(unit, unit) if n == 1 else unit}"


def _better(key: str, a, b) -> bool:
    """Is value a strictly better than b (b None = no record yet)?"""
    if b is None:
        return True
    return a < b if KEYS[key]["direction"] == "min" else a > b


# ---------------------------------------------------------------- loading

class _Data:
    """Everything the computations read, for the people (optionally a subset)."""

    def __init__(self, conn, person_ids=None, before: str | None = None) -> None:
        q = "SELECT id, display_name FROM person"
        self.names = dict(conn.execute(q))
        if person_ids is not None:
            keep = set(person_ids)
            self.names = {p: n for p, n in self.names.items() if p in keep}
        self.me_pid = _my_person(conn)
        self.matches = {}
        for mid, started, mp, dur, res, my_s, en_s in conn.execute(
                "SELECT id, started_at, map, duration, result, my_team_score, enemy_score "
                "FROM match WHERE id IN (SELECT id FROM counted_match)"):
            if before is not None and (started is None or started >= before):
                continue
            self.matches[mid] = (started, mp, dur, res, my_s, en_s)
        self.acct = {}
        if self.names:
            pids = list(self.names)
            self.acct = dict(conn.execute(
                f"SELECT steam_id, person_id FROM account WHERE person_id IN ({_ph(len(pids))})",
                pids))
        # (pid, mid) -> summed my-team row
        self.rows: dict[tuple, dict] = {}
        self.fun: dict[tuple, list[dict]] = defaultdict(list)
        if not self.acct:
            return
        sids = list(self.acct)
        for mid, sid, kills, k5 in conn.execute(
                "SELECT match_id, steam_id, kills, k5 FROM match_player WHERE team='mine' "
                f"AND steam_id IN ({_ph(len(sids))})", sids):
            if mid not in self.matches:
                continue
            r = self.rows.setdefault((self.acct[sid], mid), {"kills": None, "k5": None})
            if kills is not None:
                r["kills"] = (r["kills"] or 0) + kills
            if k5 is not None:
                r["k5"] = (r["k5"] or 0) + k5
        for mid, sid, data in conn.execute(
                "SELECT f.match_id, f.steam_id, f.data FROM fun_stats f JOIN match_player mp "
                "ON mp.match_id=f.match_id AND mp.steam_id=f.steam_id AND mp.team='mine' "
                f"WHERE f.steam_id IN ({_ph(len(sids))})", sids):
            if mid in self.matches:
                self.fun[(self.acct[sid], mid)].append(json.loads(data))

    def ordered(self, mids) -> list[int]:
        return sorted(mids, key=lambda m: _order(self.matches[m][0], m))


# ---------------------------------------------------------------- per-match values

def _fun_values(row: dict, match) -> dict:
    out = {}
    for cat in ("any", "pistol", "awp", "zeus", "knife"):
        e = (row.get("longest") or {}).get(cat)
        if e and e.get("m") is not None:
            out[f"longest_{cat}"] = (round(e["m"], 1), {"weapon": e.get("weapon"),
                                                       "round": e.get("round")})
    c = row.get("closest")
    if c and c.get("m") is not None:
        out["closest"] = (round(c["m"], 1), {"weapon": c.get("weapon"), "round": c.get("round")})
    for key, stat in (("most_kills", "kills"), ("most_damage", "damage"),
                      ("most_headshots", "hs_kills"), ("kill_streak", "best_kill_streak")):
        if row.get(stat):
            out[key] = (int(row[stat]), None)
    rounds = row.get("rounds_played") or 0
    _, _, _, _, my_s, en_s = match
    if rounds and row.get("damage") and (my_s is None or en_s is None
                                         or rounds * 2 >= my_s + en_s):
        out["best_adr"] = (round(row["damage"] / rounds, 1), {"rounds": rounds})
    for key, stat in (("fastest_ace", "fastest_ace_s"), ("fastest_opening", "fastest_opening_s")):
        if row.get(stat) is not None:
            out[key] = (round(row[stat], 1), None)
    won = [int(n) for n, c in (row.get("clutch_wins") or {}).items() if c]
    if won:
        out["biggest_clutch"] = (max(won), None)
    return out


def _side_plans(n: int, start: str, ot_swap: bool) -> list[str]:
    other = {"CT": "T", "T": "CT"}
    sides = []
    for r in range(1, n + 1):
        if r <= 12:
            s = start
        elif r <= 24:
            s = other[start]
        else:
            # overtime: blocks of 6 rounds, sides swap after 3. With ot_swap the sides also
            # swap at the start of every block (so each block starts on `start`); without,
            # they carry on from round 24.
            block, half = divmod(r - 25, 6)
            if ot_swap:
                first = start
            else:
                first = other[start] if block % 2 == 0 else start
            s = first if half < 3 else other[first]
        sides.append(s)
    return sides


def comeback_deficit(winners: list[str | None], my_score, enemy_score) -> int | None:
    """Largest deficit my team overcame, from round winner sides (see module docstring)."""
    n = len(winners)
    if (not n or None in winners or my_score is None or enemy_score is None
            or n != my_score + enemy_score):
        return None
    found = set()
    for start in ("CT", "T"):
        for ot_swap in ((False, True) if n > 24 else (False,)):
            sides = _side_plans(n, start, ot_swap)
            mine = sum(w == s for w, s in zip(winners, sides))
            if mine != my_score:
                continue
            a = b = worst = 0
            for w, s in zip(winners, sides):
                if w == s:
                    a += 1
                else:
                    b += 1
                worst = max(worst, b - a)
            found.add(worst)
    return found.pop() if len(found) == 1 else None


def _comebacks(conn, d: _Data) -> dict[int, int]:
    winners: dict[int, list] = defaultdict(list)
    for mid, w in conn.execute("SELECT match_id, winner_side FROM round ORDER BY match_id, number"):
        if mid in d.matches and d.matches[mid][3] == "win":
            winners[mid].append(w)
    out = {}
    for mid, ws in winners.items():
        _, _, _, _, my_s, en_s = d.matches[mid]
        v = comeback_deficit(ws, my_s, en_s)
        if v:
            out[mid] = v
    return out


# ---------------------------------------------------------------- compute

def _compute(conn, person_ids=None, before: str | None = None) -> dict[tuple, tuple]:
    """{(key, pid): (value, match_id, detail, achieved_at)} — each person's best."""
    d = _Data(conn, person_ids, before)
    best: dict[tuple, tuple] = {}

    def offer(key, pid, value, mid, detail=None):
        cur = best.get((key, pid))
        if cur is not None and not _better(key, value, cur[0]):
            if value != cur[0] or _order(d.matches[mid][0], mid) >= _order(
                    d.matches[cur[1]][0], cur[1]):
                return
        best[(key, pid)] = (value, mid, detail, d.matches[mid][0])

    for (pid, mid), rows in d.fun.items():
        for row in rows:
            for key, (v, det) in _fun_values(row, d.matches[mid]).items():
                offer(key, pid, v, mid, det)
    # knife kills all-time: running total per person
    knives: dict[int, dict[int, int]] = defaultdict(dict)
    for (pid, mid), rows in d.fun.items():
        n = sum(r.get("knife_kills") or 0 for r in rows)
        if n:
            knives[pid][mid] = n
    for pid, per in knives.items():
        total, last = 0, None
        for mid in d.ordered(per):
            total += per[mid]
            last = mid
        best[("knife_kills_total", pid)] = (total, last, None, d.matches[last][0])
    # match-level
    present: dict[int, list[int]] = defaultdict(list)   # pid -> matches on my team
    for pid, mid in d.rows:
        present[pid].append(mid)
    combacks = _comebacks(conn, d)
    for pid, mids in present.items():
        streak = 0
        for mid in d.ordered(mids):
            started, mp, dur, res, my_s, en_s = d.matches[mid]
            if dur:
                offer("longest_match", pid, int(dur) // 60, mid)
            if mid in combacks:
                offer("comeback", pid, combacks[mid], mid,
                      {"score": [my_s, en_s]})
            if pid != d.me_pid:
                streak = streak + 1 if res == "win" else 0
                if streak:
                    offer("win_streak", pid, streak, mid)
    return {k: v for k, v in best.items()
            if KEYS[k[0]]["direction"] == "min" or v[0] > 0}


def _write(conn, rows: dict[tuple, tuple], person_ids=None) -> None:
    if person_ids is None:
        conn.execute("DELETE FROM record")
    else:
        pids = list(person_ids)
        if not pids:
            return
        conn.execute(f"DELETE FROM record WHERE person_id IN ({_ph(len(pids))})", pids)
    conn.executemany(
        "INSERT INTO record(key, person_id, value, match_id, detail, achieved_at) "
        "VALUES (?,?,?,?,?,?)",
        [(key, pid, v, mid, None if det is None else json.dumps(det, sort_keys=True), at)
         for (key, pid), (v, mid, det, at) in sorted(rows.items())])
    # a deleted person holds nothing
    conn.execute("DELETE FROM record WHERE person_id NOT IN (SELECT id FROM person)")


def rebuild(conn, person_ids=None) -> None:
    """Recompute the record rows (all people, or the given ones); no commit."""
    _write(conn, _compute(conn, person_ids), person_ids)


def backfill(conn) -> bool:
    """Startup safety net: rebuild (and commit) when ``record`` is empty but matches exist.
    Returns whether it rebuilt."""
    if conn.execute("SELECT 1 FROM record LIMIT 1").fetchone() is not None:
        return False
    if conn.execute("SELECT 1 FROM match LIMIT 1").fetchone() is None:
        return False
    rebuild(conn)
    conn.commit()
    return True


def rebuild_for(conn, match_ids) -> None:
    """Recompute the rows of the people in these matches (see module docstring)."""
    mids = [int(m) for m in match_ids]
    if not mids:
        return
    pids: set[int] = set()
    for i in range(0, len(mids), 500):
        chunk = mids[i:i + 500]
        pids.update(r[0] for r in conn.execute(
            "SELECT DISTINCT a.person_id FROM match_player mp JOIN account a "
            "ON a.steam_id=mp.steam_id JOIN person p ON p.id=a.person_id "
            f"WHERE mp.team='mine' AND mp.match_id IN ({_ph(len(chunk))})",
            chunk))
        pids.update(r[0] for r in conn.execute(
            f"SELECT DISTINCT person_id FROM record WHERE match_id IN ({_ph(len(chunk))})",
            chunk))
    rebuild(conn, sorted(pids))


# ---------------------------------------------------------------- reading

def _tops(rows: dict[tuple, tuple], names: dict[int, str], matches: dict) -> dict[str, dict]:
    """Best entry per key from {(key, pid): (value, mid, detail, at)} (ties: first there)."""
    by_key: dict[str, list] = defaultdict(list)
    for (key, pid), entry in rows.items():
        if pid in names and key in KEYS:
            by_key[key].append((pid, entry))
    out = {}
    for key, items in by_key.items():
        sign = 1 if KEYS[key]["direction"] == "min" else -1
        items.sort(key=lambda it: (sign * it[1][0], it[1][3] is None, it[1][3] or "",
                                   it[1][1] if it[1][1] is not None else 1 << 62))
        pid, (v, mid, det, at) = items[0]
        holder_ids = {p for p, e in items if e[0] == v and e[1] == mid}
        holders = sorted({names[p] for p in holder_ids}, key=lambda n: (n.casefold(), n))
        m = matches.get(mid)
        out[key] = {"key": key, "label": KEYS[key]["label"], "person_id": pid,
                    "name": " & ".join(holders), "value": v, "unit": KEYS[key]["unit"],
                    "text": value_text(key, v), "match_id": mid, "date": at,
                    "map": m[0] if m else None, "detail": det,
                    "holder_ids": holder_ids}
    return out


def _stored(conn) -> dict[tuple, tuple]:
    return {(k, p): (v, m, json.loads(d) if d else None, at) for k, p, v, m, d, at in conn.execute(
        "SELECT key, person_id, value, match_id, detail, achieved_at FROM record")}


def _tidy(key: str, v):
    return v if KEYS[key]["unit"] in ("m", "s", "ADR") else int(v)


def _all_time(conn) -> list[dict]:
    """all_time() rows plus ``holder_ids``: the people who share the record."""
    names = dict(conn.execute("SELECT id, display_name FROM person"))
    maps = {mid: (mp,) for mid, mp in conn.execute("SELECT id, map FROM match")}
    rows = {k: (_tidy(k[0], e[0]), *e[1:]) for k, e in _stored(conn).items() if k[0] in KEYS}
    tops = _tops(rows, names, maps)
    return [tops[k] for k in KEYS if k in tops]


def all_time(conn) -> list[dict]:
    """The all-time record per key (KEYS order): {key, label, person_id, name, value, unit,
    text, match_id, date, map, detail}. ``name`` joins holders who share it in one match."""
    return [{k: v for k, v in r.items() if k != "holder_ids"} for r in _all_time(conn)]


def broken_in(conn, match_ids, ticked=None) -> list[str]:
    """Lines for the records whose all-time holder's match is one of match_ids and beats
    the best from matches before the session (first-ever records too). With ``ticked``
    (person ids), only ticked holders are named and records no ticked person holds are
    left out; the old record keeps its value but names only its ticked holders."""
    ids = {int(m) for m in match_ids}
    if not ids:
        return []
    keep = None if ticked is None else {int(p) for p in ticked}
    current = [r for r in _all_time(conn) if r["match_id"] in ids]
    if keep is not None:
        current = [r for r in current if r["holder_ids"] & keep]
    if not current:
        return []
    starts = [s for (s,) in conn.execute(
        f"SELECT started_at FROM match WHERE id IN ({_ph(len(ids))}) AND started_at IS NOT NULL",
        sorted(ids))]
    names = dict(conn.execute("SELECT id, display_name FROM person"))
    old = {}
    if starts:
        maps = {mid: (mp,) for mid, mp in conn.execute("SELECT id, map FROM match")}
        old = _tops(_compute(conn, before=min(starts)), names, maps)
    lines = []
    for r in current:
        prev = old.get(r["key"])
        if prev is not None and not _better(r["key"], r["value"], prev["value"]):
            continue
        name = r["name"]
        if keep is not None:
            name = " & ".join(sorted({names[p] for p in r["holder_ids"] & keep},
                                     key=lambda n: (n.casefold(), n)))
        line = f"{r['label']} ever — {name}, {r['text']}"
        if r["key"] not in _NO_MAP and r["map"]:
            line += f" on {map_name(r['map'])}"
        if prev is not None:
            old_name = prev["name"]
            if keep is not None:      # unticked old holders: the value stays, the name goes
                old_name = " & ".join(sorted({names[p] for p in prev["holder_ids"] & keep},
                                             key=lambda n: (n.casefold(), n)))
            line += (f" (old record {prev['text']}, {old_name})" if old_name
                     else f" (old record {prev['text']})")
        lines.append(line)
    return lines


# ---------------------------------------------------------------- milestones

def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n:,}{suffix}"


def milestones_in(conn, match_ids, ticked=None) -> list[str]:
    """Milestone lines reached in these matches, in time order (see module docstring).
    With ``ticked`` (person ids), only those people's milestones."""
    ids = {int(m) for m in match_ids}
    if not ids:
        return []
    keep = None if ticked is None else {int(p) for p in ticked}
    pids = [r[0] for r in conn.execute(
        "SELECT DISTINCT a.person_id FROM match_player mp JOIN account a ON a.steam_id=mp.steam_id "
        "JOIN person p ON p.id=a.person_id WHERE mp.team='mine' "
        f"AND mp.match_id IN ({_ph(len(ids))})", sorted(ids))]
    if not pids:
        return []
    d = _Data(conn, pids)
    per: dict[int, list[int]] = defaultdict(list)
    for pid, mid in d.rows:
        per[pid].append(mid)
    found = []   # (order, name, kind index, line)
    for pid, mids in per.items():
        if keep is not None and pid not in keep:
            continue
        name = d.names[pid]
        kills = aces = together = 0
        for mid in d.ordered(mids):
            r = d.rows[(pid, mid)]
            steps = []
            nk = kills + (r["kills"] or 0)
            steps.append((0, kills, nk, KILL_MILESTONES, "kill"))
            kills = nk
            if pid != d.me_pid:
                steps.append((1, together, together + 1, TOGETHER_MILESTONES, "match together"))
                together += 1
            na = aces + (r["k5"] or 0)
            steps.append((2, aces, na, ACE_MILESTONES, "ace"))
            aces = na
            if mid not in ids:
                continue
            for kind, a, b, marks, what in steps:
                for t in marks:
                    if a < t <= b:
                        found.append((_order(d.matches[mid][0], mid), name.casefold(), kind, t,
                                      f"Milestone — {name}'s {_ordinal(t)} {what}"))
    found.sort()
    return [f[-1] for f in found]


# ---------------------------------------------------------------- duos & nemeses

def duos_nemeses(conn, match_ids, ticked) -> list[str]:
    """Best duo, trade partner and nemesis lines over the session's event-file matches."""
    ids = sorted({int(m) for m in match_ids})
    pids = sorted({int(p) for p in ticked})
    if not ids or not pids:
        return []
    ev = {r[0] for r in conn.execute(
        f"SELECT DISTINCT match_id FROM fun_stats WHERE match_id IN ({_ph(len(ids))})", ids)}
    if not ev:
        return []
    evl = sorted(ev)
    names = dict(conn.execute(
        f"SELECT id, display_name FROM person WHERE id IN ({_ph(len(pids))})", pids))
    acct = dict(conn.execute(
        f"SELECT steam_id, person_id FROM account WHERE person_id IN ({_ph(len(pids))})", pids))
    result = dict(conn.execute(f"SELECT id, result FROM match WHERE id IN ({_ph(len(evl))})", evl))
    present: dict[int, set[int]] = defaultdict(set)
    for mid, sid in conn.execute(
            f"SELECT match_id, steam_id FROM match_player WHERE team='mine' "
            f"AND match_id IN ({_ph(len(evl))})", evl):
        if sid in acct:
            present[acct[sid]].add(mid)
    lines = []
    key = lambda n: (n.casefold(), n)  # noqa: E731
    duos = []
    people = sorted(present, key=lambda p: key(names[p]))
    for i, a in enumerate(people):
        for b in people[i + 1:]:
            both = present[a] & present[b]
            wins = sum(result.get(m) == "win" for m in both)
            if len(both) >= DUO_MIN and wins:
                duos.append((-wins / len(both), -len(both), key(names[a]), key(names[b]),
                             a, b, wins, len(both)))
    if duos:
        *_, a, b, w, n = min(duos)
        lines.append(f"Best duo — {names[a]} & {names[b]} (won {w} of {n} together)")
    avenged: dict[tuple, int] = defaultdict(int)
    killers: dict[tuple, int] = defaultdict(int)
    for mid, sid, data in conn.execute(
            "SELECT f.match_id, f.steam_id, f.data FROM fun_stats f JOIN match_player mp "
            "ON mp.match_id=f.match_id AND mp.steam_id=f.steam_id AND mp.team='mine' "
            f"WHERE f.match_id IN ({_ph(len(evl))})", evl):
        if sid not in acct:
            continue
        pid = acct[sid]
        row = json.loads(data)
        for t, n in (row.get("avenged") or {}).items():
            other = acct.get(t)
            if other is not None and other != pid:
                avenged[(pid, other)] += n
        for k, n in (row.get("killers") or {}).items():
            if k not in acct:
                killers[(pid, k)] += n
    trades = [(-n, key(names[a]), key(names[b]), a, b, n) for (a, b), n in avenged.items()
              if n >= TRADE_MIN]
    if trades:
        *_, a, b, n = min(trades)
        lines.append(f"Trade partner — {names[a]} avenged {names[b]} {n} times")
    nem = [(-n, k, key(names[p]), p, n) for (p, k), n in killers.items() if n >= NEMESIS_MIN]
    if nem:
        _, enemy, _, p, n = min(nem)
        row = conn.execute(
            "SELECT COALESCE(mp.name_in_match, a.last_seen_name) FROM match_player mp "
            "JOIN match m ON m.id=mp.match_id LEFT JOIN account a ON a.steam_id=mp.steam_id "
            f"WHERE mp.steam_id=? AND mp.match_id IN ({_ph(len(evl))}) "
            "ORDER BY m.started_at DESC, m.id DESC LIMIT 1", (enemy, *evl)).fetchone()
        lines.append(f"Nemesis — {(row and row[0]) or enemy} killed {names[p]} {n} times")
    return lines
