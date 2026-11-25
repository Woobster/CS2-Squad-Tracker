"""Stats, sessions and summaries.

Only ``team='mine'`` rows count towards a person's stats: "friends' stats" means
games played on my team, never games against them. ``person_leetify_ref`` never
feeds per-match stats; only ``games_together`` reads it (Leetify's all-time count).
"""
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from cs2tracker import db
from cs2tracker.demo import KillRow, favourite_weapon

WEAPON_NAMES = {"ak47": "AK-47", "m4a1_silencer": "M4A1-S", "m4a1": "M4A4", "awp": "AWP"}


@dataclass
class PersonStats:
    person_id: int
    name: str
    group: str | None
    matches: int
    kills: int
    deaths: int
    kd: float | None
    adr: float | None
    k2: int
    k3: int
    k4: int
    k5: int
    fav_weapon: str | None
    wins: int
    assists: int = 0
    utility_dmg: int | None = None
    self_dmg: int | None = None
    knife_kills: int | None = None
    fav_weapon_kills: int | None = None
    played: float = 0.0


def weapon_name(w: str | None) -> str:
    if not w:
        return "-"
    return WEAPON_NAMES.get(w, w[:1].upper() + w[1:])


def _ph(n: int) -> str:
    return ",".join("?" * n)


def is_partial(rounds_played: int | None, my_score: int | None, enemy_score: int | None) -> bool:
    """Played under half of the match's rounds (unknowable when any value is NULL)."""
    if rounds_played is None or my_score is None or enemy_score is None:
        return False
    return rounds_played * 2 < my_score + enemy_score


def played_text(played: float) -> str:
    """4 -> "4", 4.5 -> "4½", 0.5 -> "½"."""
    whole = int(played)
    if played - whole >= 0.5:
        return f"{whole}½" if whole else "½"
    return str(whole)


def _add_opt(cur: int | None, v: int | None) -> int | None:
    if v is None:
        return cur
    return (cur or 0) + v


def person_stats(conn, person_ids: list[int] | None = None, match_ids: list[int] | None = None,
                 since: date | None = None, until: date | None = None) -> list[PersonStats]:
    """Per-person totals over my-team rows, summed across all linked accounts.

    ADR is weighted by rounds played (falling back to my_team_score + enemy_score when
    rounds_played is NULL/0). Only people with at least one matching row are returned,
    ordered by kills descending then name. since/until compare the UTC date of
    started_at (inclusive); matches without a start are excluded when either is set.
    """
    where = ["mp.team='mine'", "a.person_id IS NOT NULL"]
    args: list = []
    if person_ids is not None:
        where.append(f"a.person_id IN ({_ph(len(person_ids))})")
        args += list(person_ids)
    if match_ids is not None:
        where.append(f"mp.match_id IN ({_ph(len(match_ids))})")
        args += list(match_ids)
    if since is not None:
        where.append("substr(m.started_at,1,10) >= ?")
        args.append(since.isoformat())
    if until is not None:
        where.append("substr(m.started_at,1,10) <= ?")
        args.append(until.isoformat())
    rows = conn.execute(
        "SELECT a.person_id, p.display_name, p.\"group\", mp.match_id, mp.steam_id, mp.won, "
        "mp.kills, mp.deaths, mp.adr, mp.k2, mp.k3, mp.k4, mp.k5, mp.rounds_played, "
        "m.my_team_score, m.enemy_score, mp.assists, mp.utility_dmg, mp.self_dmg, "
        "mp.knife_kills "
        "FROM match_player mp JOIN account a ON a.steam_id=mp.steam_id "
        "JOIN person p ON p.id=a.person_id JOIN match m ON m.id=mp.match_id "
        "WHERE " + " AND ".join(where), args).fetchall()

    acc: dict[int, dict] = {}
    for (pid, name, grp, mid, _sid, won, k, d, adr, k2, k3, k4, k5, rp, ms, es,
         ast, util, selfd, knife) in rows:
        s = acc.setdefault(pid, dict(name=name, group=grp, matches=set(), wins=set(), kills=0,
                                     deaths=0, k2=0, k3=0, k4=0, k5=0, adr_w=0.0, w=0.0,
                                     assists=0, util=None, selfd=None, knife=None, played={}))
        s["matches"].add(mid)
        # a person counts once per match: full beats partial (two accounts in one match)
        part = 0.5 if is_partial(rp, ms, es) else 1.0
        s["played"][mid] = max(s["played"].get(mid, 0.0), part)
        s["assists"] += ast or 0
        s["util"] = _add_opt(s["util"], util)
        s["selfd"] = _add_opt(s["selfd"], selfd)
        s["knife"] = _add_opt(s["knife"], knife)
        if won:
            s["wins"].add(mid)
        s["kills"] += k or 0
        s["deaths"] += d or 0
        s["k2"] += k2 or 0
        s["k3"] += k3 or 0
        s["k4"] += k4 or 0
        s["k5"] += k5 or 0
        if adr is not None:
            w = rp if rp else (ms or 0) + (es or 0)
            if w:
                s["adr_w"] += adr * w
                s["w"] += w
    favs = _fav_weapons(conn, list(acc), match_ids, since, until)
    out = []
    for pid, s in acc.items():
        fav, fav_kills = favs.get(pid, (None, None))
        out.append(PersonStats(
            person_id=pid, name=s["name"], group=s["group"], matches=len(s["matches"]),
            kills=s["kills"], deaths=s["deaths"],
            kd=round(s["kills"] / s["deaths"], 2) if s["deaths"] else None,
            adr=s["adr_w"] / s["w"] if s["w"] else None,
            k2=s["k2"], k3=s["k3"], k4=s["k4"], k5=s["k5"],
            fav_weapon=fav, wins=len(s["wins"]),
            assists=s["assists"], utility_dmg=s["util"], self_dmg=s["selfd"],
            knife_kills=s["knife"], fav_weapon_kills=fav_kills,
            played=sum(s["played"].values())))
    out.sort(key=lambda r: (-r.kills, r.name))
    return out


