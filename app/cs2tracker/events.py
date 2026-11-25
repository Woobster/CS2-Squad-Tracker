"""Event bundles: everything the app may ever want from a demo, extracted once.

A bundle is gzip JSON, column-oriented (``{table: {column: [values]}}``), with a
top-level ``"format": EVENTS_FORMAT``. It is stored as ``events/<match.id>.json.gz``.
Steam IDs are strings, NaN is ``null``, numpy values become plain JSON values.

Confirmed with demoparser2 0.42.2 ``DemoParser.list_game_events()`` on the three
test demos (all three list the same events). Used here:

    round_end (winner, reason, round), round_freeze_end, player_death,
    player_hurt, weapon_fire, player_blind, flashbang_detonate,
    hegrenade_detonate, smokegrenade_detonate, smokegrenade_expired,
    inferno_startburn, inferno_expire, decoy_started, decoy_detonate,
    bomb_beginplant, bomb_planted (site), bomb_begindefuse (haskit),
    bomb_defused, bomb_exploded, bomb_pickup, bomb_dropped, weapon_reload.

Not game events but synthesised by demoparser2's ``parse_event`` (absent from
``list_game_events()``): ``round_start`` (round, tick) and ``item_purchase``
(steamid, item_name, cost, inventory_slot, ...). ``item_purchase`` replays warmup
buys at tick 1; purchases before round 1's start are dropped.

Not in these demos (``parse_event`` returns an empty list): ``round_mvp``,
``player_jump``, ``chat_message``/``player_chat``. MVPs are therefore derived from
the ``mvps`` prop rising between round ends; jumps and chat are added to
``other`` only if a future demo has them. Footsteps are omitted on purpose.

Player props (``parse_ticks``) confirmed on the demos: X, Y, Z, yaw, pitch,
health, armor_value, active_weapon_name, is_alive, team_num, name, kills_total,
deaths_total, assists_total, mvps, score, damage_total, utility_damage_total,
headshot_kills_total, team_rounds_total, balance, start_balance,
current_equip_value, round_start_equip_value, cash_spent_this_round, inventory,
has_helmet, has_defuser. ``player=[...]`` props on events come back prefixed
``user_``/``attacker_``/``assister_`` (``user`` = the victim).

Tables (event columns keep demoparser2's names; ``round`` is
``bisect_left(round_end_ticks, tick) + 1`` as in ``demo._parse``). No last-tick
cutoff is applied here: the header's ``last_tick`` is the last ``round_end`` tick,
and stats code filters on it.

    rounds     round, tick (end), winner, reason, start_tick, freeze_end_tick,
               score_ct, score_t (team_rounds_total after the round)
    ticks_end  parse_ticks(END_PROPS) at every round_end tick, all rows
    economy    parse_ticks(ECONOMY_PROPS) at each freeze end (players on team 2/3)
    purchases  item_purchase: tick, round, steamid, item_name, cost, inventory_slot
    kills      player_death (KILL_COLS) + team_kill
    damage     player_hurt (DAMAGE_COLS)
    shots      weapon_fire: tick, round, user_steamid, user_team_num, weapon
    grenades   event ("thrown" from weapon_fire, or the detonate/start/expire
               event name), grenade, entityid, x/y/z (thrower pos when thrown)
    blinds     player_blind (BLIND_COLS)
    bomb       BOMB_EVENTS: event, user_steamid, site, haskit, x/y/z
    other      weapon_reload, round_mvp (from props), jumps/chat if present
    positions  alive players every POSITION_EVERY_S from round 1's start:
               steamid, team_num, X, Y, Z, yaw, health, armor_value,
               active_weapon_name

Header ``last_tick`` is the last round_end tick (the same cutoff the stats use,
and what the match's rounds record), not the demo's final frame.
"""
import gzip
import json
import math
import os
import tempfile
import zlib
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from demoparser2 import DemoParser

from cs2tracker.demo import DemoParseError

EVENTS_FORMAT = 1
TICK_RATE = 64
POSITION_EVERY_S = 1

PLAYER_PROPS = ["team_num", "X", "Y", "Z", "yaw", "pitch"]
END_PROPS = ["kills_total", "deaths_total", "assists_total", "team_num", "name", "mvps", "score",
             "damage_total", "utility_damage_total", "headshot_kills_total", "team_rounds_total"]
