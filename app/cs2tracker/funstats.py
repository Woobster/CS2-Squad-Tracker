"""Per-player fun stats for one match, computed from its event bundle (spec v1.3 §2).

``compute(bundle)`` returns ``{steam_id: stats}`` for every player ``demo.stats_from_bundle``
lists (bots excluded: Steam ID ``0`` or a name starting ``[BOT]``). ``store`` writes one
``fun_stats`` row per player with ``fun_format = FUN_FORMAT``. Every value is plain JSON:
dict keys are strings (``clutch_attempts["2"]`` is the 1v2 count), so a stored row reads
back equal to what ``compute`` returned.

Definitions (the builder's choices where the spec leaves them open)
-------------------------------------------------------------------
Only events up to the header's ``last_tick`` count, as in ``stats_from_bundle``.

Rounds. ``stats_from_bundle`` puts an event in round ``bisect_left(end_ticks, tick) + 1``,
so kills in the gap after a round end land in the next round. Here such an event (before
that round's ``start_tick``) belongs to the round that just ended. A player's side in a
round is their ``team_num`` in ``ticks_end`` at that round's end (3 = CT, 2 = T); a round is
"played" when that is 2 or 3. "Live" means between freeze end and the round end tick.

Kills. A *real* kill has an attacker who is not the victim and is on the other team.
- ``kills``, ``deaths``, ``hs_kills``, ``assists``, ``damage`` (enemy damage, the ADR
  numerator), ``utility_dmg``, ``multikills`` and ``rounds_played`` are taken from
  ``stats_from_bundle`` (scoreboard totals), so they equal the match's demo stats.
- Opening kill/death: the first live real kill of a round. ``first_deaths``: the first live
  death of the round, any cause. ``fastest_opening_s``: fewest seconds from freeze end to
  one of the player's opening kills.
- Trade: a real kill of V by A where V real-killed a teammate T of A at most 5 s
  (``5 * tick_rate`` ticks, inclusive) earlier in the same round. A counts one
  ``trade_kills`` per such kill; T's death counts once as ``traded_deaths``, and the first
  avenger A gets ``avenged[T] += 1``.
- Clutch: deaths are replayed in tick order (same-tick deaths together). The first time a
  player is the only one alive on their side, they are ``last_alive`` for that round; if
  N >= 1 enemies are alive then, it is a 1vN clutch attempt (N capped at 5). Won = their
  side won the round. ``last_alive_won`` counts the last-alive rounds won.
- Kill types (real kills): wallbang ``penetrated > 0``, ``thrusmoke``, ``attackerblind``
  (blind kills), ``noscope``; knife = weapon starts with ``knife`` or is ``bayonet``; Zeus =
  ``taser``; AWP = ``awp``; pistols = glock, hkp2000, usp_silencer, p250, fiveseven, tec9,
  cz75a, deagle, revolver, elite. A ``_off`` suffix (silencer off) is dropped first.
- ``eco_kills``: the killer's equipment value at the kill <= $2,000 and the enemy side's
  average then >= $3,500. Value at the kill = freeze-end ``current_equip_value`` plus the
  cost of the player's de-duplicated late buys (see Purchases) before the kill tick. ``pistol_round_kills``: rounds 1 and 13.
- Distance: metres = 3-D distance between attacker and victim positions in Source units
  x 0.0254 (the bundle's ``distance`` column when positions are missing), 1 decimal.
  ``longest`` {any, pistol, awp, zeus, knife} and ``closest`` use real kills, leaving out
  grenades, fire, world and bomb kills; each is ``{m, weapon, round}`` or null. Ties go to
  the earlier kill.
- ``kd_ct``/``kd_t``: real kills and all deaths split by the player's side that round.
- ``best_kill_streak``: most real kills in a row without dying, across rounds.
- ``fastest_ace_s``: seconds from first to last kill of a 5-kill round (``multikills``).
- ``victims``/``killers``: real kills per enemy Steam ID (bots left out).

Utility.
- Blinds by the player on someone alive (not already dead that round), self-flashes
  ignored. ``enemies_blinded`` / ``team_flashes``: enemy / teammate blinds lasting >= 1.0 s.
  ``blind_seconds``: total enemy blind duration (all lengths), 1 decimal.
- ``flash_assists``: real kills where the player is the assister and ``assistedflash``.
- ``nades_thrown`` {flash, he, smoke, molotov (molotov + incendiary), decoy}: throws.
- Grenades held at a live death: freeze-end inventory, plus grenades bought after freeze end
  and before the death (see Purchases), minus throws before the death, per type, never
  below 0. Grenades picked up from the floor are not counted. Value at list price: flash $200, smoke $300, HE $300, molotov $400, incendiary $500, decoy $50.

Survival. ``rounds_survived``: played rounds without a death. ``kast_rounds``: played rounds
with a real kill, an assist on an enemy's death, survival, or a traded death.
``avg_time_alive_s``: mean over played rounds of seconds from freeze end to death (or the
round end), 1 decimal.

Purchases. demoparser2's ``item_purchase`` re-emits a player's earlier buys of the round in
batches later on (at throws, deaths): ~20% of rows. So a row after freeze end counts only
when that item was not bought earlier in the round (a genuine second identical late buy is
missed), and rows up to freeze end are used as sets (which items were bought).

Economy. ``money_spent``: per round, the freeze-end ``cash_spent_this_round`` (else the
purchases up to freeze end), plus the late buys above. ``taser_buys``: rounds with a
"Zeus x27" purchase.
``weapons_dropped_to_enemies``: an enemy's first real kill in a round with a gun he did not
own that round (not in his freeze-end inventory nor bought before the kill) is credited to
the latest-dying player of the other side who owned that gun that round and died before the
kill. ``buy_streak``: longest run of consecutive rounds in which the player bought the same
weapon, not counting the default pistols (Glock-18, USP-S, P2000) and the Zeus; ties go to
the earliest run. ``{"weapon": null, "n": 0}`` when there is none. ``buy_streak`` and
``taser_buys`` count every purchase, including guns bought (dropped) for teammates.

Shooting. ``shots``: weapon_fire with a gun (no knives, grenades, C4 or Zeus). ``hits``:
distinct (victim, tick) enemy damage events with those guns, so a shotgun blast is one hit.
``reload_deaths``: deaths at most 2 s after one of the player's own ``weapon_reload``.

Bomb. ``plants``/``defuses``: bomb_planted/bomb_defused. ``ninja_defuses``: a defuse while an
alive enemy's last sampled position (positions table, 1/s) is within 1,000 units.

Self-harm. Damage is the drop in the victim's health (as ``demo.split_damage``), capped by
the event's ``dmg_health``.
``fall_dmg``: damage with no attacker (or self) and weapon "" or "world". ``team_dmg``:
damage the player dealt to teammates. ``team_kills``: kills of teammates.

Team level (the same on every player of a team). ``pistol_rounds_won_team``: rounds 1 and 13
won. ``ecos_lost_after_pistol_win_team``: those pistol wins after which the team lost the
next round (the anti-eco); a next round with an unknown winner is not a loss.

Rating: HLTV 1.0, rounded to 3 decimals, with rounds = rounds played::

    KillRating     = kills / rounds / 0.679
    SurvivalRating = (rounds - deaths) / rounds / 0.317
    RMK            = (1K + 4*2K + 9*3K + 16*4K + 25*5K) / rounds / 1.277
    rating         = (KillRating + 0.7 * SurvivalRating + RMK) / 2.7

where nK is the number of rounds with exactly n real kills (5K: five or more).
"""
import json
import math
from bisect import bisect_left
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