def _fav_weapons(conn, pids, match_ids, since, until) -> dict[int, tuple[str, int]]:
    """(favourite weapon, kills with it) per person from the kill table (real kills on
    my-team rows only: no team kills, no suicides, the person's own accounts)."""
    if not pids:
        return {}
    where = ["mp.team='mine'", f"a.person_id IN ({_ph(len(pids))})", "k.team_kill=0",
             "k.attacker_steam_id IS NOT NULL", "k.attacker_steam_id != k.victim_steam_id"]
    args: list = list(pids)
    if match_ids is not None:
        where.append(f"k.match_id IN ({_ph(len(match_ids))})")
        args += list(match_ids)
    if since is not None:
        where.append("substr(m.started_at,1,10) >= ?")
        args.append(since.isoformat())
    if until is not None:
        where.append("substr(m.started_at,1,10) <= ?")
        args.append(until.isoformat())
    rows = conn.execute(
        "SELECT a.person_id, k.match_id, k.round, k.tick, k.attacker_steam_id, "
        "k.victim_steam_id, k.weapon FROM kill k "
        "JOIN match_player mp ON mp.match_id=k.match_id AND mp.steam_id=k.attacker_steam_id "
        "JOIN account a ON a.steam_id=mp.steam_id JOIN match m ON m.id=k.match_id "
        "WHERE " + " AND ".join(where) +
        " ORDER BY COALESCE(m.started_at,''), k.match_id, k.round, k.tick, k.id", args).fetchall()
    per: dict[int, list[KillRow]] = defaultdict(list)
    for pid, _mid, rnd, tick, att, vic, weapon in rows:
        per[pid].append(KillRow(rnd, tick, att, vic, None, weapon, False, False, False,
                                None, (0.0, 0.0, 0.0)))
    return {pid: (w, sum(k.weapon == w for k in ks))
            for pid, ks in per.items() if (w := favourite_weapon(ks))}


def sessions(conn, gap_hours: float = 2) -> list[list[int]]:
    """Group matches into play sessions, newest first (ids ascending by time inside).

    Exact-time matches join while consecutive gaps are < gap_hours. Estimated-time
    matches form one session per UTC calendar date and never merge with exact ones.
    """
    rows = conn.execute("SELECT id, started_at, start_estimated FROM match "
                        "WHERE started_at IS NOT NULL ORDER BY started_at, id").fetchall()
    gap = timedelta(hours=gap_hours)
    groups: list[tuple[datetime, list[int]]] = []   # (latest start, ids)
    est: dict[str, list] = {}
    cur: list[int] = []
    last: datetime | None = None
    for mid, started, estimated in rows:
        t = datetime.fromisoformat(started)
        if estimated:
            est.setdefault(started[:10], []).append((t, mid))
            continue
        if last is not None and t - last < gap:
            cur.append(mid)
        else:
            if cur:
                groups.append((last, cur))
            cur = [mid]
        last = t
    if cur:
        groups.append((last, cur))
    for items in est.values():
        groups.append((max(t for t, _ in items), [mid for _, mid in items]))
    groups.sort(key=lambda g: (g[0], g[1][-1]), reverse=True)
    return [ids for _, ids in groups]


