"""Shared per-app state: paths, settings access and a fresh DB connection per use."""
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from fastapi import Depends

from cs2tracker import db, steam_paths
from cs2tracker.events import extract_events
from cs2tracker.jobs import JobRunner

DEFAULT_REPLAYS = (r"C:\Program Files (x86)\Steam\steamapps\common"
                   r"\Counter-Strike Global Offensive\game\csgo\replays")
STEAM_ID_RE = re.compile(r"^\d{17}$")
PKG = Path(__file__).resolve().parent
NO_OWNER = "Set your Steam ID first."


class NoOwner(Exception):
    """A route that needs "me" was called before the Steam ID is known (HTTP 428)."""


def owner_required(ctx: "Ctx"):
    """A FastAPI dependency: raises NoOwner while me_steam_id isn't set."""
    def check() -> None:
        with ctx.db() as conn:
            if not ctx.me(conn):
                raise NoOwner()
    return Depends(check)


def candidate_roots() -> list[Path]:
    return [PKG.parent, PKG.parent.parent]


def default_boiler_cmd() -> list[str] | None:
    if os.name != "nt":
        return None
    for root in candidate_roots():
        exe = root / "vendor" / "boiler-writter" / "boiler-writter.exe"
        if exe.is_file():
            return [str(exe)]
    return None


@dataclass
class Ctx:
    data_dir: Path
    parse: Callable
    boiler_cmd: list[str] | None
    runner: JobRunner
    extract: Callable = extract_events

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tracker.db"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def imported_dir(self) -> Path:
        return self.data_dir / "imported"

    @property
    def events_dir(self) -> Path:
        d = self.data_dir / "events"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @contextmanager
    def db(self) -> Iterator:
        conn = db.connect(self.db_path, self.backups_dir)
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def me(conn) -> str | None:
        return db.get_setting(conn, "me_steam_id") or None

    @staticmethod
    def replays_dir(conn) -> str:
        """The saved (or default) replays folder; when it doesn't exist, the one found
        through Steam's library list, if any."""
        configured = db.get_setting(conn, "replays_dir", DEFAULT_REPLAYS)
        if configured and configured.strip() and Path(configured).is_dir():
            return configured
        found = steam_paths.find_replays_dir()
        return str(found) if found is not None else configured

    def job_settings(self, conn) -> dict:
        return {"me_steam_id": self.me(conn),
                "replays_dir": self.replays_dir(conn),
                "backups_dir": str(self.backups_dir), "boiler_cmd": self.boiler_cmd,
                "tmp_dir": str(self.data_dir / "tmp"), "events_dir": str(self.events_dir),
                "imported_dir": str(self.imported_dir),
                # secrets: only passed to the Update job, never put in a result or log
                "steam_api_key": db.get_setting(conn, "steam_api_key"),
                "match_auth_code": db.get_setting(conn, "match_auth_code"),
                "demo_retention_days": db.get_setting(conn, "demo_retention_days")}