from cs2tracker.demo import _sid, is_knife, split_damage, stats_from_bundle
from cs2tracker.events import EventBundle

FUN_FORMAT = 1

TRADE_S = 5
RELOAD_S = 2
BLIND_MIN_S = 1.0
ECO_MAX = 2000
ECO_ENEMY_MIN = 3500
PISTOL_ROUNDS = (1, 13)
NINJA_UNITS = 1000
UNIT_M = 0.0254

PISTOLS = frozenset({"glock", "hkp2000", "usp_silencer", "p250", "fiveseven", "tec9", "cz75a",
                     "deagle", "revolver", "elite"})
NOT_AIMED = frozenset({"hegrenade", "inferno", "molotov", "incgrenade", "flashbang",
                       "smokegrenade", "decoy", "world", "planted_c4", "c4", ""})
FALL_WEAPONS = frozenset({"", "world"})

# display name (inventory, purchases) -> kill/damage weapon name
GUNS = {
    "AK-47": "ak47", "AUG": "aug", "AWP": "awp", "CZ75-Auto": "cz75a", "Desert Eagle": "deagle",
    "Dual Berettas": "elite", "FAMAS": "famas", "Five-SeveN": "fiveseven", "G3SG1": "g3sg1",
    "Galil AR": "galilar", "Glock-18": "glock", "M249": "m249", "M4A1-S": "m4a1_silencer",
    "M4A4": "m4a1", "MAC-10": "mac10", "MAG-7": "mag7", "MP5-SD": "mp5sd", "MP7": "mp7",
    "MP9": "mp9", "Negev": "negev", "Nova": "nova", "P2000": "hkp2000", "P250": "p250",
    "P90": "p90", "PP-Bizon": "bizon", "R8 Revolver": "revolver", "SCAR-20": "scar20",
    "SG 553": "sg556", "SSG 08": "ssg08", "Sawed-Off": "sawedoff", "Tec-9": "tec9",
    "UMP-45": "ump45", "USP-S": "usp_silencer", "XM1014": "xm1014", "Zeus x27": "taser",
}
GUN_NAMES = frozenset(GUNS.values())
SHOT_GUNS = GUN_NAMES - {"taser"}
DEFAULT_PISTOLS = frozenset({"Glock-18", "USP-S", "P2000"})
ZEUS = "Zeus x27"

NADE_ITEMS = {"Flashbang": "flash", "Smoke Grenade": "smoke", "High Explosive Grenade": "he",
              "Molotov": "molotov", "Incendiary Grenade": "incendiary", "Decoy Grenade": "decoy"}