def players_in(conn, match_ids: list[int]) -> list[int]:
    """Person ids (sorted) with a my-team row in any of the matches; enemies and unlinked
    accounts are excluded."""
    if not match_ids:
        return []
    rows = conn.execute(
        "SELECT DISTINCT a.person_id FROM match_player mp JOIN account a "
        "ON a.steam_id=mp.steam_id WHERE mp.team='mine' AND a.person_id IS NOT NULL "
        f"AND mp.match_id IN ({_ph(len(match_ids))}) ORDER BY a.person_id", list(match_ids))
    return [r[0] for r in rows]


def recurring_teammates(conn, me: str, min_matches: int = 3) -> list[dict]:
    """Unlinked accounts (no person) that were on my team in >= min_matches matches."""
    rows = conn.execute(
        "SELECT a.steam_id, a.last_seen_name, COUNT(DISTINCT mp.match_id) c "
        "FROM match_player mp JOIN account a ON a.steam_id=mp.steam_id "
        "WHERE mp.team='mine' AND a.person_id IS NULL AND a.steam_id != ? "
        "GROUP BY a.steam_id HAVING c >= ? ORDER BY c DESC, a.steam_id", (me, min_matches))
    return [{"steam_id": s, "name": n, "count": c} for s, n, c in rows]


def _my_person(conn, me: str | None) -> int | None:
    me = me if me is not None else (db.get_setting(conn, "me_steam_id") or None)
    row = conn.execute("SELECT person_id FROM account WHERE steam_id=?", (me,)).fetchone()
    return row[0] if row else None


def games_together(conn, me: str | None = None) -> dict[int, int]:
    """All-time games on my team per person (my own person excluded).

    People with a Leetify ref: the ref games (summed over their accounts) plus app
    matches after the ``leetify_cutoff`` setting (earlier ones are inside Leetify's
    number). People without a ref: every app match. With Steam history (v1.3) the app may
    hold more of those games than Leetify counted, so the result is the larger of that
    rule and the number of app matches with the person on my team: it never drops.
    ``me`` defaults to the ``me_steam_id`` setting.
    """
    cutoff = datetime.fromisoformat(
        db.get_setting(conn, "leetify_cutoff", db.LEETIFY_CUTOFF_DEFAULT))
    refs = {pid: games for pid, games in conn.execute(
        "SELECT person_id, SUM(COALESCE(games, 0)) FROM person_leetify_ref GROUP BY person_id")}
    out = {pid: 0 for (pid,) in conn.execute("SELECT id FROM person")}
    app_games = dict.fromkeys(out, 0)
    for pid, games in refs.items():
        if pid in out:
            out[pid] = games
    for pid, _mid, started in conn.execute(
            "SELECT DISTINCT a.person_id, m.id, m.started_at FROM match_player mp "
            "JOIN account a ON a.steam_id=mp.steam_id JOIN match m ON m.id=mp.match_id "
            "WHERE mp.team='mine' AND a.person_id IS NOT NULL"):
        if pid not in out:
            continue
        app_games[pid] += 1
        if pid in refs and (started is None or datetime.fromisoformat(started) <= cutoff):
            continue
        out[pid] += 1
    out = {pid: max(n, app_games[pid]) for pid, n in out.items()}
    out.pop(_my_person(conn, me), None)
    return out


def best_mates(conn, me: str, n: int | None = None) -> list[dict]:
    """Everyone with games together > 0, most games first (then name); n truncates."""
    games = games_together(conn, me)
    names = dict(conn.execute("SELECT id, display_name FROM person").fetchall())
    out = [{"person_id": pid, "name": names[pid], "games": g}
           for pid, g in games.items() if g > 0]
    out.sort(key=lambda m: (-m["games"], m["name"].casefold(), m["name"], m["person_id"]))
    return out if n is None else out[:n]


def _map_name(m: str | None) -> str:
    if not m:
        return "?"
    m = m[3:] if m.startswith("de_") else m
    return m[:1].upper() + m[1:]


