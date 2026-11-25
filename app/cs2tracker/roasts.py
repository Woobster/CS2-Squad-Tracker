"""Roast of the night (spec v1.3 §4). Pure functions: no database access.

The 20 templates live in ``data/roasts.json``: ``{id, stat, when: {op, value}, needs?,
rate?, trash, friendly}``. ``trash`` is the spec wording verbatim; ``friendly`` is the same
joke, warmer. Placeholders are filled from ``awards.session_totals`` totals.

Conditions. A person is a candidate for a template when ``totals[stat]`` meets
``when`` (``>=`` / ``<=`` the value) and every ``needs`` entry; ``rate`` templates also
need ``played * 2 >= total`` (as rate awards). Special cases:

- ``played_share`` (template 14's ``needs``) is ``played / total``; op ``top`` means the
  person's value is the highest among the ticked people (ties included). That "top K/D"
  covers the event-file matches in ``totals`` only (Task 6 may pass the Summary's K/D
  instead). ``{p}`` is ``stats.played_text(played)`` ("½", "2½"). Template 14 also
  needs at least 2 ticked people and a K/D above 0.
- Template 8 needs the nemesis's in-game name in ``context["enemy_names"]`` (Steam ID ->
  name); without one it is skipped. That enemy is the only non-ticked name ever used.
- Template 18 is about the team, not a person: ``context["pistol_rounds_won"] >= 2`` and
  ``context["ecos_lost_after_pistol_win"]`` equal to it (``team_context`` computes both).
  Its standout is pistol rounds won / ``context["matches"]`` (at least 1).
- Template 2 is the one allowed change to the verbatim text: "a {weapon}" becomes
  "an {weapon}" when ``article`` says so.

Ranking. Each template gives at most one candidate: the person with the most extreme value
(ties: lowest person id). ``standout`` is ``awards.rated_standout``: the same per-match
measure as awards, against the ticked people who have the stat, with the template's
``when`` value as the bound (``<=`` templates: mean / value). Candidates
are sorted by standout (ties: template id); the first is used, then the next one about
someone else (the team roast counts as nobody). A roast about a person without a name in
``names`` is skipped. Level ``off`` gives no roasts.
"""
from __future__ import annotations

import json

from cs2tracker import awards
from cs2tracker.stats import played_text

TEMPLATES: list[dict] = json.loads((awards.DATA_DIR / "roasts.json")
                                   .read_text(encoding="utf-8"))
MAX_ROASTS = 2
TEAM_TEMPLATE = 18


def team_context(per_match: list[dict[str, dict]], person_of: dict[str, int],
                 ticked: set[int]) -> dict:
    """Team-level pistol counts for template 18: per match, the team of the first ticked
    account (by Steam ID), summed over the session; ``matches`` = matches with a ticked
    account."""
    won = lost = matches = 0
    for match in per_match:
        for sid in sorted(match):
            if person_of.get(str(sid)) in ticked:
                won += match[sid].get("pistol_rounds_won_team", 0) or 0
                lost += match[sid].get("ecos_lost_after_pistol_win_team", 0) or 0
                matches += 1
                break
    return {"pistol_rounds_won": won, "ecos_lost_after_pistol_win": lost, "matches": matches}


def _num(v) -> str:
    """Whole numbers as '12' (a float like 2.0 too), with thousands separators."""
    return awards.fmt_num(v)


def _vars(tid: int, t: dict, ctx: dict, played: dict, total: int, pid: int) -> dict | None:
    n = _num
    if tid == 1:
        return {"n": n(t["team_flashes"])}
    if tid == 2:
        return {"weapon": t["buy_streak"]["weapon"], "n": n(t["buy_streak_n"])}
    if tid == 3:
        return {"n": n(t["nades_held_at_death"])}
    if tid == 4:
        return {"n": n(t["fall_dmg"])}
    if tid == 5:
        return {"n": n(t["last_alive"]), "w": n(t.get("last_alive_won", 0))}
    if tid == 6:
        return {"p": n(t["hs_pct"])}
    if tid == 7:
        return {"money": n(t["money_spent"]), "k": n(t["kills"]), "per": n(t["cost_per_kill"])}
    if tid == 8:
        enemy = (ctx.get("enemy_names") or {}).get(t["nemesis"]["steam_id"])
        if not enemy:
            return None
        return {"enemy": enemy, "n": n(t["nemesis_n"])}
    if tid == 9:
        return {"n": n(t["opening_duels"]), "d": n(t["opening_deaths"])}
    if tid == 10:
        return {"n": n(t["clutches_tried"]), "w": n(t["clutches_won"])}
    if tid == 11:
        return {"n": n(t["wallbangs"])}
    if tid == 12:
        return {"n": n(t["team_dmg"])}
    if tid == 13:
        return {"n": n(t["reload_deaths"])}
    if tid == 14:
        return {"p": played_text(played.get(pid) or 0), "t": n(total)}
    if tid == 15:
        return {"n": n(t["smokes_thrown"])}
    if tid == 16:
        return {"kd1": f"{t['kd_ct_ratio']:.2f}", "kd2": f"{t['kd_t_ratio']:.2f}"}
    if tid == 17:
        return {}
    if tid == 19:
        return {"s": n(t["accuracy"])}
    if tid == 20:
        return {"n": n(t["first_deaths"])}
    raise KeyError(tid)