ECONOMY_PROPS = ["team_num", "balance", "start_balance", "current_equip_value",
                 "round_start_equip_value", "cash_spent_this_round", "armor_value", "has_helmet",
                 "has_defuser", "inventory"]
POSITION_PROPS = ["team_num", "X", "Y", "Z", "yaw", "health", "armor_value", "active_weapon_name",
                  "is_alive"]

GRENADE_EVENTS = {
    "flashbang_detonate": "flashbang", "hegrenade_detonate": "hegrenade",
    "smokegrenade_detonate": "smokegrenade", "smokegrenade_expired": "smokegrenade",
    "inferno_startburn": "inferno", "inferno_expire": "inferno",
    "decoy_started": "decoy", "decoy_detonate": "decoy",
}
THROWN = {"weapon_flashbang": "flashbang", "weapon_hegrenade": "hegrenade",
          "weapon_smokegrenade": "smokegrenade", "weapon_molotov": "molotov",
          "weapon_incgrenade": "incgrenade", "weapon_decoy": "decoy"}
BOMB_EVENTS = ["bomb_beginplant", "bomb_planted", "bomb_begindefuse", "bomb_defused",
               "bomb_exploded", "bomb_pickup", "bomb_dropped"]
OTHER_EVENTS = ["weapon_reload"]
OPTIONAL_OTHER = ["round_mvp", "player_jump", "chat_message"]
GAME_EVENTS = (["player_death", "player_hurt", "weapon_fire", "player_blind", "round_freeze_end"]
               + list(GRENADE_EVENTS) + BOMB_EVENTS + OTHER_EVENTS)

KILL_COLS = ["tick", "round", "attacker_steamid", "user_steamid", "assister_steamid", "weapon",
             "headshot", "penetrated", "noscope", "thrusmoke", "attackerblind", "assistedflash",
             "attackerinair", "distance", "hitgroup", "dmg_health", "dmg_armor", "team_kill",
             "attacker_team_num", "user_team_num", "assister_team_num",
             "attacker_X", "attacker_Y", "attacker_Z", "attacker_yaw", "attacker_pitch",
             "user_X", "user_Y", "user_Z", "user_yaw", "user_pitch"]
DAMAGE_COLS = ["tick", "round", "attacker_steamid", "user_steamid", "weapon", "dmg_health",
               "dmg_armor", "hitgroup", "health", "armor", "attacker_team_num", "user_team_num"]
BLIND_COLS = ["tick", "round", "attacker_steamid", "user_steamid", "blind_duration", "entityid",
              "attacker_team_num", "user_team_num"]


@dataclass
class EventBundle:
    header: dict
    tables: dict[str, dict[str, list]]


# ------------------------------------------------------------------ JSON safety

def _is_sid_col(name: str) -> bool:
    return name == "steamid" or name.endswith("_steamid")