def _fmt(v: float) -> str:
    return f"{v:.2f}"


def _top(vals: list[tuple[str, float | int | None]]) -> tuple[float | int, str] | None:
    """(top value, names joined with " & ") or None when nobody has a non-zero value."""
    vals = [(n, v) for n, v in vals if v is not None]
    if not vals:
        return None
    top = max(v for _, v in vals)
    if top == 0:
        return None
    return top, " & ".join(n for n, v in vals if v == top)


def _best(rows: list[PersonStats], total: int, ticked: int) -> list[str]:
    """Best-of lines in the fixed label order; each omitted when nobody has the stat."""
    out = []

    def add(label, vals, fmt=str):
        if (t := _top(vals)) is not None:
            out.append(f"{label} — {t[1]} ({fmt(t[0])})")

    add("Most kills", [(r.name, r.kills) for r in rows])
    # Best K/D is only open to people who played at least half of the matches.
    open_ = [r for r in rows if r.played * 2 >= total]
    if (t := _top([(r.name, r.kd) for r in open_])) is not None:
        line = f"Best K/D — {t[1]} ({_fmt(t[0])})"
        skipped = [r for r in rows if r not in open_ and r.kd is not None and r.kd > t[0]]
        if skipped:
            x = max(skipped, key=lambda r: r.kd)   # rows are in table order: ties -> first
            line += (f" · min. {math.ceil(total / 2)} of {total} matches "
                     f"({x.name} {_fmt(x.kd)} in {played_text(x.played)})")
        out.append(line)
    add("Most multikills", [(r.name, r.k3 + r.k4 + r.k5) for r in rows])
    add("Most assists", [(r.name, r.assists) for r in rows])
    add("Most utility damage", [(r.name, r.utility_dmg) for r in rows])
    add("Most self-damage", [(r.name, r.self_dmg) for r in rows])
    add("Knife kills", [(r.name, r.knife_kills) for r in rows])
    if ticked >= 2 and total:
        names = [r.name for r in rows if r.played == total]
        if names:
            out.append(f"Ever-present — {' & '.join(names)} ({played_text(total)}/{total})")
    return out


_RESULT = {"win": "W", "loss": "L", "tie": "D"}


def _lineups(conn, ids: list[int], person_ids: list[int]) -> list[dict]:
    """Per match (in the given order): who of the ticked people played it, fully or in
    part, and how many other my-team players there were."""
    ticked = set(person_ids)
    out = []
    for mid in ids:
        mp, started, mine, enemy, res = conn.execute(
            "SELECT map, started_at, my_team_score, enemy_score, result FROM match WHERE id=?",
            (mid,)).fetchone()
        cells = {pid: "none" for pid in person_ids}
        others = 0
        for pid, rp in conn.execute(
                "SELECT a.person_id, mp.rounds_played FROM match_player mp "
                "JOIN account a ON a.steam_id=mp.steam_id "
                "WHERE mp.match_id=? AND mp.team='mine'", (mid,)):
            if pid not in ticked:
                others += 1
            elif cells[pid] != "full":
                cells[pid] = "part" if is_partial(rp, mine, enemy) else "full"
        r = _RESULT.get(res or "")
        if r is None and mine is not None and enemy is not None:
            r = "W" if mine > enemy else "L" if mine < enemy else "D"
        out.append({"match_id": mid, "map": _map_name(mp), "started_at": started,
                    "score": f"{mine}–{enemy}" if mine is not None and enemy is not None
                    else "?", "result": r or "?", "cells": cells, "others": others})
    return out


def _lineup_line(lu: dict, rows: list[PersonStats]) -> str:
    names = [r.name + (" (part)" if lu["cells"].get(r.person_id) == "part" else "")
             for r in rows if lu["cells"].get(r.person_id, "none") != "none"]
    who = ", ".join(names)
    if lu["others"]:
        who = f"{who} +{lu['others']}" if who else f"+{lu['others']}"
    return f"{lu['map']} {lu['score']} {lu['result']} — {who or '-'}"


def _person_line(r: PersonStats) -> str:
    kd = "-" if r.kd is None else f"{r.kd:.2f}"
    adr = "-" if r.adr is None else round(r.adr)
    fav = weapon_name(r.fav_weapon)
    if r.fav_weapon:
        fav += f" ({r.fav_weapon_kills})"
    return (f"{r.name}: K {r.kills} · D {r.deaths} · A {r.assists} · K/D {kd} · ADR {adr}"
            f" · 2K {r.k2} · 3K {r.k3} · 4K {r.k4} · Aces {r.k5} · Fav {fav}")