NADE_THROWN = {"flashbang": "flash", "smokegrenade": "smoke", "hegrenade": "he",
               "molotov": "molotov", "incgrenade": "incendiary", "decoy": "decoy"}
NADE_PRICE = {"flash": 200, "smoke": 300, "he": 300, "molotov": 400, "incendiary": 500,
              "decoy": 50}
THROWN_KEYS = ("flash", "he", "smoke", "molotov", "decoy")
CLUTCH_KEYS = ("1", "2", "3", "4", "5")
LONGEST_KEYS = ("any", "pistol", "awp", "zeus", "knife")

TABLE_COLS = {
    "kills": ["tick", "attacker_steamid", "user_steamid", "assister_steamid", "weapon",
              "headshot", "penetrated", "noscope", "thrusmoke", "attackerblind",
              "assistedflash", "attacker_team_num", "user_team_num", "attacker_X",
              "attacker_Y", "attacker_Z", "user_X", "user_Y", "user_Z", "distance"],
    "damage": ["tick", "attacker_steamid", "user_steamid", "weapon", "dmg_health", "health",
               "attacker_team_num", "user_team_num"],
    "economy": ["tick", "steamid", "team_num", "current_equip_value", "cash_spent_this_round",
                "inventory"],
    "purchases": ["tick", "steamid", "item_name", "cost"],
    "shots": ["tick", "user_steamid", "weapon"],
    "grenades": ["event", "tick", "user_steamid", "grenade"],
    "blinds": ["tick", "attacker_steamid", "user_steamid", "blind_duration"],
    "bomb": ["event", "tick", "user_steamid", "user_team_num", "x", "y", "z"],
    "other": ["event", "tick", "user_steamid"],
    "positions": ["tick", "steamid", "X", "Y", "Z"],
}


def is_bot(steam_id: str, name: str | None) -> bool:
    return str(steam_id) == "0" or (name or "").startswith("[BOT]")


def _base(weapon) -> str:
    w = "" if weapon is None or (isinstance(weapon, float) and math.isnan(weapon)) else str(weapon)
    w = w.removeprefix("weapon_")
    return w.removesuffix("_off")


def _num(v, default=None):
    if v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(f) else f


def _frame(tables: dict, name: str, last_tick: int) -> pd.DataFrame:
    """The table as a DataFrame with at least TABLE_COLS[name], rows up to last_tick, sorted."""
    t = tables.get(name)
    df = pd.DataFrame(t) if isinstance(t, dict) and t else pd.DataFrame()
    for c in TABLE_COLS[name]:
        if c not in df.columns:
            df[c] = None
    if df.empty:
        return df
    df = df[pd.to_numeric(df["tick"], errors="coerce").notna()].copy()
    df["tick"] = df["tick"].astype("int64")
    df = df[df["tick"] <= last_tick]
    for c in df.columns:
        if c == "steamid" or c.endswith("_steamid"):
            df[c] = pd.Series([_sid(v) for v in df[c]], index=df.index, dtype=object)
    return df.sort_values("tick", kind="stable").reset_index(drop=True)


def _where(df: pd.DataFrame, mask) -> pd.DataFrame:
    """Rows where mask (a list or Series of truthy values) holds; safe on empty frames."""
    return df[np.asarray(list(mask), dtype=bool)] if len(df) else df


class _Match:
    """Rounds, sides and per-event round numbers for one bundle."""

    def __init__(self, bundle: EventBundle, demo):
        h, t = bundle.header, bundle.tables
        self.tr = int(_num(h.get("tick_rate"), 64) or 64)
        rounds = sorted(zip(*(t["rounds"].get(c, [None] * len(t["rounds"]["tick"]))
                              for c in ("tick", "start_tick", "freeze_end_tick"))),
                        key=lambda r: int(r[0]))
        self.end = [int(r[0]) for r in rounds]
        self.n = len(self.end)
        self.last_tick = self.end[-1]
        self.start, self.fe = [], []
        for i, (_, st, fe) in enumerate(rounds):
            prev = self.end[i - 1] if i else 0
            st = int(st) if _num(st) is not None else prev
            self.start.append(st)
            self.fe.append(int(fe) if _num(fe) is not None else st)
        self.winner = [3 if str(r.winner_side) == "CT" else 2 if str(r.winner_side) == "T"
                       else None for r in demo.rounds]
        # side per player per round, from ticks_end at each round end
        te = t["ticks_end"]
        idx = {tk: i for i, tk in enumerate(self.end)}
        self.side: dict[str, list[int | None]] = {}
        for tick, sid, team in zip(te["tick"], te["steamid"], te["team_num"]):
            s, i = _sid(sid), idx.get(int(tick))
            team = _num(team)
            if s is None or i is None:
                continue
            row = self.side.setdefault(s, [None] * self.n)
            row[i] = int(team) if team in (2, 3) else None

    def rnd(self, tick: int) -> int:
        """1-based round; the gap after a round end belongs to the round that just ended."""
        r = min(bisect_left(self.end, tick), self.n - 1)
        if r > 0 and tick < self.start[r]:
            r -= 1
        return r + 1

    def side_of(self, sid, r: int) -> int | None:
        row = self.side.get(sid)
        return row[r - 1] if row is not None else None

    def live(self, tick: int, r: int) -> bool:
        return self.fe[r - 1] <= tick <= self.end[r - 1]


