"""Awards of the night (spec v1.3 §3). Pure functions: no database access.

``session_totals`` adds up the per-match fun-stats rows (``funstats.compute`` shape) of each
ticked person across their accounts; ``pick`` chooses the award cards from those totals.
The catalogue (titles, stats, minimums, value lines, blurbs) is ``data/awards.json``.

Session totals
--------------
Numbers are summed, and dicts of numbers (``kd_ct``, ``nades_thrown``, ``killers``, ...) are
merged by summing per key. Exceptions: ``best_kill_streak`` and ``buy_streak`` (by ``n``) take
the session maximum, ``longest`` the longest kill per category and ``closest`` the shortest,
``fastest_*`` the minimum; ``rating`` and ``avg_time_alive_s`` are averaged weighted by
``rounds_played``; ``team`` is dropped. Then these are derived from the sums (None when the
denominator is 0):

- ``matches``: matches with a row for the person;
- ``hs_pct`` = 100 * hs_kills / kills; ``accuracy`` = 100 * hits / shots;
  ``adr`` = damage / rounds_played; ``kd`` = kills / max(deaths, 1);
- ``clutches_tried`` / ``clutches_won`` (all sizes); ``clutch_win_pct``;
- ``smokes_thrown``; ``buy_streak_n``;
- ``opening_duels`` = opening kills + opening deaths; ``opening_death_pct``;
- ``cost_per_kill`` = money_spent / kills;
- ``kd_ct_ratio`` / ``kd_t_ratio`` = k / max(d, 1); ``kd_side_gap`` = the larger side ratio
  over the smaller (the smaller floored at 0.1), when both sides have >= 5 rounds and the
  larger ratio is > 0;
- ``nemesis`` = ``{steam_id, n}`` for the enemy who killed the person most (ties: lowest
  Steam ID), ``nemesis_n`` its count.

Picking
-------
An award goes to the person(s) with the best value (``direction`` max or min). A max award
needs value >= ``min``; a min award needs value <= ``min`` (its bound). ``needs`` adds extra
conditions (e.g. Stormtrooper: shots >= 100). Rate awards need ``played * 2 >= total``.
Ties (equal values) share the card.

``standout`` compares per-match rates, so a rare single event or a long night doesn't
dominate: for a count stat each eligible person's rate is total / their matches in the
session; rate stats (``RATE_STATS``) are used as they are. The winner (still decided by
the session total) scores best winner rate / max(mean rate, floor), where floor =
``min`` / total session matches for count stats with direction max (else 0). Min awards:
mean / winner rate. A zero denominator gives 99.0 when the numerator is > 0, else 1.0;
capped at 99.0. Cards are chosen skill first, then alternating kinds while both have cards
left, each kind by standout (ties: catalogue order); a card whose winner already holds 2,
or has no name in ``names``, is skipped; at most 12.
"""
from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
CATALOGUE: list[dict] = json.loads((DATA_DIR / "awards.json").read_text(encoding="utf-8"))

MAX_CARDS = 12
MAX_PER_PERSON = 2
STANDOUT_CAP = 99.0
_MIN_KEYS = {"fastest_opening_s", "fastest_ace_s"}
_WEIGHTED = {"rating", "avg_time_alive_s"}
_EPS = 1e-9
# stats that are already rates (or session maxima): standout uses them as they are
RATE_STATS = {"hs_pct", "accuracy", "adr", "kd", "cost_per_kill", "kd_side_gap",
              "clutch_win_pct", "opening_death_pct", "buy_streak_n", "played_share",
              "rating", "avg_time_alive_s", "best_kill_streak"}


# ---------------------------------------------------------------- session totals

def _merge_num_dict(into: dict, d: dict) -> None:
    for k, v in d.items():
        if isinstance(v, dict):
            _merge_num_dict(into.setdefault(k, {}), v)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            into[k] = into.get(k, 0) + v