ROAST_LEVELS = ("trash", "friendly", "off")
ROLE_AWP_SHARE = 0.35
ROLE_ENTRY_SHARE = 0.25
ROLE_LURK_SHARE = 0.20
DASH = "–"


def _fun_per_match(conn, ids: list[int]) -> list[tuple[int, dict[str, dict]]]:
    """(match id, {steam_id: fun stats}) for the matches (in the given order) that have
    fun_stats rows; only my-team rows are kept, as for every other Summary stat."""
    if not ids:
        return []
    per: dict[int, dict[str, dict]] = {}
    for mid in {r[0] for r in conn.execute(
            f"SELECT DISTINCT match_id FROM fun_stats WHERE match_id IN ({_ph(len(ids))})",
            list(ids))}:
        per[mid] = {}
    for mid, sid, data in conn.execute(
            "SELECT f.match_id, f.steam_id, f.data FROM fun_stats f JOIN match_player mp "
            "ON mp.match_id=f.match_id AND mp.steam_id=f.steam_id AND mp.team='mine' "
            f"WHERE f.match_id IN ({_ph(len(ids))})", list(ids)):
        per[mid][sid] = json.loads(data)
    return [(mid, per[mid]) for mid in ids if mid in per]


def roles(totals: dict[int, dict]) -> dict[int, str]:
    """Role tonight per person from their session fun-stats totals (spec v1.3 §5), the
    first that applies:

    1. AWPer: awp_kills >= 35% of kills;
    2. Entry: opening attempts (opening kills + opening deaths) >= 25% of rounds played;
    3. Support: (flash assists + enemies blinded) per round is > 0 and in the top two of the
       session (ties included), and kills per round below the session mean;
    4. Lurker: last alive in >= 20% of rounds played;
    5. otherwise Rifler.
    """
    def per_round(t, v):
        r = t.get("rounds_played") or 0
        return v / r if r else 0.0
    sup = {p: per_round(t, (t.get("flash_assists") or 0) + (t.get("enemies_blinded") or 0))
           for p, t in totals.items()}
    kpr = {p: per_round(t, t.get("kills") or 0) for p, t in totals.items()}
    mean_kpr = sum(kpr.values()) / len(kpr) if kpr else 0.0
    out = {}
    for p, t in totals.items():
        kills = t.get("kills") or 0
        if kills and (t.get("awp_kills") or 0) >= ROLE_AWP_SHARE * kills - 1e-9:
            out[p] = "AWPer"
        elif per_round(t, (t.get("opening_kills") or 0) + (t.get("opening_deaths") or 0)) \
                >= ROLE_ENTRY_SHARE - 1e-9 and t.get("rounds_played"):
            out[p] = "Entry"
        elif (sup[p] > 0 and sum(v > sup[p] + 1e-12 for v in sup.values()) < 2
              and kpr[p] < mean_kpr - 1e-12):
            out[p] = "Support"
        elif t.get("rounds_played") and \
                per_round(t, t.get("last_alive") or 0) >= ROLE_LURK_SHARE - 1e-9:
            out[p] = "Lurker"
        else:
            out[p] = "Rifler"
    return out


def _steam_hs(conn, ids: list[int], person_ids: list[int]) -> dict[int, float]:
    """HS% from the Steam scoreboard (``match_player.hs_percent``) weighted by kills."""
    if not ids or not person_ids:
        return {}
    num: dict[int, float] = defaultdict(float)
    den: dict[int, int] = defaultdict(int)
    for pid, hs, k in conn.execute(
            "SELECT a.person_id, mp.hs_percent, mp.kills FROM match_player mp "
            "JOIN account a ON a.steam_id=mp.steam_id WHERE mp.team='mine' "
            f"AND mp.hs_percent IS NOT NULL AND mp.match_id IN ({_ph(len(ids))}) "
            f"AND a.person_id IN ({_ph(len(person_ids))})", [*ids, *person_ids]):
        num[pid] += hs * (k or 0)
        den[pid] += k or 0
    return {pid: round(num[pid] / den[pid], 1) for pid in den if den[pid]}