def _dist_m(row) -> float | None:
    a = [_num(row[f"attacker_{c}"]) for c in "XYZ"]
    v = [_num(row[f"user_{c}"]) for c in "XYZ"]
    if None not in a and None not in v:
        return math.dist(a, v) * UNIT_M
    return _num(row["distance"])


def compute(bundle: EventBundle) -> dict[str, dict]:
    demo = stats_from_bundle(bundle)
    mt = _Match(bundle, demo)
    tables = bundle.tables
    names = {p.steam_id: p.name for p in demo.players}
    out_ids = [p.steam_id for p in demo.players if not is_bot(p.steam_id, p.name)]
    bots = {p.steam_id for p in demo.players if is_bot(p.steam_id, p.name)} | {"0"}
    tr, n = mt.tr, mt.n
    played = {sid: [r for r in range(1, n + 1) if mt.side_of(sid, r) in (2, 3)]
              for sid in names}

    S: dict[str, dict] = {sid: _blank() for sid in names}

    def add(sid, key, v=1):
        if sid in S:
            S[sid][key] += v

    # ---------------------------------------------------------------- kills
    K = _frame(tables, "kills", mt.last_tick)
    K["r"] = [mt.rnd(t) for t in K["tick"]]
    K["w"] = [_base(w) for w in K["weapon"]]
    K["att"], K["vic"], K["ast"] = K["attacker_steamid"], K["user_steamid"], K["assister_steamid"]
    at_l = [_num(x) for x in K["attacker_team_num"]]   # lists: a column would hold NaN
    vt_l = [_num(x) for x in K["user_team_num"]]
    K["at"], K["vt"] = at_l, vt_l
    K["real"] = [bool(a) and a != v and at is not None and vt is not None and at != vt
                 for a, v, at, vt in zip(K["att"], K["vic"], at_l, vt_l)]
    K["tk"] = [bool(a) and a != v and at is not None and at == vt
               for a, v, at, vt in zip(K["att"], K["vic"], at_l, vt_l)]
    K["live"] = [mt.live(t, r) for t, r in zip(K["tick"], K["r"])]
    real = _where(K, K["real"])

    flag = lambda c: [bool(_num(x, 0)) for x in real[c]]  # noqa: E731
    for key, mask in (("wallbangs", flag("penetrated")), ("smoke_kills", flag("thrusmoke")),
                      ("blind_kills", flag("attackerblind")), ("noscope_kills", flag("noscope")),
                      ("knife_kills", [is_knife(w) for w in real["w"]]),
                      ("zeus_kills", [w == "taser" for w in real["w"]]),
                      ("pistol_kills", [w in PISTOLS for w in real["w"]]),
                      ("awp_kills", [w == "awp" for w in real["w"]]),
                      ("pistol_round_kills", [r in PISTOL_ROUNDS for r in real["r"]])):
        for sid, c in Counter(_where(real, mask)["att"]).items():
            add(sid, key, int(c))
    for sid, c in Counter(_where(K, K["tk"])["att"]).items():
        add(sid, "team_kills", int(c))
    fa = _where(real, [bool(_num(x, 0)) for x in real["assistedflash"]])
    for sid, c in Counter(x for x in fa["ast"] if x).items():
        add(sid, "flash_assists", int(c))

    # victims / killers / sides
    for a, v, r in zip(real["att"], real["vic"], real["r"]):
        if v not in bots and a in S:
            S[a]["victims"][v] = S[a]["victims"].get(v, 0) + 1
        if a not in bots and v in S:
            S[v]["killers"][a] = S[v]["killers"].get(a, 0) + 1
        sd = mt.side_of(a, r)
        if a in S and sd in (2, 3):
            S[a]["kd_ct" if sd == 3 else "kd_t"]["k"] += 1
    for v, r in zip(K["vic"], K["r"]):
        sd = mt.side_of(v, r)
        if v in S and sd in (2, 3):
            S[v]["kd_ct" if sd == 3 else "kd_t"]["d"] += 1

    # distances
    for row in real.to_dict("records"):
        w, a = row["w"], row["att"]
        if a not in S or w in NOT_AIMED:
            continue
        d = _dist_m(row)
        if d is None:
            continue
        entry = {"m": round(d, 1), "weapon": w, "round": int(row["r"])}
        cats = ["any"] + (["pistol"] if w in PISTOLS else []) + (["awp"] if w == "awp" else []) \
            + (["zeus"] if w == "taser" else []) + (["knife"] if is_knife(w) else [])
        for c in cats:
            cur = S[a]["longest"][c]
            if cur is None or d > cur["_d"]:
                S[a]["longest"][c] = {**entry, "_d": d}
        cur = S[a]["closest"]
        if cur is None or d < cur["_d"]:
            S[a]["closest"] = {**entry, "_d": d}

    E = _frame(tables, "economy", mt.last_tick)

    # opening kills, first deaths, trades
    live = _where(K, K["live"])
    for r, g in live.groupby("r", sort=True):
        first = g.iloc[0]
        add(first["vic"], "first_deaths")
        gr = _where(g, g["real"])
        if not gr.empty:
            o = gr.iloc[0]
            add(o["att"], "opening_kills")
            add(o["vic"], "opening_deaths")
            if o["att"] in S:
                s = round((int(o["tick"]) - mt.fe[r - 1]) / tr, 2)
                cur = S[o["att"]]["fastest_opening_s"]
                S[o["att"]]["fastest_opening_s"] = s if cur is None else min(cur, s)

    traded_rounds: set[tuple[str, int]] = set()
    rl = real.reset_index(drop=True)
    rl["i"] = rl.index
    pairs = rl.merge(rl, left_on="att", right_on="vic", suffixes=("", "_r"))
    pairs = pairs[(pairs["r"] == pairs["r_r"]) & (pairs["tick_r"] >= pairs["tick"])
                  & (pairs["tick_r"] - pairs["tick"] <= TRADE_S * tr)
                  & (pairs["at_r"] == pairs["vt"]) & (pairs["att_r"] != pairs["vic"])
                  & (pairs["i_r"] != pairs["i"])]
    for sid, c in Counter(pairs.drop_duplicates("i_r")["att_r"]).items():
        add(sid, "trade_kills", int(c))
    firsts = pairs.sort_values(["tick_r", "i_r"], kind="stable").drop_duplicates("i")
    for vic, avenger, r in zip(firsts["vic"], firsts["att_r"], firsts["r"]):
        add(vic, "traded_deaths")
        traded_rounds.add((vic, int(r)))
        if avenger in S:
            S[avenger]["avenged"][vic] = S[avenger]["avenged"].get(vic, 0) + 1

    # clutches and last alive
    for r in range(1, n + 1):
        alive = {t: {s for s in mt.side if mt.side_of(s, r) == t} for t in (2, 3)}
        done: set[int] = set()
        g = live[live["r"] == r]
        for _, gt in g.groupby("tick", sort=True):
            for v in gt["vic"]:
                for t in (2, 3):
                    alive[t].discard(v)
            for t in (2, 3):
                if t in done or len(alive[t]) != 1:
                    continue
                done.add(t)
                (p,) = alive[t]
                won = mt.winner[r - 1] == t
                add(p, "last_alive")
                add(p, "last_alive_won", int(won))
                enemies = len(alive[5 - t])
                if enemies >= 1 and p in S:
                    k = str(min(enemies, 5))
                    S[p]["clutch_attempts"][k] += 1
                    S[p]["clutch_wins"][k] += int(won)

    # survival, KAST, time alive
    death_tick: dict[tuple[str, int], int] = {}
    for v, r, tick in zip(K["vic"], K["r"], K["tick"]):
        death_tick.setdefault((v, r), int(tick))
    kill_rounds = set(zip(real["att"], real["r"]))
    assist_rounds = set((a, r) for a, r in zip(real["ast"], real["r"]) if a)
    for sid in S:
        alive_s = []
        for r in played[sid]:
            dt = death_tick.get((sid, r))
            if dt is None:
                S[sid]["rounds_survived"] += 1
            if dt is None or (sid, r) in kill_rounds or (sid, r) in assist_rounds \
                    or (sid, r) in traded_rounds:
                S[sid]["kast_rounds"] += 1
            stop = mt.end[r - 1] if dt is None else min(dt, mt.end[r - 1])
            alive_s.append(max(stop - mt.fe[r - 1], 0) / tr)
            sd = mt.side_of(sid, r)
            S[sid]["side_rounds"]["ct" if sd == 3 else "t"] += 1
        S[sid]["avg_time_alive_s"] = round(sum(alive_s) / len(alive_s), 1) if alive_s else 0.0

    # kill streaks: real kills by the player, reset by any death
    ev = sorted([(int(t), 0, a) for t, a in zip(real["tick"], real["att"])]
                + [(int(t), 1, v) for t, v in zip(K["tick"], K["vic"])])
    run: Counter = Counter()
    for _, kind, sid in ev:
        if sid not in S:
            continue
        run[sid] = 0 if kind else run[sid] + 1
        S[sid]["best_kill_streak"] = max(S[sid]["best_kill_streak"], run[sid])

    # aces
    for mk in demo.multikills:
        if mk.kills >= 5 and mk.steam_id in S:
            s = round((mk.last_tick - mk.first_tick) / tr, 2)
            cur = S[mk.steam_id]["fastest_ace_s"]
            S[mk.steam_id]["fastest_ace_s"] = s if cur is None else min(cur, s)

    # ---------------------------------------------------------------- utility
    B = _frame(tables, "blinds", mt.last_tick)
    for a, v, tick, dur in zip(B["attacker_steamid"], B["user_steamid"], B["tick"],
                               B["blind_duration"]):
        dur = _num(dur, 0.0)
        if a is None or v is None or a == v or a not in S:
            continue
        r = mt.rnd(tick)
        dt = death_tick.get((v, r))
        if dt is not None and dt < tick:
            continue
        sa, sv = mt.side_of(a, r), mt.side_of(v, r)
        if sa not in (2, 3) or sv not in (2, 3):
            continue
        if sa != sv:
            S[a]["blind_seconds"] += dur
            if dur >= BLIND_MIN_S:
                S[a]["enemies_blinded"] += 1
        elif dur >= BLIND_MIN_S:
            S[a]["team_flashes"] += 1

    G = _frame(tables, "grenades", mt.last_tick)
    G = _where(G, G["event"] == "thrown")
    thrown = defaultdict(list)   # (sid, round) -> [(tick, type)]
    for sid, g, tick in zip(G["user_steamid"], G["grenade"], G["tick"]):
        typ = NADE_THROWN.get(str(g))
        if typ is None:
            continue
        thrown[(sid, mt.rnd(tick))].append((int(tick), typ))
        if sid in S:
            S[sid]["nades_thrown"]["molotov" if typ == "incendiary" else typ] += 1

    # Purchases: demoparser2 re-emits a player's earlier buys of the round later on (in
    # batches, e.g. at a throw or at death), so a buy after freeze end counts only for an
    # item not bought before in that round, and money before freeze end comes from the
    # economy's cash_spent_this_round where there is one.
    P = _frame(tables, "purchases", mt.last_tick)
    bought = defaultdict(list)   # (sid, round) -> [(tick, item)]
    pre_cost: Counter = Counter()
    late_cost = defaultdict(list)   # (sid, round) -> [(tick, cost)] of de-duplicated late buys
    for sid, item, tick, cost in zip(P["steamid"], P["item_name"], P["tick"], P["cost"]):
        item, r = str(item), mt.rnd(tick)
        key = (sid, r)
        if tick > mt.fe[r - 1]:
            if any(i == item for _, i in bought[key]):
                continue
            add(sid, "money_spent", int(_num(cost, 0)))
            late_cost[key].append((int(tick), int(_num(cost, 0))))
        else:
            pre_cost[key] += int(_num(cost, 0))
        bought[key].append((int(tick), item))
    cash = {}
    for sid, tick, c in zip(E["steamid"], E["tick"], E["cash_spent_this_round"]):
        if _num(c) is not None:
            cash[(sid, mt.rnd(tick))] = int(_num(c))
    for key in set(cash) | set(pre_cost):
        add(key[0], "money_spent", cash.get(key, pre_cost[key]))
    for (sid, _), items in bought.items():
        if any(i == ZEUS for _, i in items):
            add(sid, "taser_buys")

    # eco kills: equipment value at the kill = freeze-end value + late buys before the kill
    equip, team_of_round = {}, defaultdict(list)
    for tick, sid, team, val in zip(E["tick"], E["steamid"], E["team_num"],
                                    E["current_equip_value"]):
        val, team = _num(val), _num(team)
        if val is None or sid is None:
            continue
        r = mt.rnd(tick)
        equip[(r, sid)] = val
        if team in (2, 3):
            team_of_round[(r, int(team))].append(sid)

    def value_at(sid, r, tick):
        return equip[(r, sid)] + sum(c for t, c in late_cost.get((sid, r), []) if t < tick)
    for a, r, at, tick in zip(real["att"], real["r"], real["at"], real["tick"]):
        enemies = team_of_round.get((r, 5 - int(at)))
        if (r, a) not in equip or not enemies:
            continue
        avg = sum(value_at(e, r, tick) for e in enemies) / len(enemies)
        if value_at(a, r, tick) <= ECO_MAX and avg >= ECO_ENEMY_MIN:
            add(a, "eco_kills")

    inventory = {}
    for sid, tick, inv in zip(E["steamid"], E["tick"], E["inventory"]):
        inventory[(sid, mt.rnd(tick))] = list(inv) if isinstance(inv, list) else []

    for v, r, tick in zip(K["vic"], K["r"], K["tick"]):
        if v not in S or not mt.live(tick, r):
            continue
        held = Counter(NADE_ITEMS[x] for x in inventory.get((v, r), []) if x in NADE_ITEMS)
        for bt, item in bought.get((v, r), []):
            if item in NADE_ITEMS and mt.fe[r - 1] < bt <= tick:
                held[NADE_ITEMS[item]] += 1
        for tt, typ in thrown.get((v, r), []):
            if tt < tick:
                held[typ] -= 1
        for typ, c in held.items():
            if c > 0:
                S[v]["nades_held_at_death"] += c
                S[v]["nades_held_value"] += c * NADE_PRICE[typ]

    # weapons dropped to enemies
    def owned(sid, r, before):
        have = {GUNS[x] for x in inventory.get((sid, r), []) if x in GUNS}
        have |= {GUNS[i] for t, i in bought.get((sid, r), []) if i in GUNS and t <= before}
        return have
    seen: set[tuple[str, str, int]] = set()
    for a, w, r, tick, at in zip(real["att"], real["w"], real["r"], real["tick"], real["at"]):
        if w not in GUN_NAMES or (a, w, r) in seen:
            continue
        if w in owned(a, r, tick):
            continue
        seen.add((a, w, r))
        donors = [(dt, v) for (v, rr), dt in death_tick.items()
                  if rr == r and dt < tick and mt.side_of(v, r) not in (None, at)
                  and w in owned(v, r, dt)]
        if donors:
            add(max(donors)[1], "weapons_dropped_to_enemies")

    # buy streaks: a strictly longer run replaces the best, so ties go to the earliest run
    for sid in S:
        runs: dict[str, int] = {}
        best_w, best_n = None, 0
        for r in range(1, n + 1):
            items = sorted({i for _, i in bought.get((sid, r), [])
                            if i in GUNS and i not in DEFAULT_PISTOLS and i != ZEUS})
            runs = {i: runs.get(i, 0) + 1 for i in items}
            for i in items:
                if runs[i] > best_n:
                    best_w, best_n = i, runs[i]
        S[sid]["buy_streak"] = {"weapon": best_w, "n": best_n}

    # ---------------------------------------------------------------- shooting
    SH = _frame(tables, "shots", mt.last_tick)
    for sid, c in Counter(s for s, w in zip(SH["user_steamid"], SH["weapon"])
                          if _base(w) in SHOT_GUNS).items():
        add(sid, "shots", int(c))
    H = _frame(tables, "damage", mt.last_tick)
    hits = set()
    for a, v, tick, w, at, vt in zip(H["attacker_steamid"], H["user_steamid"], H["tick"],
                                     H["weapon"], H["attacker_team_num"], H["user_team_num"]):
        at, vt = _num(at), _num(vt)
        if a and v and a != v and at is not None and vt is not None and at != vt \
                and _base(w) in SHOT_GUNS:
            hits.add((a, v, int(tick)))
    for sid, c in Counter(a for a, _, _ in hits).items():
        add(sid, "hits", int(c))

    O = _frame(tables, "other", mt.last_tick)
    reloads = defaultdict(list)
    for sid, tick in zip(O["user_steamid"][O["event"] == "weapon_reload"],
                         O["tick"][O["event"] == "weapon_reload"]):
        reloads[sid].append(int(tick))
    for v, tick in zip(K["vic"], K["tick"]):
        ts = reloads.get(v)
        if ts:
            i = bisect_left(ts, tick - RELOAD_S * tr)
            if i < len(ts) and ts[i] <= tick:
                add(v, "reload_deaths")

    # self-harm: health-drop chain, as demo.split_damage
    if not H.empty:
        hh = H.sort_values(["tick", "health"], ascending=[True, False], kind="stable").copy()
        hh["hp"] = [max(int(_num(x, 0)), 0) for x in hh["health"]]
        hh["key"] = [bisect_left(mt.end, t) for t in hh["tick"]]
        prev = hh.groupby(["user_steamid", "key"], sort=False)["hp"].shift(1).fillna(100)
        dmg_h = pd.Series([_num(x, 0) for x in hh["dmg_health"]], index=hh.index)
        hh["drop"] = (prev - hh["hp"]).clip(lower=0).combine(dmg_h, min).astype(int)
        for a, v, w, at, vt, d in zip(hh["attacker_steamid"], hh["user_steamid"], hh["weapon"],
                                      hh["attacker_team_num"], hh["user_team_num"], hh["drop"]):
            if (a is None or a == v) and _base(w) in FALL_WEAPONS:
                add(v, "fall_dmg", int(d))
            elif a and a != v and _num(at) is not None and _num(at) == _num(vt):
                add(a, "team_dmg", int(d))

    # ---------------------------------------------------------------- bomb
    BM = _frame(tables, "bomb", mt.last_tick)
    POS = _frame(tables, "positions", mt.last_tick)
    pos_by = {sid: g for sid, g in POS.groupby("steamid")} if not POS.empty else {}
    for ev_name, sid, tick, team, x, y, z in zip(BM["event"], BM["user_steamid"], BM["tick"],
                                                 BM["user_team_num"], BM["x"], BM["y"], BM["z"]):
        if ev_name == "bomb_planted":
            add(sid, "plants")
        elif ev_name == "bomb_defused":
            add(sid, "defuses")
            r = mt.rnd(tick)
            me = (_num(x), _num(y), _num(z))
            side = mt.side_of(sid, r) or _num(team)
            if None in me or side not in (2, 3):
                continue
            for e in mt.side:
                if mt.side_of(e, r) != 5 - side:
                    continue
                dt = death_tick.get((e, r))
                if dt is not None and dt <= tick:
                    continue
                g = pos_by.get(e)
                if g is None:
                    continue
                g = g[(g["tick"] <= tick) & (g["tick"] >= mt.start[r - 1])]
                if g.empty:
                    continue
                last = g.iloc[-1]
                p = (_num(last["X"]), _num(last["Y"]), _num(last["Z"]))
                if None not in p and math.dist(me, p) <= NINJA_UNITS:
                    add(sid, "ninja_defuses")
                    break

    # ---------------------------------------------------------------- team level
    team_of = {p.steam_id: p.team for p in demo.players}
    team_side: dict[tuple[str, int], int] = {}
    for r in range(1, n + 1):
        for lbl in ("A", "B"):
            c = Counter(mt.side_of(s, r) for s in team_of if team_of[s] == lbl
                        and mt.side_of(s, r) in (2, 3))
            if c:
                team_side[(lbl, r)] = c.most_common(1)[0][0]
    team_level = {}
    for lbl in ("A", "B"):
        won = lost_after = 0
        for p in PISTOL_ROUNDS:
            if p <= n and mt.winner[p - 1] is not None \
                    and team_side.get((lbl, p)) == mt.winner[p - 1]:
                won += 1
                nxt = mt.winner[p] if p < n else None   # unknown winner: not a loss
                if nxt is not None and team_side.get((lbl, p + 1)) != nxt:
                    lost_after += 1
        team_level[lbl] = (won, lost_after)

    # ---------------------------------------------------------------- scoreboard numbers
    dmg = split_damage(H if not H.empty else pd.DataFrame(
        columns=["tick", "health", "attacker_steamid", "user_steamid", "weapon",
                 "attacker_team_num", "user_team_num"]), mt.end)[0]
    one_kill = Counter(a for (a, _), c in Counter(
        (k.attacker, k.round) for k in demo.kills
        if k.attacker and k.attacker != k.victim and not k.team_kill).items() if c == 1)
    out: dict[str, dict] = {}
    for p in demo.players:
        if p.steam_id not in out_ids:
            continue
        s = S[p.steam_id]
        s.update(kills=p.kills, deaths=p.deaths, assists=p.assists, hs_kills=p.hs_kills,
                 utility_dmg=p.utility_dmg, rounds_played=p.rounds_played,
                 damage=int(dmg.get(p.steam_id, 0)), team=p.team,
                 multikills={"2": p.k2, "3": p.k3, "4": p.k4, "5": p.k5})
        s["rating"] = hltv_rating(p.kills, p.deaths, p.rounds_played,
                                  [one_kill.get(p.steam_id, 0), p.k2, p.k3, p.k4, p.k5])
        s["pistol_rounds_won_team"], s["ecos_lost_after_pistol_win_team"] = \
            team_level.get(p.team, (0, 0))
        s["blind_seconds"] = round(s["blind_seconds"], 1)
        for c in LONGEST_KEYS:
            if s["longest"][c] is not None:
                s["longest"][c].pop("_d")
        if s["closest"] is not None:
            s["closest"].pop("_d")
        s["victims"] = dict(sorted(s["victims"].items()))
        s["killers"] = dict(sorted(s["killers"].items()))
        s["avenged"] = dict(sorted(s["avenged"].items()))
        out[p.steam_id] = s
    return out


