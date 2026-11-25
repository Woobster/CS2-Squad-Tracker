"""Custom group routes."""
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from cs2tracker import db
from cs2tracker.context import Ctx


class GroupIn(BaseModel):
    name: str


def build(ctx: Ctx) -> APIRouter:
    r = APIRouter()

    @r.get("/api/groups")
    def list_groups():
        with ctx.db() as conn:
            return db.list_groups(conn)

    @r.post("/api/groups", status_code=201)
    def create_group(body: GroupIn):
        with ctx.db() as conn:
            try:
                return {"id": db.create_group(conn, body.name)}
            except ValueError as e:
                raise HTTPException(400, str(e)) from None
            except db.GroupExistsError as e:
                raise HTTPException(409, f"A group called {e.name} already exists.") from None

    @r.patch("/api/groups/{group_id}")
    def rename_group(group_id: int, body: GroupIn):
        with ctx.db() as conn:
            try:
                ok = db.rename_group(conn, group_id, body.name)
            except ValueError as e:
                raise HTTPException(400, str(e)) from None
            except db.GroupExistsError as e:
                raise HTTPException(409, f"A group called {e.name} already exists.") from None
            if not ok:
                raise HTTPException(404, "no such group")
            return {"id": group_id}

    @r.delete("/api/groups/{group_id}", status_code=204)
    def delete_group(group_id: int):
        with ctx.db() as conn:
            if not db.delete_group(conn, group_id):
                raise HTTPException(404, "no such group")
        return Response(status_code=204)

    return r
