"""Dashboard tab data: the owner's counted matches in the compact format the page reads.

One entry per counted match the owner played (any of the owner's accounts), oldest first:
``{id, t, mo, mp, w, du, s: [mine, enemy], T: [myTeam, enemyTeam], D?}``. A player is
``[id, name, ping, kills, assists, deaths, mvps, hs_percent, scoreboard_score]``; ``id`` is a
Steam64 string, with every account of a person shown as that person's canonical account (the
owner as ``me``), so alt accounts merge. ``ping`` is always null: the app doesn't store it.
``D`` is the owner's demo stats (fun_stats) plus ADR and rating, only for demo matches.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime

from cs2tracker import db, records
from cs2tracker.steam_history import MAP_NAMES

_MAP_SHOWN = {v: k for k, v in MAP_NAMES.items()}       # de_dust2 -> Dust II
_MODE_SHOWN = {"competitive": "Competitive", "premier": "Premier"}


def _map(m: str | None) -> str:
    return _MAP_SHOWN.get(m or "", records.map_name(m))


def _mode(game_mode: str | None, mode: str | None) -> str:
    if game_mode in _MODE_SHOWN:
        return _MODE_SHOWN[game_mode]
    text = (mode or "").strip()
    if text:
        return _MODE_SHOWN.get(text.lower(), text if text.isupper() else text.title())
    return "Unknown 5v5"


def _canonical(conn, me: str) -> tuple[dict[str, str], dict[str, str]]:
    """(account -> shown id, shown id -> person name) for every linked account."""
    by_person: dict[int, list[str]] = defaultdict(list)
    names: dict[int, str] = dict(conn.execute("SELECT id, display_name FROM person"))
    for sid, pid in conn.execute("SELECT steam_id, person_id FROM account WHERE person_id IS NOT NULL"):
        by_person[pid].append(sid)
    canon, shown = {}, {}
    for pid, sids in by_person.items():
        head = me if me in sids else min(sids)
        for s in sids:
            canon[s] = head
        shown[head] = names.get(pid)
    canon.setdefault(me, me)
    return canon, shown


def _remap(d: dict | None, canon: dict[str, str]) -> dict:
    out: dict[str, int] = {}
    for k, v in (d or {}).items():
        key = canon.get(str(k), str(k))
        out[key] = out.get(key, 0) + v
    return out


def build(conn, me: str) -> dict:
    canon, shown = _canonical(conn, me)
    mine = [s for s, head in canon.items() if head == me]
    ph = ",".join("?" * len(mine))
    matches = conn.execute(
        "SELECT m.id, m.started_at, m.game_mode, m.mode, m.map, m.wait_seconds, m.duration, "
        "m.my_team_score, m.enemy_score FROM match m "
        f"WHERE m.id IN ({db.COUNTED_SQL}) AND m.started_at IS NOT NULL AND EXISTS ("
        f"SELECT 1 FROM match_player mp WHERE mp.match_id=m.id AND mp.steam_id IN ({ph})) "
        "ORDER BY m.started_at, m.id", mine).fetchall()
    if not matches:
        return {"me": me, "matches": []}
    ids = [r[0] for r in matches]
    players: dict[int, list] = defaultdict(list)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        players_rows = conn.execute(
            "SELECT mp.match_id, mp.steam_id, mp.team, COALESCE(mp.name_in_match, a.last_seen_name), "
            "mp.kills, mp.assists, mp.deaths, mp.mvps, mp.hs_percent, mp.scoreboard_score, "
            "mp.adr, mp.hltv_rating FROM match_player mp LEFT JOIN account a ON a.steam_id=mp.steam_id "
            f"WHERE mp.match_id IN ({','.join('?' * len(chunk))}) ORDER BY mp.match_id, mp.steam_id",
            chunk).fetchall()
        for row in players_rows:
            players[row[0]].append(row[1:])
    fun: dict[int, dict] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for mid, data in conn.execute(
                f"SELECT match_id, data FROM fun_stats WHERE steam_id IN ({ph}) "
                f"AND match_id IN ({','.join('?' * len(chunk))})", [*mine, *chunk]):
            fun.setdefault(mid, json.loads(data))
    out = []
    for mid, started, gm, mode, mp, wait, dur, my_s, en_s in matches:
        teams: dict[str, list] = {"mine": [], "enemy": []}
        me_line = None
        for sid, team, name, k, a, d, mvps, hsp, score, adr, rating in players[mid]:
            pid = canon.get(sid, sid)
            if pid == me and me_line is None and team == "mine":
                me_line = (adr, rating)
            teams.setdefault(team, []).append(
                [pid, shown.get(pid) or name or sid, None, k, a, d, mvps, hsp, score])
        entry = {"id": mid, "t": int(datetime.fromisoformat(started).timestamp()),
                 "mo": _mode(gm, mode), "mp": _map(mp), "w": wait, "du": dur,
                 "s": [my_s, en_s], "T": [teams["mine"], teams["enemy"]]}
        if mid in fun:
            dd = dict(fun[mid])
            for key in ("victims", "killers", "avenged"):
                if key in dd:
                    dd[key] = _remap(dd[key], canon)
            adr, rating = me_line or (None, None)
            if rating is not None:
                dd["rating"] = rating
            if adr is not None:
                dd["adr"] = adr
            elif dd.get("rounds_played"):
                dd["adr"] = round(dd.get("damage", 0) / dd["rounds_played"], 1)
            entry["D"] = dd
        out.append(entry)
    return {"me": me, "matches": out}