def _scoreboard(conn, ids, rows: list[PersonStats], totals: dict[int, dict]) -> dict[int, dict]:
    """The v1.3 columns per person in ``rows``: None where they have no fun-stats rows in the
    session, except HS%, which then comes from the Steam scoreboard (weighted by kills)."""
    role = roles(totals)
    steam_hs = _steam_hs(conn, ids, [r.person_id for r in rows if r.person_id not in totals])
    out = {}
    for r in rows:
        t = totals.get(r.person_id)
        if t is None:
            out[r.person_id] = {"hs_pct": steam_hs.get(r.person_id), "kast_pct": None,
                                "opening_w": None, "opening_l": None, "clutches_won": None,
                                "blinded": None, "rating": None, "role": None}
            continue
        rp = t.get("rounds_played") or 0
        out[r.person_id] = {
            "hs_pct": round(t["hs_pct"], 1) if t.get("hs_pct") is not None else None,
            "kast_pct": round(100 * (t.get("kast_rounds") or 0) / rp, 1) if rp else None,
            "opening_w": t.get("opening_kills") or 0,
            "opening_l": t.get("opening_deaths") or 0,
            "clutches_won": t.get("clutches_won") or 0,
            "blinded": t.get("enemies_blinded") or 0,
            "rating": round(t.get("rating") or 0.0, 2),
            "role": role[r.person_id]}
    return out


def _board_text(sb: dict) -> str:
    """The per-person line's v1.3 tail; "–" for a column the person has no data for."""
    def v(x, fmt="{}"):
        return DASH if x is None else fmt.format(x)
    hs = None if sb["hs_pct"] is None else round(sb["hs_pct"])
    kast = None if sb["kast_pct"] is None else round(sb["kast_pct"])
    opening = None if sb["opening_w"] is None else f"{sb['opening_w']}{DASH}{sb['opening_l']}"
    return (f" · HS% {v(hs)} · KAST {v(kast, '{}%')} · Opening {v(opening)}"
            f" · Clutches {v(sb['clutches_won'])} · Blinded {v(sb['blinded'])}"
            f" · Rating {v(sb['rating'], '{:.2f}')} · Role {v(sb['role'])}")


def _enemy_names(conn, ids: list[int]) -> dict[str, str]:
    """Latest in-game name per enemy Steam ID in these matches (bots left out)."""
    if not ids:
        return {}
    out: dict[str, str] = {}
    for sid, name in conn.execute(
            "SELECT mp.steam_id, COALESCE(mp.name_in_match, a.last_seen_name) "
            "FROM match_player mp JOIN match m ON m.id=mp.match_id "
            "LEFT JOIN account a ON a.steam_id=mp.steam_id "
            f"WHERE mp.team='enemy' AND mp.match_id IN ({_ph(len(ids))}) "
            "ORDER BY COALESCE(m.started_at,''), m.id", list(ids)):
        if name and sid != "0" and not name.startswith("[BOT]"):
            out[sid] = name
    return out


