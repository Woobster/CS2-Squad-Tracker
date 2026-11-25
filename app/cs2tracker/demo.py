"""Per-player stats from CS2 demos, computed from event bundles (cs2tracker.events)."""
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


class DemoParseError(Exception):
    pass


@dataclass
class PlayerLine:
    steam_id: str
    name: str
    team: str  # 'A' | 'B', stable across halftime
    kills: int
    assists: int
    deaths: int
    adr: float
    rounds_played: int
    k2: int
    k3: int
    k4: int
    k5: int
    hs_kills: int
    utility_dmg: int = 0
    self_dmg: int = 0
    knife_kills: int = 0


@dataclass
class KillRow:
    round: int
    tick: int
    attacker: str | None
    victim: str
    assister: str | None
    weapon: str
    headshot: bool
    wallbang: bool
    team_kill: bool
    attacker_xyz: tuple[float, float, float] | None
    victim_xyz: tuple[float, float, float]


@dataclass
class RoundRow:
    number: int
    winner_side: str
    reason: str
    end_tick: int


@dataclass
class MultiKill:
    steam_id: str
    round: int
    kills: int
    first_tick: int
    last_tick: int


@dataclass
class ParsedDemo:
    map: str
    server: str
    rounds: list[RoundRow]
    players: list[PlayerLine]
    kills: list[KillRow]
    multikills: list[MultiKill]
    score_a: int
    score_b: int


def _real_kills(kills: list[KillRow]) -> list[KillRow]:
    return [k for k in kills if k.attacker and k.attacker != k.victim and not k.team_kill]


def _multikill_groups(kills: list[KillRow]) -> dict[tuple[str, int], list[KillRow]]:
    groups: dict[tuple[str, int], list[KillRow]] = {}
    for k in _real_kills(kills):
        groups.setdefault((k.attacker, k.round), []).append(k)
    return groups


def count_multikills(kills: list[KillRow]) -> dict[str, dict[int, int]]:
    """steam_id -> {2: n, 3: n, 4: n, 5: n}; team kills and suicides ignored."""
    out: dict[str, dict[int, int]] = {}
    for (sid, _), ks in _multikill_groups(kills).items():
        counts = out.setdefault(sid, {2: 0, 3: 0, 4: 0, 5: 0})
        if len(ks) >= 2:
            counts[min(len(ks), 5)] += 1
    return out


def assign_teams(team_by_tick: dict[str, list[int | None]]) -> dict[str, str]:
    """Label players 'A'/'B' by who shared a team at their first sighting.

    Each list holds a player's team_num (2/3, or None if absent) at successive
    round_end ticks. Halftime swaps sides, so a player seen on team X at tick i
    is grouped with everyone else seen on team X at that same tick.
    """
    labels: dict[str, str] = {}
    n = max((len(v) for v in team_by_tick.values()), default=0)
    for i in range(n):
        at_i = {sid: v[i] for sid, v in team_by_tick.items() if i < len(v) and v[i] in (2, 3)}
        if not at_i:
            continue
        known = {sid: labels[sid] for sid in at_i if sid in labels}
        # Map this tick's team_num -> label via already-labelled players (majority).
        mapping: dict[int, str] = {}
        for t in (2, 3):
            votes = Counter(lbl for sid, lbl in known.items() if at_i[sid] == t)
            if votes:
                mapping[t] = votes.most_common(1)[0][0]
        if len(mapping) == 1:
            (t, lbl), = mapping.items()
            mapping[5 - t] = "B" if lbl == "A" else "A"
        elif not mapping:
            mapping = {2: "A", 3: "B"}
        for sid, t in at_i.items():
            labels.setdefault(sid, mapping[t])
    return labels


def favourite_weapon(kills: list[KillRow]) -> str | None:
    """Most-used weapon over real kills; ties go to the most recent kill."""
    c: Counter = Counter()
    last: dict[str, int] = {}
    for i, k in enumerate(_real_kills(kills)):
        if k.weapon:
            c[k.weapon] += 1
            last[k.weapon] = i
    return max(c, key=lambda w: (c[w], last[w])) if c else None


BOMB_WEAPONS = frozenset({"planted_c4", "c4"})
UTILITY_WEAPONS = frozenset({"hegrenade", "molotov", "inferno"})