def _add(acc: dict, row: dict) -> None:
    rounds = row.get("rounds_played") or 0
    for k, v in row.items():
        if k == "team":
            continue
        if k == "longest":
            cur = acc.setdefault("longest", {})
            for cat, e in (v or {}).items():
                if cat not in cur:
                    cur[cat] = None
                if e is not None and (cur[cat] is None or e["m"] > cur[cat]["m"]):
                    cur[cat] = dict(e)
        elif k == "closest":
            cur = acc.get("closest")
            if v is not None and (cur is None or v["m"] < cur["m"]):
                acc["closest"] = dict(v)
            else:
                acc.setdefault("closest", None)
        elif k == "buy_streak":
            cur = acc.get("buy_streak")
            if cur is None or (v or {}).get("n", 0) > cur["n"]:
                acc["buy_streak"] = dict(v or {"weapon": None, "n": 0})
        elif k in _MIN_KEYS:
            cur = acc.get(k)
            acc[k] = v if cur is None else (cur if v is None else min(cur, v))
        elif k == "best_kill_streak":
            acc[k] = max(acc.get(k, 0), v or 0)
        elif k in _WEIGHTED:
            acc.setdefault("_w", {})
            acc["_w"][k] = acc["_w"].get(k, 0.0) + (v or 0) * rounds
        elif isinstance(v, dict):
            _merge_num_dict(acc.setdefault(k, {}), v)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            acc[k] = acc.get(k, 0) + v


def _div(a, b):
    return a / b if b else None


def derive(t: dict) -> dict:
    """Add the derived rates and counts listed in the module docstring to totals ``t``."""
    g = t.get
    rounds = g("rounds_played", 0)
    w = t.pop("_w", {})
    for k in _WEIGHTED:
        t[k] = round(w[k] / rounds, 3) if rounds and k in w else 0.0
    t["hs_pct"] = _div(100 * g("hs_kills", 0), g("kills", 0))
    t["accuracy"] = _div(100 * g("hits", 0), g("shots", 0))
    t["adr"] = _div(g("damage", 0), rounds)
    t["kd"] = g("kills", 0) / max(g("deaths", 0), 1)
    t["clutches_tried"] = sum((g("clutch_attempts") or {}).values())
    t["clutches_won"] = sum((g("clutch_wins") or {}).values())
    t["clutch_win_pct"] = _div(100 * t["clutches_won"], t["clutches_tried"])
    t["smokes_thrown"] = (g("nades_thrown") or {}).get("smoke", 0)
    t["buy_streak_n"] = (g("buy_streak") or {}).get("n", 0)
    t["opening_duels"] = g("opening_kills", 0) + g("opening_deaths", 0)
    t["opening_death_pct"] = _div(100 * g("opening_deaths", 0), t["opening_duels"])
    t["cost_per_kill"] = _div(g("money_spent", 0), g("kills", 0))
    ct, tt = g("kd_ct") or {}, g("kd_t") or {}
    t["kd_ct_ratio"] = ct.get("k", 0) / max(ct.get("d", 0), 1)
    t["kd_t_ratio"] = tt.get("k", 0) / max(tt.get("d", 0), 1)
    sides = g("side_rounds") or {}
    hi = max(t["kd_ct_ratio"], t["kd_t_ratio"])
    lo = min(t["kd_ct_ratio"], t["kd_t_ratio"])
    t["kd_side_gap"] = (hi / max(lo, 0.1)
                        if hi > 0 and sides.get("ct", 0) >= 5 and sides.get("t", 0) >= 5
                        else None)
    killers = g("killers") or {}
    if killers:
        sid, n = min(killers.items(), key=lambda kv: (-kv[1], kv[0]))
        t["nemesis"] = {"steam_id": sid, "n": n}
    else:
        t["nemesis"] = None
    t["nemesis_n"] = t["nemesis"]["n"] if t["nemesis"] else 0
    return t


def session_totals(per_match: list[dict[str, dict]], person_of: dict[str, int],
                   ticked: set[int]) -> dict[int, dict]:
    """Sum each ticked person's fun-stats rows over the session (see module docstring).

    per_match: one ``{steam_id: stats}`` per event-file match; person_of: account -> person.
    Accounts without a person, or whose person isn't ticked, are left out."""
    acc: dict[int, dict] = {}
    for match in per_match:
        seen: set[int] = set()
        for sid in sorted(match):
            pid = person_of.get(str(sid))
            if pid is None or pid not in ticked:
                continue
            a = acc.setdefault(pid, {"matches": 0})
            if pid not in seen:
                a["matches"] += 1
                seen.add(pid)
            _add(a, match[sid])
    return {pid: derive(a) for pid, a in acc.items()}


# ---------------------------------------------------------------- picking

def check(value, op: str, bound) -> bool:
    if value is None:
        return False
    if op == ">=":
        return value >= bound - _EPS
    if op == "<=":
        return value <= bound + _EPS
    raise ValueError(op)