def summary(conn, match_ids: list[int], person_ids: list[int],
            roast_level: str = "trash") -> dict:
    """Chat-ready summary of matches (chronological) for the given people.

    Returns matches, rows (PersonStats, table order), total, lineups, best and text, and
    (v1.3) fun_matches, scoreboard ({person_id: columns}), awards (cards), roasts (lines),
    records_broken, milestones, duos and seen_again (lines).

    Awards, roasts and the scoreboard columns use only the selected matches with fun stats
    (``fun_matches``); awards count played/total over those matches. Roasts use the
    Summary's played/total over all selected matches, and template 14 ("still topped the
    K/D") is judged on the Summary's K/D over all matches (kills / max(deaths, 1)), so it
    agrees with the table; a person without fun stats can top it but is never roasted (the
    roast is then skipped). ``roast_level`` is trash, friendly or off.
    """
    if roast_level not in ROAST_LEVELS:
        raise ValueError(f"roast_level must be one of {', '.join(ROAST_LEVELS)}")
    from cs2tracker import awards, records, rivals, roasts   # roasts imports this module
    ids = sorted(dict.fromkeys(match_ids), key=lambda i: (conn.execute(
        "SELECT COALESCE(started_at,'') FROM match WHERE id=?", (i,)).fetchone()[0], i))
    person_ids = list(dict.fromkeys(person_ids))
    matches = []
    for mid in ids:
        mp, started, mine, enemy, res = conn.execute(
            "SELECT map, started_at, my_team_score, enemy_score, result FROM match WHERE id=?",
            (mid,)).fetchone()
        if mine is not None and enemy is not None:
            r = "W" if mine > enemy else "L" if mine < enemy else "D"
        else:
            r = _RESULT.get(res or "", "?")
        matches.append(dict(id=mid, date=started[:10] if started else None,
                            map=_map_name(mp),
                            score=f"{mine}-{enemy}" if mine is not None else "?", result=r))
    total = len(ids)
    rows = person_stats(conn, person_ids=person_ids, match_ids=ids)
    lineups = _lineups(conn, ids, person_ids)
    best = _best(rows, total, len(person_ids))
    counts = [f"{sum(m['result'] == c for m in matches)}{c}" for c in "WLD"
              if any(m["result"] == c for m in matches)]
    day = next((m["date"] for m in matches if m["date"]), "?")
    lines = [f"CS2 session — {day} ({total} match{'es' if total != 1 else ''}"
             + (f": {' '.join(counts)}" if counts else "") + ")",
             " · ".join(f"{m['map']} {m['score']} {m['result']}" for m in matches)]
    # ---- v1.3: fun stats (event-file matches only), awards, roasts, records, rivals
    ticked = set(person_ids)
    fun = _fun_per_match(conn, ids)
    fun_ids = [mid for mid, _ in fun]
    per_match = [m for _, m in fun]
    person_of = {sid: pid for sid, pid in conn.execute(
        "SELECT steam_id, person_id FROM account WHERE person_id IS NOT NULL")}
    totals = awards.session_totals(per_match, person_of, ticked) if per_match else {}
    names = {r.person_id: r.name for r in rows}
    fun_names = {p: n for p, n in names.items() if p in totals}
    scoreboard = _scoreboard(conn, ids, rows, totals)
    played_fun = ({r.person_id: r.played
                   for r in person_stats(conn, person_ids=person_ids, match_ids=fun_ids)}
                  if fun_ids else {})
    cards = awards.pick(totals, fun_names, played_fun, len(fun_ids)) if totals else []
    roast_lines: list[str] = []
    if totals and roast_level != "off":
        # template 14 judges the Summary's K/D (all matches); people without fun stats
        # take part in that "top" only (no name: never roasted)
        r_totals = {}
        for r in rows:
            kd_all = r.kills / max(r.deaths, 1)
            r_totals[r.person_id] = ({**totals[r.person_id], "kd": kd_all}
                                     if r.person_id in totals else {"matches": 0, "kd": kd_all})
        context = {**roasts.team_context(per_match, person_of, ticked),
                   "enemy_names": _enemy_names(conn, fun_ids)}
        roast_lines = roasts.pick(r_totals, fun_names,
                                  {r.person_id: r.played for r in rows}, total, roast_level,
                                  context)
    broken = records.broken_in(conn, ids, ticked)
    milestones = records.milestones_in(conn, ids, ticked)
    duos = records.duos_nemeses(conn, ids, person_ids)
    me = db.get_setting(conn, "me_steam_id") or None
    seen = rivals.seen_again(conn, me, ids, rivals.min_games(conn))

    show_board = any(v is not None for sb in scoreboard.values() for v in sb.values())
    lines += [_person_line(r) + (_board_text(scoreboard[r.person_id]) if show_board else "")
              for r in rows]
    if rows:
        lines.append("Played: " + " · ".join(
            f"{r.name} {played_text(r.played)}/{total}" for r in rows))
    if roast_lines:
        lines.append("Roast of the night")
        lines += roast_lines
    if cards:
        lines.append("Awards" if len(fun_ids) == total
                     else f"Awards (from {len(fun_ids)} of {total} matches)")
        lines += [f"{c['title']} — {c['name']} ({c['value_line']})" for c in cards]
    if broken or milestones:
        lines.append("Records broken tonight")
        lines += broken + milestones
    if duos:
        lines.append("Duos & nemeses")
        lines += duos
    if seen:
        lines.append("Seen again")
        lines += seen
    if best:
        lines.append("Best of the night")
        lines += best
    if lineups:
        lines.append("Lineups")
        lines += [_lineup_line(lu, rows) for lu in lineups]
    return {"matches": matches, "rows": rows, "total": total, "lineups": lineups,
            "best": best, "fun_matches": len(fun_ids), "scoreboard": scoreboard,
            "awards": cards, "roasts": roast_lines, "records_broken": broken,
            "milestones": milestones, "duos": duos, "seen_again": seen,
            "text": "\n".join(lines)}