def split_damage(hurt: pd.DataFrame, end_ticks: list[int]) -> tuple[Counter, Counter, Counter]:
    """(enemy_dmg, utility_dmg, self_dmg) per steam_id.

    dmg_health is uncapped (an AWP shot at 20 hp reads 100+), so measure the drop
    in the victim's `health` instead. All rows (incl. world damage) feed the
    health chain. Enemy damage is credited to the attacker; utility is the subset of
    it dealt with a utility weapon; self damage (attacker == victim, or no attacker,
    e.g. falls) is credited to the victim. Team damage is excluded.
    """
    enemy: Counter = Counter()
    utility: Counter = Counter()
    selfd: Counter = Counter()
    hp_left: dict[tuple[str, int], int] = {}
    ordered = hurt.sort_values(["tick", "health"], ascending=[True, False], kind="stable")
    for r in ordered.itertuples(index=False):
        a, v = _sid(r.attacker_steamid), _sid(r.user_steamid)
        if v is None:
            continue
        key = (v, bisect_left(end_ticks, int(r.tick)))
        before = hp_left.get(key, 100)
        after = max(int(r.health), 0)
        hp_left[key] = after
        drop = max(before - after, 0)
        if a is None or a == v:
            if str(getattr(r, "weapon", "")) not in BOMB_WEAPONS:
                selfd[v] += drop
        elif r.attacker_team_num != r.user_team_num:
            enemy[a] += drop
            if str(getattr(r, "weapon", "")) in UTILITY_WEAPONS:
                utility[a] += drop
    return enemy, utility, selfd


def damage_dealt(hurt: pd.DataFrame, end_ticks: list[int]) -> Counter:
    """Enemy damage per attacker, capped by the victim's remaining health."""
    return split_damage(hurt, end_ticks)[0]


def is_knife(weapon: str) -> bool:
    return weapon.startswith("knife") or weapon == "bayonet"


def knife_kills(kills: list[KillRow]) -> dict[str, int]:
    """Non-team, non-suicide kills with a knife, per attacker."""
    c: Counter = Counter(k.attacker for k in _real_kills(kills) if is_knife(k.weapon or ""))
    return dict(c)


def build_players(totals, names, team_of, team_by_tick, dmg, mk_counts, hs,
                  util=None, selfd=None, knives=None) -> list[PlayerLine]:
    util, selfd, knives = util or {}, selfd or {}, knives or {}
    players = []
    for sid, (k, a, d) in totals.items():
        if sid not in team_of:  # spectator / coach: never on team 2 or 3
            continue
        played = sum(1 for t in team_by_tick[sid] if t in (2, 3))
        m = mk_counts.get(sid, {2: 0, 3: 0, 4: 0, 5: 0})
        players.append(PlayerLine(
            sid, names[sid], team_of[sid], k, a, d, round(dmg[sid] / played, 1) if played else 0.0,
            played, m[2], m[3], m[4], m[5], hs[sid],
            int(util.get(sid, 0)), int(selfd.get(sid, 0)), int(knives.get(sid, 0))))
    return players


def _sid(v) -> str | None:
    if v is None or pd.isna(v):
        return None
    return str(int(v)) if not isinstance(v, str) else v


def parse_demo(path: Path) -> ParsedDemo:
    """Stats for one demo: the event bundle extracted from it, then stats from the bundle."""
    from cs2tracker.events import extract_events  # events imports this module
    bundle = extract_events(path)  # raises DemoParseError itself
    try:
        return stats_from_bundle(bundle)
    except DemoParseError:
        raise
    except Exception as e:
        raise DemoParseError(f"{path}: {e}") from e


def _col(tables: dict, name: str) -> dict[str, list]:
    t = tables.get(name)
    if not isinstance(t, dict):
        raise DemoParseError(f"event bundle has no {name!r} table")
    return t


def _rows(table: dict[str, list]):
    """Column-oriented table -> row dicts."""
    cols = list(table)
    return [dict(zip(cols, vals)) for vals in zip(*(table[c] for c in cols))]


def _num_eq(a, b) -> bool:
    """a == b for team numbers, where a missing value (None/NaN) never matches."""
    return a is not None and b is not None and not pd.isna(a) and not pd.isna(b) and a == b


