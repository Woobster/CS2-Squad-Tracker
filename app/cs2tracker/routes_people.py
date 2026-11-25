"""People and account routes."""
import dataclasses
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from cs2tracker import db, records, stats
from cs2tracker.context import STEAM_ID_RE, Ctx, owner_required
from cs2tracker.serialize import steam_url


class PersonIn(BaseModel):
    name: str
    group_ids: list[int] | None = None
    tracked: bool = False


class PersonPatch(BaseModel):
    name: str | None = None
    group_ids: list[int] | None = None
    tracked: bool | None = None


class AccountIn(BaseModel):
    steam_id: str

    @field_validator("steam_id")
    @classmethod
    def _check(cls, v: str) -> str:
        if not STEAM_ID_RE.match(v):
            raise ValueError("steam_id must be 17 digits")
        return v


def _exists(conn, pid: int) -> bool:
    return conn.execute("SELECT 1 FROM person WHERE id=?", (pid,)).fetchone() is not None


def build(ctx: Ctx) -> APIRouter:
    r = APIRouter()
    need_owner = owner_required(ctx)

    @r.get("/api/people", dependencies=[need_owner])
    def list_people(since: date | None = None, until: date | None = None):
        with ctx.db() as conn:
            st = {s.person_id: {k: v for k, v in dataclasses.asdict(s).items() if k != "group"}
                  for s in stats.person_stats(conn, since=since, until=until)}
            groups = db.person_groups(conn)
            together = stats.games_together(conn, ctx.me(conn))
            accounts: dict[int, list] = {}
            for sid, pid, name in conn.execute(
                    "SELECT steam_id, person_id, last_seen_name FROM account "
                    "WHERE person_id IS NOT NULL ORDER BY steam_id"):
                accounts.setdefault(pid, []).append(
                    {"steam_id": sid, "last_seen_name": name, "steam_url": steam_url(sid)})
            refs: dict[int, list] = {}
            for pid, sid, games, win, mine, theirs in conn.execute(
                    "SELECT person_id, steam_id, games, win_pct, my_rating, their_rating "
                    "FROM person_leetify_ref ORDER BY steam_id"):
                refs.setdefault(pid, []).append(
                    {"steam_id": sid, "games": games, "win_pct": win, "my_rating": mine,
                     "their_rating": theirs})
            me_pid = conn.execute("SELECT person_id FROM account WHERE steam_id=?",
                                  (ctx.me(conn),)).fetchone()
            me_pid = me_pid[0] if me_pid else None
            return [{"id": pid, "name": name, "groups": groups.get(pid, []),
                     # "games together" is meaningless for me: null, rendered as a dash
                     "games_together": None if pid == me_pid else together.get(pid, 0), "tracked": bool(tr),
                     "accounts": accounts.get(pid, []), "stats": st.get(pid),
                     "leetify_refs": refs.get(pid, [])}
                    for pid, name, tr in conn.execute(
                        'SELECT id, display_name, tracked FROM person '
                        "ORDER BY display_name COLLATE NOCASE, id")]

    @r.post("/api/people")
    def create_person(body: PersonIn):
        with ctx.db() as conn:
            try:
                pid = db.create_person(conn, body.name, body.tracked, group_ids=body.group_ids)
            except ValueError as e:
                raise HTTPException(400, str(e)) from None
            return {"id": pid}

    @r.patch("/api/people/{person_id}")
    def patch_person(person_id: int, body: PersonPatch):
        fields = body.model_fields_set
        with ctx.db() as conn:
            if not _exists(conn, person_id):
                raise HTTPException(404, "no such person")
            if "name" in fields and not body.name:
                raise HTTPException(422, "name must not be empty")
            if "group_ids" in fields:  # validated first: an unknown id changes nothing
                try:
                    db.set_person_groups(conn, person_id, body.group_ids or [])
                except ValueError as e:
                    raise HTTPException(400, str(e)) from None
            for f, col in (("name", "display_name"), ("tracked", "tracked")):
                if f in fields:
                    v = getattr(body, f)
                    conn.execute(f"UPDATE person SET {col}=? WHERE id=?",
                                 (int(v) if f == "tracked" else v, person_id))
            conn.commit()
            return {"id": person_id}

    @r.post("/api/people/{person_id}/accounts")
    def link(person_id: int, body: AccountIn):
        with ctx.db() as conn:
            if not _exists(conn, person_id):
                raise HTTPException(404, "no such person")
            if conn.execute("SELECT 1 FROM account WHERE steam_id=?",
                            (body.steam_id,)).fetchone() is None:
                db.upsert_account(conn, body.steam_id, None)
            prev = conn.execute("SELECT person_id FROM account WHERE steam_id=?",
                                (body.steam_id,)).fetchone()[0]
            db.link_account(conn, body.steam_id, person_id, commit=False)
            records.rebuild(conn, [p for p in (person_id, prev) if p is not None])
            conn.commit()
            return {"id": person_id, "steam_id": body.steam_id}

    @r.delete("/api/people/{person_id}")
    def delete_person(person_id: int):
        with ctx.db() as conn:
            if not _exists(conn, person_id):
                raise HTTPException(404, "no such person")
            conn.execute("UPDATE account SET person_id=NULL WHERE person_id=?", (person_id,))
            conn.execute("DELETE FROM person_leetify_ref WHERE person_id=?", (person_id,))
            conn.execute("DELETE FROM person WHERE id=?", (person_id,))
            records.rebuild(conn, [person_id])  # its records go (holders follow person links)
            conn.commit()
            return {"deleted": person_id}

    return r