def hltv_rating(kills: int, deaths: int, rounds: int, mk: list[int]) -> float:
    """HLTV 1.0. mk = [1K, 2K, 3K, 4K, 5K] round counts."""
    if rounds <= 0:
        return 0.0
    kr = kills / rounds / 0.679
    sr = (rounds - deaths) / rounds / 0.317
    rmk = (mk[0] + 4 * mk[1] + 9 * mk[2] + 16 * mk[3] + 25 * mk[4]) / rounds / 1.277
    return round((kr + 0.7 * sr + rmk) / 2.7, 3)


def _blank() -> dict:
    s = {k: 0 for k in (
        "kills", "deaths", "assists", "hs_kills", "opening_kills", "opening_deaths",
        "trade_kills", "traded_deaths", "wallbangs", "smoke_kills", "blind_kills",
        "noscope_kills", "knife_kills", "zeus_kills", "pistol_kills", "awp_kills", "eco_kills",
        "pistol_round_kills", "enemies_blinded", "team_flashes", "flash_assists",
        "utility_dmg", "nades_held_at_death", "nades_held_value", "rounds_played",
        "rounds_survived", "last_alive", "last_alive_won", "first_deaths", "kast_rounds",
        "money_spent", "weapons_dropped_to_enemies", "taser_buys", "shots", "hits",
        "reload_deaths", "plants", "defuses", "ninja_defuses", "fall_dmg", "team_dmg",
        "team_kills", "damage", "pistol_rounds_won_team", "ecos_lost_after_pistol_win_team",
        "best_kill_streak")}
    s.update(blind_seconds=0.0, avg_time_alive_s=0.0, rating=0.0,
             clutch_attempts={k: 0 for k in CLUTCH_KEYS}, clutch_wins={k: 0 for k in CLUTCH_KEYS},
             longest={k: None for k in LONGEST_KEYS}, closest=None,
             nades_thrown={k: 0 for k in THROWN_KEYS}, kd_ct={"k": 0, "d": 0},
             kd_t={"k": 0, "d": 0}, side_rounds={"ct": 0, "t": 0}, fastest_ace_s=None,
             fastest_opening_s=None, victims={}, killers={}, avenged={},
             buy_streak={"weapon": None, "n": 0}, multikills={}, team=None)
    return s


def store(conn, match_id: int, stats: dict[str, dict]) -> None:
    """Replace the match's fun_stats rows (fun_format = FUN_FORMAT); no commit."""
    conn.execute("DELETE FROM fun_stats WHERE match_id=?", (match_id,))
    conn.executemany(
        "INSERT INTO fun_stats(match_id, steam_id, fun_format, data) VALUES(?,?,?,?)",
        [(match_id, str(sid), FUN_FORMAT,
          json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False))
         for sid, data in sorted(stats.items())])