def _xyz_of(row: dict, prefix: str):
    vals = tuple(row.get(f"{prefix}_{a}") for a in "XYZ")
    return None if any(v is None or pd.isna(v) for v in vals) else tuple(float(v) for v in vals)


def stats_from_bundle(bundle) -> ParsedDemo:
    """Per-player stats from an EventBundle (see cs2tracker.events). Only events up to the
    header's last_tick (the last round end) count."""
    header, tables = bundle.header, bundle.tables
    rounds_t = sorted(_rows(_col(tables, "rounds")), key=lambda r: int(r["tick"]))
    if not rounds_t:
        raise DemoParseError("event bundle has no rounds")
    rounds = [RoundRow(int(r["round"]), str(r["winner"]), str(r["reason"]), int(r["tick"]))
              for r in rounds_t]
    end_ticks = [r.end_tick for r in rounds]
    last_tick = end_ticks[-1]

    # Scoreboard and team at each round end.
    by_sid: dict[str, dict[int, dict]] = {}
    for r in _rows(_col(tables, "ticks_end")):
        sid = _sid(r["steamid"])
        if sid is not None:
            by_sid.setdefault(sid, {})[int(r["tick"])] = r
    team_by_tick: dict[str, list[int | None]] = {}
    names: dict[str, str] = {}
    totals: dict[str, tuple[int, int, int]] = {}
    for sid in sorted(by_sid):
        rows = by_sid[sid]
        team_by_tick[sid] = [
            int(rows[t]["team_num"]) if t in rows and rows[t]["team_num"] is not None
            and not pd.isna(rows[t]["team_num"]) else None for t in end_ticks]
        last = rows[max(rows)]
        names[sid] = str(last["name"])
        totals[sid] = (int(last["kills_total"]), int(last["assists_total"]),
                       int(last["deaths_total"]))
    team_of = assign_teams(team_by_tick)

    kills: list[KillRow] = []
    deaths = sorted(_rows(_col(tables, "kills")), key=lambda r: int(r["tick"]))
    for r in deaths:
        if int(r["tick"]) > last_tick:
            continue
        att, vic = _sid(r["attacker_steamid"]), _sid(r["user_steamid"])
        tk = bool(att and att != vic and _num_eq(r["attacker_team_num"], r["user_team_num"]))
        kills.append(KillRow(
            round=bisect_left(end_ticks, int(r["tick"])) + 1, tick=int(r["tick"]), attacker=att,
            victim=vic, assister=_sid(r["assister_steamid"]), weapon=str(r["weapon"]),
            headshot=bool(r["headshot"]), wallbang=bool(r["penetrated"]), team_kill=tk,
            attacker_xyz=_xyz_of(r, "attacker") if att else None, victim_xyz=_xyz_of(r, "user")))

    hurt = pd.DataFrame(_col(tables, "damage"))
    if hurt.empty:
        hurt = pd.DataFrame(columns=["tick", "health", "attacker_steamid", "user_steamid",
                                     "weapon", "attacker_team_num", "user_team_num"])
    hurt = hurt[hurt["tick"] <= last_tick]
    dmg, util, selfd = split_damage(hurt, end_ticks)

    mk_counts = count_multikills(kills)
    multikills = [
        MultiKill(sid, rnd, len(ks), ks[0].tick, ks[-1].tick)
        for (sid, rnd), ks in _multikill_groups(kills).items() if len(ks) >= 2]
    hs = Counter(k.attacker for k in _real_kills(kills) if k.headshot)

    players = build_players(totals, names, team_of, team_by_tick, dmg, mk_counts, hs,
                            util, selfd, knife_kills(kills))

    # Team A's win count: A wins a round when the winning side is A's team_num then.
    score = {"A": 0, "B": 0}
    for i, r in enumerate(rounds):
        win_num = 3 if r.winner_side == "CT" else 2
        votes = Counter(
            team_of[s] if team_by_tick[s][i] == win_num else ("B" if team_of[s] == "A" else "A")
            for s in team_by_tick if team_by_tick[s][i] in (2, 3))
        score[votes.most_common(1)[0][0]] += 1

    return ParsedDemo(str(header.get("map") or ""), str(header.get("server") or ""), rounds,
                      players, kills, multikills, score["A"], score["B"])