def standout(value: float, values: list[float], direction: str, floor: float = 0.0) -> float:
    """Winner rate vs the mean of everyone's rates (see module docstring)."""
    if not values:
        return 1.0
    mean = sum(values) / len(values)
    num, den = (value, max(mean, floor)) if direction == "max" else (mean, value)
    if den <= 0:
        return STANDOUT_CAP if num > 0 else 1.0
    return min(num / den, STANDOUT_CAP)


def rate_ok(pid: int, played: dict[int, float], total: int) -> bool:
    return (played.get(pid) or 0) * 2 >= total


def per_match(stat: str, value, t: dict):
    """The value as a per-match rate for standout (rate stats unchanged)."""
    if stat in RATE_STATS or value is None:
        return value
    return value / max(t.get("matches") or 1, 1)


def rated_standout(stat: str, direction: str, bound, winners: list[int],
                   vals: dict[int, float], totals: dict[int, dict], total: int) -> float:
    """Standout of a card/roast won by ``winners`` among the eligible ``vals``."""
    rates = {p: per_match(stat, v, totals[p]) for p, v in vals.items()}
    best = (max if direction == "max" else min)(rates[p] for p in winners)
    floor = (bound / total if direction == "max" and stat not in RATE_STATS and total
             and bound is not None else 0.0)
    return standout(best, list(rates.values()), direction, floor)


def _eligible(a: dict, totals: dict[int, dict], played, total) -> dict[int, float]:
    vals = {}
    for pid, t in totals.items():
        v = t.get(a["stat"])
        if v is None:
            continue
        if a.get("rate") and not rate_ok(pid, played, total):
            continue
        if not all(check(t.get(n["stat"]), n["op"], n["value"]) for n in a.get("needs", [])):
            continue
        vals[pid] = v
    return vals


def fmt_num(v) -> str:
    """Integers with thousands separators; other numbers rounded to an integer."""
    return f"{int(round(v)):,}"


def candidates(totals: dict[int, dict], played: dict[int, float], total: int) -> list[dict]:
    """Every catalogue award that someone qualifies for, in catalogue order (no names)."""
    out = []
    for idx, a in enumerate(CATALOGUE):
        vals = _eligible(a, totals, played, total)
        if not vals:
            continue
        best = (max if a["direction"] == "max" else min)(vals.values())
        op = ">=" if a["direction"] == "max" else "<="
        if not check(best, op, a["min"]):
            continue
        winners = sorted(pid for pid, v in vals.items() if abs(v - best) <= _EPS)
        out.append({"id": a["id"], "title": a["title"], "kind": a["kind"],
                    "winners": winners, "value": best,
                    "standout": rated_standout(a["stat"], a["direction"], a["min"], winners,
                                               vals, totals, total),
                    "_order": idx, "_award": a})
    return out


def _card(c: dict, names: dict[int, str]) -> dict:
    a = c["_award"]
    n = fmt_num(c["value"])
    line = a.get("value_line_one") if n == "1" and a.get("value_line_one") else a["value_line"]
    return {"id": c["id"], "title": c["title"], "kind": c["kind"], "winners": c["winners"],
            "name": " & ".join(names[p] for p in c["winners"]),
            "value": c["value"], "value_line": line.format(n=n), "blurb": a["blurb"],
            "standout": c["standout"]}


def pick(totals: dict[int, dict], names: dict[int, str], played: dict[int, float],
         total: int) -> list[dict]:
    """The award cards for the session, in display order (see module docstring)."""
    queues = {k: sorted((c for c in candidates(totals, played, total) if c["kind"] == k),
                        key=lambda c: (-c["standout"], c["_order"]))
              for k in ("skill", "shame")}
    held: dict[int, int] = {}
    chosen: list[dict] = []
    want = "skill"

    def take(kind):
        q = queues[kind]
        while q:
            c = q.pop(0)
            if all(held.get(p, 0) < MAX_PER_PERSON and names.get(p) for p in c["winners"]):
                return c
        return None

    while len(chosen) < MAX_CARDS and (queues["skill"] or queues["shame"]):
        other = "shame" if want == "skill" else "skill"
        c = take(want) or take(other)
        if c is None:
            break
        for p in c["winners"]:
            held[p] = held.get(p, 0) + 1
        chosen.append(c)
        want = "shame" if c["kind"] == "skill" else "skill"
    return [_card(c, names) for c in chosen]