def _value(t: dict, stat: str, pid: int, played: dict, total: int):
    if stat == "played_share":
        return (played.get(pid) or 0) / total if total else None
    return t.get(stat)


def candidates(totals: dict[int, dict], played: dict[int, float], total: int,
               context: dict) -> list[dict]:
    """One candidate per template that fires, ranked (see module docstring)."""
    out = []
    for tpl in TEMPLATES:
        tid, stat, when = tpl["id"], tpl["stat"], tpl["when"]
        if tid == TEAM_TEMPLATE:
            won = context.get("pistol_rounds_won") or 0
            if won >= when["value"] and context.get("ecos_lost_after_pistol_win") == won:
                out.append({"id": tid, "person": None,
                            "standout": won / max(context.get("matches") or 0, 1),
                            "vars": {"n": _num(won)}, "tpl": tpl})
            continue
        everyone = {pid: v for pid, t in totals.items()
                    if (v := t.get(stat)) is not None
                    and (not tpl.get("rate") or awards.rate_ok(pid, played, total))}
        if not everyone:
            continue
        top = max(everyone.values())
        if when["op"] == "top" and (len(totals) < 2 or top <= 0):
            continue
        fit = {}
        for pid, v in everyone.items():
            if when["op"] == "top":
                ok = v >= top - awards._EPS
            else:
                ok = awards.check(v, when["op"], when["value"])
            ok = ok and all(awards.check(_value(totals[pid], c["stat"], pid, played, total),
                                         c["op"], c["value"]) for c in tpl.get("needs", []))
            if ok:
                fit[pid] = v
        if not fit:
            continue
        direction = "min" if when["op"] == "<=" else "max"
        sign = -1 if direction == "max" else 1
        for pid in sorted(fit, key=lambda p: (sign * fit[p], p)):
            v = _vars(tid, totals[pid], context, played, total, pid)
            if v is None:
                continue
            out.append({"id": tid, "person": pid, "vars": v, "tpl": tpl,
                        "standout": awards.rated_standout(stat, direction, when["value"],
                                                          [pid], everyone, totals, total)})
            break
    out.sort(key=lambda c: (-c["standout"], c["id"]))
    return out


# weapons whose spoken name starts with a vowel sound (buy_streak names, funstats.GUNS)
AN_WEAPONS = frozenset({"AK-47", "AUG", "AWP", "M249", "M4A1-S", "M4A4", "MP5-SD", "MP7",
                        "MP9", "R8 Revolver", "SG 553", "SSG 08", "XM1014"})


def article(weapon: str | None) -> str:
    """'an' for the weapons in AN_WEAPONS (an AK-47, an MP7), else 'a' (a P90, a FAMAS)."""
    return "an" if weapon in AN_WEAPONS else "a"


def render(c: dict, names: dict[int, str], level: str) -> str:
    text = c["tpl"]["friendly" if level == "friendly" else "trash"]
    if c["id"] == 2 and article(c["vars"].get("weapon")) == "an":
        text = text.replace(" a {weapon}", " an {weapon}")
    v = dict(c["vars"])
    if c["person"] is not None:
        v["name"] = names[c["person"]]
    return text.format(**v)


def pick(totals: dict[int, dict], names: dict[int, str], played: dict[int, float],
         total: int, level: str, context: dict) -> list[str]:
    """Up to 2 roast lines, never two about the same person. level: trash|friendly|off."""
    if level not in ("trash", "friendly"):
        return []
    chosen: list[dict] = []
    for c in candidates(totals, played, total, context or {}):
        if c["person"] is not None and not names.get(c["person"]):
            continue      # never show a raw person id
        if any(c["person"] is not None and c["person"] == o["person"] for o in chosen):
            continue
        chosen.append(c)
        if len(chosen) == MAX_ROASTS:
            break
    return [render(c, names, level) for c in chosen]
