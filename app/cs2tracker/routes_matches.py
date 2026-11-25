"""Match, session, summary and recurring-teammate routes."""
from collections import defaultdict
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cs2tracker import db, stats
from cs2tracker.context import Ctx, owner_required
from cs2tracker.serialize import jsonable, steam_url


def _result(mine, enemy, stored) -> str | None:
    if mine is not None and enemy is not None:
        return "W" if mine > enemy else "L" if mine < enemy else "D"
    return {"win": "W", "loss": "L", "tie": "D"}.get(stored or "")


def match_rows(conn, where: str = "", args: tuple = (), ids: list[int] | None = None) -> list[dict]:
    """Match summaries (newest first) incl. names of people who were on my team."""
    sql = ("SELECT id, started_at, start_estimated, map, mode, my_team_score, enemy_score, "
           "result, source, game_mode FROM match m")
    conds, params = [], list(args)
    if where:
        conds.append(where)
    if ids is not None:
        conds.append(f"m.id IN ({','.join('?' * len(ids))})")
        params += ids
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY m.started_at DESC, m.id DESC"
    rows = conn.execute(sql, params).fetchall()
    people: dict[int, list[str]] = defaultdict(list)
    for mid, name in conn.execute(
            "SELECT DISTINCT mp.match_id, p.display_name FROM match_player mp "
            "JOIN account a ON a.steam_id=mp.steam_id JOIN person p ON p.id=a.person_id "
            "WHERE mp.team='mine' ORDER BY p.display_name"):
        people[mid].append(name)
    return [{"id": r[0], "started_at": r[1], "start_estimated": bool(r[2]), "map": r[3],
             "mode": r[4], "my_team_score": r[5], "enemy_score": r[6],
             "result": _result(r[5], r[6], r[7]), "source": r[8], "game_mode": r[9], "people": people[r[0]]}
            for r in rows]


def _kill_summaries(conn, match_id: int) -> dict[str, dict]:
    by: dict[str, dict] = {}
    for att, weapon, hs, wb in conn.execute(
            "SELECT attacker_steam_id, weapon, headshot, wallbang FROM kill WHERE match_id=? "
            "AND attacker_steam_id IS NOT NULL AND attacker_steam_id != victim_steam_id "
            "AND team_kill=0", (match_id,)):
        s = by.setdefault(att, {"kills": 0, "headshots": 0, "wallbangs": 0, "weapons": {}})
        s["kills"] += 1
        s["headshots"] += hs
        s["wallbangs"] += wb
        s["weapons"][weapon] = s["weapons"].get(weapon, 0) + 1
    for s in by.values():
        top = max(s["weapons"].items(), key=lambda kv: (kv[1], kv[0]))[0]
        s["fav_weapon"] = stats.weapon_name(top)
    return by


class SummaryIn(BaseModel):
    match_ids: list[int]
    person_ids: list[int]
    roast_level: str = "trash"       # trash | friendly | off (localStorage cs2t.roastLevel)


def build(ctx: Ctx) -> APIRouter:
    r = APIRouter()
    need_owner = owner_required(ctx)

    @r.get("/api/matches")
    def list_matches(since: date | None = None, until: date | None = None,
                     group: int | None = None, map: str | None = None):
        where, args = [], []
        if since:
            where.append("substr(m.started_at,1,10) >= ?")
            args.append(since.isoformat())
        if until:
            where.append("substr(m.started_at,1,10) <= ?")
            args.append(until.isoformat())
        if map:
            where.append("m.map = ?")
            args.append(map)
        if group is not None:
            where.append("EXISTS (SELECT 1 FROM match_player mp JOIN account a "
                         "ON a.steam_id=mp.steam_id JOIN person_group pg ON pg.person_id=a.person_id "
                         "WHERE mp.match_id=m.id AND mp.team='mine' AND pg.group_id=?)")
            args.append(group)
        with ctx.db() as conn:
            return match_rows(conn, " AND ".join(where), tuple(args))

    @r.get("/api/matches/{match_id}")
    def match_detail(match_id: int):
        with ctx.db() as conn:
            m = db.get_match(conn, match_id)
            if m is None:
                raise HTTPException(404, "no such match")
            m.pop("info_raw", None)
            ks = _kill_summaries(conn, match_id)
            owners = {sid: (pid, name) for sid, pid, name in conn.execute(
                "SELECT a.steam_id, p.id, p.display_name FROM account a "
                "JOIN person p ON p.id=a.person_id")}
            players = []
            for p in db.get_match_players(conn, match_id):
                sid = p["steam_id"]
                pid, pname = owners.get(sid, (None, None))
                players.append({**p, "person_id": pid, "person_name": pname,
                                "steam_url": steam_url(sid),
                                "kills_summary": ks.get(sid, {
                                    "kills": 0, "headshots": 0, "wallbangs": 0, "weapons": {},
                                    "fav_weapon": "-"})})
            return jsonable({"match": m, "players": players})

    @r.get("/api/recurring", dependencies=[need_owner])
    def recurring():
        with ctx.db() as conn:
            return stats.recurring_teammates(conn, ctx.me(conn))

    @r.get("/api/sessions")
    def list_sessions():
        with ctx.db() as conn:
            out = []
            groups = stats.sessions(conn)
            # one match_rows call for every session (it scans the people rows once)
            by_id = {m["id"]: m for m in match_rows(conn)} if groups else {}
            for ids in groups:
                ms = sorted((by_id[i] for i in ids if i in by_id),
                            key=lambda m: (m["started_at"], m["id"]))
                out.append({"match_ids": [m["id"] for m in ms], "started_at": ms[0]["started_at"],
                            "matches": ms})
            return out

    @r.get("/api/stats/best-mates", dependencies=[need_owner])
    def best_mates():
        with ctx.db() as conn:
            return stats.best_mates(conn, ctx.me(conn))

    @r.post("/api/summary", dependencies=[need_owner])
    def summary(body: SummaryIn):
        if body.roast_level not in stats.ROAST_LEVELS:
            raise HTTPException(400, "roast_level must be trash, friendly or off")
        with ctx.db() as conn:
            out = jsonable(stats.summary(conn, body.match_ids, body.person_ids,
                                         roast_level=body.roast_level))
        for row in out["rows"]:
            row.pop("group", None)  # legacy text column: not part of the v1.1 API
        return out

    return r
