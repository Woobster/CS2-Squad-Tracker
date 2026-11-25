"""Who is "me": the saved Steam ID, one worked out from the matches, or nobody yet.

On start (``resolve``):
1. ``me_steam_id`` saved -> use it.
2. Not saved, matches exist -> the Steam ID on ``team = 'mine'`` in the most matches is the
   owner when it is in >= 90% of all matches and in >= 3 of them. It is saved, and a
   one-time notice is kept in ``owner_inferred`` until the page has shown it.
   Otherwise the top 3 are offered on the welcome screen.
3. Nothing -> the welcome screen (``onboarding_step = 'steam_id'``).
"""
from dataclasses import dataclass, field
from typing import Literal

from cs2tracker import db, records

INFER_SHARE = 0.9
INFER_MIN_MATCHES = 3
STEPS = ("steam_id", "history", "older", "done")
NOTICE = "Using {name} ({masked}) as you. Change it in Settings if that's wrong."


@dataclass
class OwnerState:
    status: Literal["set", "needs_setup", "ambiguous"]
    steam_id: str | None = None
    name: str | None = None
    candidates: list[dict] = field(default_factory=list)
    notice: str | None = None


def masked(steam_id: str) -> str:
    return f"{steam_id[:4]}…{steam_id[-4:]}"


def _name(conn, steam_id: str) -> str | None:
    r = conn.execute("SELECT last_seen_name FROM account WHERE steam_id=?", (steam_id,)).fetchone()
    return r[0] if r and r[0] else None


def _counts(conn) -> tuple[int, list[tuple[str, int]]]:
    total = conn.execute("SELECT COUNT(*) FROM match").fetchone()[0]
    top = conn.execute(
        "SELECT steam_id, COUNT(DISTINCT match_id) AS n FROM match_player WHERE team='mine' "
        "GROUP BY steam_id ORDER BY n DESC, steam_id LIMIT 3").fetchall()
    return total, [(s, n) for s, n in top]


def current(conn) -> OwnerState:
    """The state now, without writing anything."""
    me = db.get_setting(conn, "me_steam_id")
    if me:
        name = _name(conn, me)
        notice = None
        if db.get_setting(conn, "owner_inferred") == "1":
            notice = NOTICE.format(name=name or "this account", masked=masked(me))
        return OwnerState("set", me, name, notice=notice)
    total, top = _counts(conn)
    if not top or total < INFER_MIN_MATCHES:  # too little to offer anyone
        return OwnerState("needs_setup")
    cands = [{"steam_id": s, "name": _name(conn, s), "matches": n} for s, n in top]
    return OwnerState("ambiguous", candidates=cands)


def resolve(conn) -> OwnerState:
    """Run once on start: infer and save the owner when the matches make it clear."""
    if not db.get_setting(conn, "me_steam_id"):
        total, top = _counts(conn)
        if top and top[0][1] >= INFER_MIN_MATCHES and top[0][1] >= INFER_SHARE * total:
            db.set_setting(conn, "me_steam_id", top[0][0])
            db.set_setting(conn, "owner_inferred", "1")
        elif db.get_setting(conn, "onboarding_step") is None:
            db.set_setting(conn, "onboarding_step", "steam_id")
    return current(conn)


def set_owner(conn, steam_id: str) -> None:
    """Save the owner. Records leave the owner out, so they're rebuilt when it changes."""
    changed = db.get_setting(conn, "me_steam_id") != steam_id
    db.set_setting(conn, "me_steam_id", steam_id)
    conn.execute("DELETE FROM settings WHERE key='owner_inferred'")
    if changed:
        records.rebuild(conn)
    conn.commit()
    if db.get_setting(conn, "onboarding_step") == "steam_id":
        db.set_setting(conn, "onboarding_step", "history")


def clear_notice(conn) -> None:
    conn.execute("DELETE FROM settings WHERE key='owner_inferred'")
    conn.commit()


def set_step(conn, step: str) -> None:
    if step not in STEPS:
        raise ValueError(step)
    db.set_setting(conn, "onboarding_step", step)