def _plain(v):
    """One value -> a JSON-safe Python value (NaN -> None, numpy -> Python)."""
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, (np.ndarray, list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return int(f) if f.is_integer() and abs(f) < 2**53 else f
    if isinstance(v, str):
        return v
    return str(v)


def _sid_value(v) -> str | None:
    if v is None or v is pd.NA:
        return None
    if isinstance(v, str):
        return v or None
    if isinstance(v, (float, np.floating)):
        # Float steam IDs have lost precision; only NaN should reach here.
        return None if math.isnan(float(v)) else str(int(v))
    return str(int(v))


def to_columns(df: pd.DataFrame, cols: list[str] | None = None) -> dict[str, list]:
    """DataFrame -> {column: [JSON-safe values]}; missing requested columns become nulls."""
    out: dict[str, list] = {}
    for c in cols if cols is not None else list(df.columns):
        if c not in df.columns:
            out[c] = [None] * len(df)
            continue
        conv = _sid_value if _is_sid_col(c) else _plain
        out[c] = [conv(v) for v in df[c].tolist()]
    return out


# ------------------------------------------------------------------ extraction

def extract_events(path: Path, valve_match_id: str | None = None) -> EventBundle:
    try:
        return _extract(Path(path), valve_match_id)
    except DemoParseError:
        raise
    except Exception as e:
        raise DemoParseError(f"{path}: {e}") from e
    except BaseException as e:
        # demoparser2 Rust panics (pyo3 PanicException) derive from BaseException
        if type(e).__name__ == "PanicException":
            raise DemoParseError(f"{path}: {e}") from e
        raise


def _optional_event(parser: DemoParser, name: str) -> pd.DataFrame | None:
    """parse_event for events a demo may lack (demoparser2 returns a list then)."""
    df = parser.parse_event(name, player=["team_num"])
    return df if isinstance(df, pd.DataFrame) and not df.empty else None


def _window_max(ticks: list[int], end_ticks: list[int]) -> list[int | None]:
    """For each round i, the last tick in (end[i-1], end[i]]."""
    out: list[int | None] = []
    s = sorted(ticks)
    prev = -1
    for e in end_ticks:
        inside = [t for t in s if prev < t <= e]
        out.append(inside[-1] if inside else None)
        prev = e
    return out


def _extract(path: Path, valve_match_id) -> EventBundle:
    if not path.is_file():
        raise DemoParseError(f"{path}: no such file")
    parser = DemoParser(str(path))
    header = parser.parse_header()
    rounds_df = parser.parse_event("round_end")
    if not isinstance(rounds_df, pd.DataFrame) or rounds_df.empty:
        raise DemoParseError(f"{path}: no rounds found")
    rounds_df = rounds_df.sort_values("tick", kind="stable").reset_index(drop=True)
    end_ticks = [int(t) for t in rounds_df["tick"]]
    last_tick = end_ticks[-1]

    def rnd(df: pd.DataFrame) -> pd.DataFrame:
        df = df.sort_values("tick", kind="stable").reset_index(drop=True)
        df["round"] = [bisect_left(end_ticks, int(t)) + 1 for t in df["tick"]]
        return df

    events = {name: df for name, df in parser.parse_events(GAME_EVENTS, player=PLAYER_PROPS)}

    def ev(name: str) -> pd.DataFrame:
        df = events.get(name)
        return rnd(df.copy()) if isinstance(df, pd.DataFrame) and not df.empty else pd.DataFrame(
            {"tick": pd.Series([], dtype="int64"), "round": pd.Series([], dtype="int64")})

    # Scoreboard and team at each round end: exactly what demo._parse reads, plus extras.
    ticks_end = parser.parse_ticks(END_PROPS, ticks=end_ticks)
    ticks_end = ticks_end.sort_values(["tick", "steamid"], kind="stable").reset_index(drop=True)

    # Rounds.
    rs = _optional_event(parser, "round_start")
    start_ticks = _window_max([int(t) for t in rs["tick"]] if rs is not None else [], end_ticks)
    fe = ev("round_freeze_end")
    freeze_ticks = _window_max([int(t) for t in fe["tick"]], end_ticks)
    score = {}
    for r in ticks_end.itertuples(index=False):
        if r.team_num in (2, 3) and not pd.isna(r.team_rounds_total):
            score[(int(r.tick), int(r.team_num))] = int(r.team_rounds_total)
    rounds = to_columns(rounds_df, ["round", "tick", "winner", "reason"])
    rounds["start_tick"] = start_ticks
    rounds["freeze_end_tick"] = freeze_ticks
    rounds["score_ct"] = [score.get((t, 3)) for t in end_ticks]
    rounds["score_t"] = [score.get((t, 2)) for t in end_ticks]

    # Economy at freeze end.
    fticks = sorted({t for t in freeze_ticks if t is not None})
    econ = parser.parse_ticks(ECONOMY_PROPS, ticks=fticks) if fticks else pd.DataFrame(
        {"tick": []})
    econ = rnd(econ) if not econ.empty else econ
    if not econ.empty:
        econ = econ[econ["team_num"].isin([2, 3])]
    economy = to_columns(econ, ["round", "tick", "steamid"] + ECONOMY_PROPS)

    # demoparser2 replays warmup buys at tick 1; keep only buys from round 1's start on.
    purch = _optional_event(parser, "item_purchase")
    first_start = next((t for t in start_ticks if t is not None), 0)
    if purch is not None:
        purch = purch[purch["tick"] >= first_start]
    purchases = to_columns(rnd(purch) if purch is not None and not purch.empty else pd.DataFrame(),
                           ["tick", "round", "steamid", "item_name", "cost", "inventory_slot"])

    deaths = ev("player_death")
    if not deaths.empty:
        att, vic = deaths["attacker_steamid"], deaths["user_steamid"]
        deaths["team_kill"] = (att.notna() & (att != vic)
                               & (deaths["attacker_team_num"] == deaths["user_team_num"]))
    kills = to_columns(deaths, KILL_COLS)
    damage = to_columns(ev("player_hurt"), DAMAGE_COLS)

    fire = ev("weapon_fire")
    shots = to_columns(fire, ["tick", "round", "user_steamid", "user_team_num", "weapon"])

    # Grenades: throws (weapon_fire with a grenade, thrower position) + detonations (position).
    parts = []
    if not fire.empty:
        thrown = fire[fire["weapon"].isin(list(THROWN))].copy()
        thrown["event"] = "thrown"
        thrown["grenade"] = thrown["weapon"].map(THROWN)
        thrown["entityid"] = None
        thrown["x"], thrown["y"], thrown["z"] = thrown["user_X"], thrown["user_Y"], thrown["user_Z"]
        parts.append(thrown)
    for name, kind in GRENADE_EVENTS.items():
        df = ev(name)
        if not df.empty:
            df["event"], df["grenade"] = name, kind
            parts.append(df)
    gcols = ["event", "tick", "round", "user_steamid", "user_team_num", "grenade", "entityid",
             "x", "y", "z"]
    gren = pd.concat(parts, ignore_index=True).sort_values("tick", kind="stable") if parts \
        else pd.DataFrame()
    grenades = to_columns(gren, gcols)

    blinds = to_columns(ev("player_blind"), BLIND_COLS)

    parts = []
    for name in BOMB_EVENTS:
        df = ev(name)
        if not df.empty:
            df["event"] = name
            parts.append(df)
    bomb_df = pd.concat(parts, ignore_index=True).sort_values("tick", kind="stable") if parts \
        else pd.DataFrame()
    bomb_df = bomb_df.rename(columns={"user_X": "x", "user_Y": "y", "user_Z": "z"})
    bomb = to_columns(bomb_df, ["event", "tick", "round", "user_steamid", "user_team_num", "site",
                                "haskit", "x", "y", "z"])

    other = _other(ev, parser, rnd, ticks_end, end_ticks)

    # Positions of alive players once per POSITION_EVERY_S, from round 1's start.
    step = TICK_RATE * POSITION_EVERY_S
    pticks = list(range(first_start - first_start % step, last_tick + 1, step))
    pos = parser.parse_ticks(POSITION_PROPS, ticks=pticks)
    pos = pos[(pos["is_alive"] == True) & (pos["health"] > 0)]  # noqa: E712
    pos = rnd(pos).sort_values(["tick", "steamid"], kind="stable")
    positions = to_columns(pos, ["tick", "round", "steamid", "team_num", "X", "Y", "Z", "yaw",
                                 "health", "armor_value", "active_weapon_name"])

    tables = {
        "rounds": rounds, "ticks_end": to_columns(ticks_end, ["tick", "steamid"] + END_PROPS),
        "economy": economy, "purchases": purchases, "kills": kills, "damage": damage,
        "shots": shots, "grenades": grenades, "blinds": blinds, "bomb": bomb, "other": other,
        "positions": positions,
    }
    return EventBundle(header=_header(header, last_tick, valve_match_id, ticks_end, end_ticks),
                       tables=tables)


def _other(ev, parser, rnd, ticks_end, end_ticks) -> dict[str, list]:
    cols = ["event", "tick", "round", "user_steamid", "detail"]
    parts = []
    for name in OTHER_EVENTS:
        df = ev(name)
        if not df.empty:
            df["event"], df["detail"] = name, None
            parts.append(df)
    for name in OPTIONAL_OTHER:
        df = _optional_event(parser, name)
        if df is not None:
            df = rnd(df)
            df["event"] = name
            df["detail"] = df["text"] if "text" in df.columns else None
            if "user_steamid" not in df.columns and "steamid" in df.columns:
                df["user_steamid"] = df["steamid"]
            parts.append(df)
    if not any(p["event"].iloc[0] == "round_mvp" for p in parts):
        parts.append(_mvps_from_props(ticks_end, end_ticks))
    df = pd.concat([p for p in parts if not p.empty], ignore_index=True) if parts else pd.DataFrame()
    if not df.empty:
        df = df.sort_values("tick", kind="stable")
    return to_columns(df, cols)


def _mvps_from_props(ticks_end: pd.DataFrame, end_ticks: list[int]) -> pd.DataFrame:
    """round_mvp rows from the ``mvps`` prop rising between consecutive round ends."""
    rows = []
    for sid, g in ticks_end.groupby("steamid"):
        prev = 0
        for r in g.sort_values("tick").itertuples(index=False):
            if pd.isna(r.mvps):
                continue
            m = int(r.mvps)
            if m > prev:
                rows.append({"event": "round_mvp", "tick": int(r.tick),
                             "round": bisect_left(end_ticks, int(r.tick)) + 1,
                             "user_steamid": sid, "detail": None})
            prev = m
    return pd.DataFrame(rows, columns=["event", "tick", "round", "user_steamid", "detail"])


def _header(h: dict, last_tick: int, valve_match_id, ticks_end: pd.DataFrame,
            end_ticks: list[int]) -> dict:
    players = []
    for sid, g in ticks_end.groupby("steamid"):
        g = g.sort_values("tick")
        last = g.iloc[-1]
        by_tick = dict(zip(g["tick"].astype(int), g["team_num"]))
        players.append({
            "steam_id": _sid_value(sid),
            "name": _plain(last["name"]),
            "team_by_round": [_plain(by_tick.get(t)) for t in end_ticks],
            "kills": _plain(last["kills_total"]), "deaths": _plain(last["deaths_total"]),
            "assists": _plain(last["assists_total"]), "mvps": _plain(last["mvps"]),
            "score": _plain(last["score"]), "damage": _plain(last["damage_total"]),
        })
    return {
        "format": EVENTS_FORMAT,
        "map": h.get("map_name", ""),
        "server": h.get("server_name", ""),
        "tick_rate": TICK_RATE,
        "position_every_s": POSITION_EVERY_S,
        "last_tick": last_tick,
        "valve_match_id": None if valve_match_id is None else str(valve_match_id),
        "players": players,
    }


# ------------------------------------------------------------------ storage

def bundle_path(events_dir: Path, match_id: int) -> Path:
    return Path(events_dir) / f"{int(match_id)}.json.gz"


def write_bundle(bundle: EventBundle, events_dir: Path, match_id: int) -> Path:
    """Atomically write ``<events_dir>/<match_id>.json.gz``."""
    events_dir = Path(events_dir)
    events_dir.mkdir(parents=True, exist_ok=True)
    dest = bundle_path(events_dir, match_id)
    data = {"format": EVENTS_FORMAT, "header": bundle.header, "tables": bundle.tables}
    raw = json.dumps(data, separators=(",", ":"), allow_nan=False).encode()
    fd, tmp = tempfile.mkstemp(prefix=f"{dest.name}.", suffix=".tmp", dir=events_dir)
    try:
        with os.fdopen(fd, "wb") as f:
            with gzip.GzipFile(fileobj=f, mode="wb", compresslevel=6, mtime=0) as gz:
                gz.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return dest


def _norm_valve(v) -> str | None:
    return None if v is None else str(v)


REQUIRED_TABLES = ("rounds", "ticks_end", "kills", "damage")  # what the stats read


def _tables_ok(tables: dict) -> bool:
    """The stats' tables are present, and every table is columns (lists) of one length."""
    if any(name not in tables for name in REQUIRED_TABLES):
        return False
    for table in tables.values():
        if not isinstance(table, dict):
            return False
        if not all(isinstance(col, list) for col in table.values()):
            return False
        if len({len(col) for col in table.values()}) > 1:
            return False
    return True


def read_bundle(events_dir: Path, match_id: int, expect: dict) -> EventBundle | None:
    """The bundle, or None if missing, unreadable, another format, not this match, missing
    a table the stats need, or with a table whose columns differ in length."""
    try:
        with gzip.open(bundle_path(events_dir, match_id), "rb") as f:
            data = json.loads(f.read())
    except (OSError, EOFError, ValueError, zlib.error):
        return None
    if not isinstance(data, dict) or data.get("format") != EVENTS_FORMAT:
        return None
    header, tables = data.get("header"), data.get("tables")
    if not isinstance(header, dict) or not isinstance(tables, dict):
        return None
    if header.get("format", EVENTS_FORMAT) != EVENTS_FORMAT:
        return None
    if not _tables_ok(tables):
        return None
    for key, want in expect.items():
        have = header.get(key)
        if key == "valve_match_id":
            if _norm_valve(have) != _norm_valve(want):
                return None
        elif key == "last_tick":
            try:
                if have is None or want is None or int(have) != int(want):
                    return None
            except (TypeError, ValueError):
                return None
        elif have != want:
            return None
    return EventBundle(header=header, tables=tables)
