"""FastAPI app: settings, import/update jobs, backup and the static page."""
import base64
import binascii
import dataclasses
import json
import math
import os
import re
import urllib.request
from importlib import metadata
from pathlib import Path, PureWindowsPath

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, field_validator

from cs2tracker import (dashboard, db, history, importer, records, reparse, retention, rivals, routes_groups,
                        routes_matches, routes_people, owner, sharecode, steamid, steam_history, steam_paths, steam_sync)
from cs2tracker.context import (NO_OWNER, STEAM_ID_RE, Ctx, NoOwner, default_boiler_cmd,
                                owner_required)
from cs2tracker.demo import parse_demo
from cs2tracker.events import EVENTS_FORMAT, bundle_path, extract_events
from cs2tracker.funstats import FUN_FORMAT
from cs2tracker.jobs import Job, JobRunner
from cs2tracker.serialize import jsonable

APP_VERSION = "1.3.2"
STATIC = Path(__file__).resolve().parent / "static"
ALLOWED_SUFFIXES = (".dem", ".zip", ".gz", ".bz2")
BUSY = "Busy importing — try again when the current job finishes."
OUTDATED = ("Your data was upgraded by a newer version of CS2 Squad Tracker. "
            "Close this window and start the newer version.")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")
BAD_SHARE_CODE = "That doesn't look like a match sharing code."
BAD_RETENTION = "Delete demos after they're read must be Never, 7, 30 or 90 days."
NEED_ACCESS = "Enter the Steam Web API key and the authentication code first."
NEED_CODE = ("Enter a match sharing code first (or import one of your demos from the "
             "replays folder so the app knows a match).")
SECRET_KEYS = ("steam_api_key", "match_auth_code")
STEAM_HISTORY_MAX = 20 * 1024 * 1024
STEAM_HISTORY_TOO_BIG = "That file is too big for a Steam match history (max 20 MB)."
BAD_RIVALS_MIN = "Rivals minimum games must be between 2 and 10."
NO_RIVAL = "no such rival"


def mask(secret: str | None) -> str | None:
    """'••••' plus the last 4 characters; too short to show any of it: just '••••'."""
    if not secret:
        return None
    return "••••" + (secret[-4:] if len(secret) >= 8 else "")


def checked_share_code(code: str) -> str:
    code = code.strip()
    try:
        sharecode.decode(code)
    except ValueError:
        raise HTTPException(400, BAD_SHARE_CODE) from None
    return code


class SettingsIn(BaseModel):
    me_steam_id: str | None = None
    replays_dir: str | None = None
    steam_api_key: str | None = None      # "" clears, omitted keeps
    match_auth_code: str | None = None
    start_share_code: str | None = None
    auto_update: bool | None = None
    demo_retention_days: int | None = None  # null = never; only applied when sent
    rivals_min_games: int | None = None     # 2-10; omitted keeps

    @field_validator("me_steam_id")
    @classmethod
    def _steam(cls, v):
        if v is not None and not STEAM_ID_RE.match(v):
            raise ValueError("me_steam_id must be 17 digits")
        return v

    @field_validator("replays_dir")
    @classmethod
    def _dir(cls, v):
        if v is None:
            return v
        v = v.strip().strip("\"'").strip()  # "Copy as path" in Explorer adds quotes
        if not v:
            raise ValueError("replays_dir must not be empty")
        return v


class TestHistoryIn(BaseModel):
    """Values typed in Settings but not saved yet; a missing or empty one uses the saved value."""
    steam_api_key: str | None = None
    match_auth_code: str | None = None
    start_share_code: str | None = None


def _failed_reparse_ids(conn) -> set[int]:
    """Match ids whose demo failed to parse on the last re-read (excluded from the notice)."""
    try:
        return {int(x) for x in json.loads(db.get_setting(conn, "reparse_failed_ids", "[]"))}
    except (ValueError, TypeError):
        return set()


def upload_mtime(value) -> float | None:
    """A browser File.lastModified (ms since 1970) as seconds, or None if unusable."""
    if not isinstance(value, str):
        return None
    try:
        ms = float(value)
    except ValueError:
        return None
    if not math.isfinite(ms) or ms <= 0 or ms > 4102444800000:  # after 2100: nonsense
        return None
    return ms / 1000


def decode_token(token: str) -> Path:
    if not TOKEN_RE.match(token):
        raise HTTPException(400, "bad path token")
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        return Path(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise HTTPException(400, "bad path token") from None


def startup_retention(ctx: Ctx, conn) -> None:
    """Spec §4: delete old, fully read demos once at start (after migrate). Nothing
    happens when the setting is never; a failure here never stops the app."""
    days = retention.parse_days(db.get_setting(conn, "demo_retention_days"))
    if days is None:
        return
    try:
        allowed = [Path(ctx.replays_dir(conn)), ctx.imported_dir]
        retention.delete_old_demos(conn, days, ctx.events_dir, allowed)
    except Exception:
        pass


def create_app(data_dir: Path, *, parse=parse_demo, boiler_cmd: list[str] | None = None,
               extract=extract_events, history_opener=urllib.request.urlopen) -> FastAPI:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    ctx = Ctx(data_dir, parse, boiler_cmd if boiler_cmd is not None else default_boiler_cmd(),
              JobRunner(), extract)
    with ctx.db() as conn:
        owner.resolve(conn)
        records.backfill(conn)
        startup_retention(ctx, conn)

    app = FastAPI(title="CS2 Squad Tracker")
    app.state.ctx = ctx
    need_owner = owner_required(ctx)

    @app.exception_handler(NoOwner)
    def _no_owner(request: Request, exc: NoOwner):
        return JSONResponse({"detail": NO_OWNER, "code": "no_owner"}, status_code=428)

    @app.exception_handler(db.NewerDatabaseError)
    def _newer_db(request: Request, exc: db.NewerDatabaseError):
        return JSONResponse({"detail": OUTDATED, "code": "newer_db"}, status_code=409)

    app.include_router(routes_groups.build(ctx))
    app.include_router(routes_people.build(ctx))
    app.include_router(routes_matches.build(ctx))
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def auto_update_on(conn) -> bool:
        return db.get_setting(conn, "auto_update", db.settings_defaults()["auto_update"]) != "0"

    def read_settings() -> dict:
        with ctx.db() as conn:
            s = ctx.job_settings(conn)
            return {"me_steam_id": s["me_steam_id"], "replays_dir": s["replays_dir"],
                    "steam_api_key_masked": mask(s["steam_api_key"]),
                    "match_auth_code_masked": mask(s["match_auth_code"]),
                    "last_share_code": db.get_setting(conn, steam_sync.LAST_CODE) or None,
                    "pending_count": len(steam_sync.load_pending(conn)),
                    "auto_update": auto_update_on(conn),
                    "demo_retention_days": retention.parse_days(s["demo_retention_days"]),
                    "rivals_min_games": rivals.min_games(conn),
                    "disk": retention.disk_summary(conn, ctx.events_dir)}

    def delete_setting(conn, key: str) -> None:
        conn.execute("DELETE FROM settings WHERE key=?", (key,))
        conn.commit()

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    class OnboardingIn(BaseModel):
        step: str

    @app.get("/api/owner")
    def get_owner():
        with ctx.db() as conn:
            st = owner.current(conn)
            return {**dataclasses.asdict(st),
                    "onboarding_step": db.get_setting(conn, "onboarding_step")}

    class OwnerIn(BaseModel):
        input: str
        api_key: str | None = None   # typed on the welcome screen for a /id/<name> link

    @app.post("/api/owner")
    def post_owner(body: OwnerIn):
        typed_key = (body.api_key or "").strip()
        with ctx.db() as conn:
            key = typed_key or db.get_setting(conn, "steam_api_key")
        try:
            sid = steamid.parse(body.input, key, opener=history_opener)
        except steamid.SteamIdError as e:
            raise HTTPException(400, e.message) from None
        with ctx.db() as conn:
            if typed_key and steamid.needs_key(body.input):   # it worked: keep it for step 2
                db.set_setting(conn, "steam_api_key", typed_key)
            owner.set_owner(conn, sid)
        return {"steam_id": sid}

    @app.post("/api/owner/notice-seen")
    def owner_notice_seen():
        with ctx.db() as conn:
            owner.clear_notice(conn)
        return {"ok": True}

    @app.put("/api/onboarding")
    def put_onboarding(body: OnboardingIn):
        if body.step not in owner.STEPS:
            raise HTTPException(400, "step must be one of " + ", ".join(owner.STEPS))
        with ctx.db() as conn:
            owner.set_step(conn, body.step)
        return {"onboarding_step": body.step}

    @app.get("/api/settings")
    def get_settings():
        return read_settings()

    @app.put("/api/settings")
    def put_settings(body: SettingsIn):
        sent = body.model_fields_set
        # validate everything before writing anything
        typed = (body.start_share_code or "").strip()
        start = checked_share_code(typed) if typed else None
        days = body.demo_retention_days
        if "demo_retention_days" in sent and days is not None and days not in retention.RETENTION_DAYS:
            raise HTTPException(400, BAD_RETENTION)
        if body.rivals_min_games is not None and body.rivals_min_games not in rivals.MIN_GAMES_RANGE:
            raise HTTPException(400, BAD_RIVALS_MIN)
        with ctx.db() as conn:
            if body.rivals_min_games is not None:
                db.set_setting(conn, rivals.MIN_GAMES_KEY, str(body.rivals_min_games))
            if body.me_steam_id is not None:
                owner.set_owner(conn, body.me_steam_id)
            if body.replays_dir is not None:
                db.set_setting(conn, "replays_dir", body.replays_dir)
            for k in SECRET_KEYS:
                v = getattr(body, k)
                if v is None:
                    continue
                if v.strip():
                    db.set_setting(conn, k, v.strip())
                else:
                    delete_setting(conn, k)
            if start is not None:  # the next Update queues it once (steam_sync.CHAIN_MARK)
                db.set_setting(conn, steam_sync.LAST_CODE, start)
                delete_setting(conn, steam_sync.CHAIN_MARK)
            if body.auto_update is not None:
                db.set_setting(conn, "auto_update", "1" if body.auto_update else "0")
            if "demo_retention_days" in sent:
                if days is None:
                    delete_setting(conn, "demo_retention_days")
                else:
                    db.set_setting(conn, "demo_retention_days", str(days))
        return read_settings()

    @app.get("/api/dashboard", dependencies=[need_owner])
    def dashboard_data():
        with ctx.db() as conn:
            return dashboard.build(conn, ctx.me(conn))

    @app.get("/api/rivals", dependencies=[need_owner])
    def rivals_list(min: int | None = None, tracked: int = 0):
        if min is not None and min not in rivals.MIN_GAMES_RANGE:
            raise HTTPException(400, BAD_RIVALS_MIN)
        if tracked not in (0, 1):
            raise HTTPException(400, "tracked must be 0 or 1")
        with ctx.db() as conn:
            n = min if min is not None else rivals.min_games(conn)
            return rivals.list(conn, ctx.me(conn), n, show_tracked=bool(tracked))

    @app.get("/api/rivals/{key}", dependencies=[need_owner])
    def rival_detail(key: str):
        with ctx.db() as conn:
            if not rivals.exists(conn, key):
                raise HTTPException(404, NO_RIVAL)
            d = rivals.detail(conn, ctx.me(conn), key)
            if not d["steam_ids"]:          # only my own accounts
                raise HTTPException(404, NO_RIVAL)
            return d

    @app.post("/api/settings/test-history", dependencies=[need_owner])
    def test_history(body: TestHistoryIn | None = None):
        body = body or TestHistoryIn()
        with ctx.db() as conn:
            key = (body.steam_api_key or "").strip() or db.get_setting(conn, "steam_api_key")
            auth = (body.match_auth_code or "").strip() or db.get_setting(conn, "match_auth_code")
            typed = (body.start_share_code or "").strip()
            if typed:
                try:
                    sharecode.decode(typed)
                except ValueError:
                    return {"ok": False, "message": BAD_SHARE_CODE}
            known = typed or steam_sync.start_code(conn)
            me = ctx.me(conn)
        if not (key and auth):
            return {"ok": False, "message": NEED_ACCESS}
        if not known:
            return {"ok": False, "message": NEED_CODE}
        msg = history.test_access(key, me, auth, known, opener=history_opener)
        if msg is None:
            return {"ok": True, "message": "It works — Update can fetch your full match history."}
        return {"ok": False, "message": msg}

    @app.get("/api/settings/detect-replays")
    def detect_replays():
        found = steam_paths.find_replays_dir()
        return {"replays_dir": str(found) if found is not None else None}

    @app.post("/api/update", dependencies=[need_owner])
    def update():
        def run(job: Job) -> None:
            def progress(stage: str, done: int, total: int) -> None:
                job.stage, job.done, job.total = stage, done, total
            with ctx.db() as conn:
                report = steam_sync.update(conn, ctx.job_settings(conn), progress, parse=ctx.parse,
                                           extract=ctx.extract)
            job.results = jsonable(report.imported)
            rep = jsonable(report)
            # the exact texts for the flags, so the page doesn't keep its own copies
            rep["cs2_running_message"] = steam_sync.MSG_CS2_RUNNING if report.cs2_running else None
            rep["low_disk_message"] = steam_sync.MSG_LOW_DISK if report.low_disk else None
            job.report = rep
        return {"job_id": ctx.runner.submit("update", run)}

    def import_job(paths: list[Path], idle_only: bool = False,
                   uploaded: bool = False) -> int | None:
        def run(job: Job) -> None:
            job.total = len(paths)

            def progress(done: int, total: int, res) -> None:
                job.done, job.total = done, total
                job.results.append(dataclasses.asdict(res))
            with ctx.db() as conn:
                # now=None: a user-chosen file needs no "still being written" wait
                results = importer.import_batch(conn, paths, ctx.me(conn), ctx.backups_dir,
                                                on_progress=progress, parse=ctx.parse, now=None,
                                                events_dir=ctx.events_dir, extract=ctx.extract)
            if uploaded:  # our own copy of an upload nobody needs: drop it
                for res in results:
                    if res.status in ("skipped", "failed"):
                        Path(res.path).unlink(missing_ok=True)
        if idle_only:
            return ctx.runner.submit_if_idle("import", run)
        return ctx.runner.submit("import", run)

    def confine(path: Path) -> Path:
        path = path.resolve()
        with ctx.db() as conn:
            replays = ctx.job_settings(conn)["replays_dir"]
        roots = [ctx.imported_dir.resolve()]
        if replays and replays.strip():  # a blank setting must not resolve to the cwd
            roots.append(Path(replays).resolve())
        if path.suffix.lower() not in ALLOWED_SUFFIXES or not any(
                path.is_relative_to(r) for r in roots):
            raise HTTPException(400, f"not an importable demo in a known folder: {path}")
        return path

    def checked_path(token: str) -> Path:
        return confine(decode_token(token))

    def unique_target(name: str) -> Path:
        dest = ctx.imported_dir / name
        n = 2
        while dest.exists():
            p = Path(name)
            dest = ctx.imported_dir / f"{p.stem} ({n}){p.suffix}"
            n += 1
        return dest

    @app.post("/api/import", dependencies=[need_owner])
    async def import_files(request: Request):
        if not request.headers.get("content-type", "").startswith("multipart/"):
            try:
                body = await request.json()
                paths = [Path(p) for p in body["paths"]]
            except (ValueError, KeyError, TypeError):
                raise HTTPException(422, 'expected multipart files or {"paths": [...]}') from None
            paths = [confine(p) for p in paths]
            job_id = import_job(paths, idle_only=True)
            if job_id is None:
                raise HTTPException(409, BUSY)
            return {"job_id": job_id}
        if ctx.runner.busy():
            raise HTTPException(409, BUSY)
        saved: list[Path] = []
        try:
            async with request.form() as form:
                uploads = []  # (form index, file, safe name); mtime_<index> is per file
                for i, f in enumerate(form.getlist("files")):
                    if not hasattr(f, "filename"):  # a plain form field, e.g. empty filename
                        raise HTTPException(400, "files must be uploaded as files")
                    name = PureWindowsPath(f.filename or "").name  # also splits "/" paths
                    if name.lower().endswith(".info"):  # Steam's sidecar files: not demos
                        continue
                    if name in ("", ".", "..") or Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
                        raise HTTPException(400, f"unsupported upload name: {f.filename!r}")
                    uploads.append((i, f, name))
                ctx.imported_dir.mkdir(parents=True, exist_ok=True)
                for i, f, name in uploads:
                    dest = unique_target(name)
                    saved.append(dest)
                    with open(dest, "wb") as out:
                        while chunk := await f.read(1 << 20):
                            out.write(chunk)
                    mt = upload_mtime(form.get(f"mtime_{i}"))
                    if mt is not None:  # the file's own date, not the upload time
                        os.utime(dest, (mt, mt))
            job_id = import_job(saved, idle_only=True, uploaded=True)
            if job_id is None:  # a job started between the check and now
                raise HTTPException(409, BUSY)
        except BaseException:
            for p in saved:
                p.unlink(missing_ok=True)
            raise
        return {"job_id": job_id}

    @app.post("/api/steam-history", dependencies=[need_owner])
    async def steam_history_import(request: Request):
        """Upload a Steam history export or saved page (field "file", at most 20 MB); it is
        parsed here (a bad file is a 400 at once) and imported as a job."""
        if ctx.runner.busy():
            raise HTTPException(409, BUSY)
        if not request.headers.get("content-type", "").startswith("multipart/"):
            raise HTTPException(400, "expected a multipart upload with a file")
        async with request.form() as form:
            f = form.get("file")
            if not hasattr(f, "read"):
                raise HTTPException(400, "file must be uploaded as a file")
            data = bytearray()
            while chunk := await f.read(1 << 20):
                data += chunk
                if len(data) > STEAM_HISTORY_MAX:
                    raise HTTPException(413, STEAM_HISTORY_TOO_BIG)
            name = f.filename or ""
        try:
            matches = await run_in_threadpool(steam_history.parse, bytes(data), name)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

        def run(job: Job) -> None:
            job.total = len(matches)

            def progress(done: int, total: int) -> None:
                job.done, job.total = done, total
            with ctx.db() as conn:
                rep = steam_history.import_matches(conn, matches, ctx.me(conn),
                                                   backup=ctx.backups_dir, progress=progress)
            job.report = {**dataclasses.asdict(rep), "message": steam_history.report_message(rep)}
        job_id = ctx.runner.submit_if_idle("steam_history", run)
        if job_id is None:  # a job started while the file was read
            raise HTTPException(409, BUSY)
        return {"job_id": job_id}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: int):
        job = ctx.runner.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        return job.to_json()

    @app.post("/api/import/{token}/attach/{match_id}", dependencies=[need_owner])
    def attach(token: str, match_id: int):
        path = checked_path(token)
        if ctx.runner.busy():
            raise HTTPException(409, BUSY)
        with ctx.db() as conn:
            return jsonable(importer.attach(conn, path, match_id, ctx.me(conn), parse=ctx.parse,
                                            events_dir=ctx.events_dir, extract=ctx.extract))

    @app.post("/api/import/{token}/new", dependencies=[need_owner])
    def add_new(token: str):
        path = checked_path(token)
        if ctx.runner.busy():
            raise HTTPException(409, BUSY)
        with ctx.db() as conn:
            return jsonable(importer.add_as_new(conn, path, ctx.me(conn), parse=ctx.parse,
                                                events_dir=ctx.events_dir, extract=ctx.extract))

    @app.delete("/api/matches/{match_id}")
    def delete_match(match_id: int):
        if ctx.runner.busy():
            raise HTTPException(409, BUSY)
        with ctx.db() as conn:
            if db.get_match(conn, match_id) is None:
                raise HTTPException(404, "no such match")
            db.backup(conn, ctx.backups_dir)
            db.delete_match(conn, match_id, ctx.events_dir)
        return {"deleted": match_id}

    @app.get("/api/about")
    def about():
        try:
            parser = f"demoparser2 {metadata.version('demoparser2')}"
        except metadata.PackageNotFoundError:
            parser = "demoparser2"
        with ctx.db() as conn:
            failed = _failed_reparse_ids(conn)
            # only what a re-read could fix, and not already failed:
            # - demo still on disk, and v1.1 stats missing or no / an older event bundle;
            # - an event bundle on disk (demo or not) without fun_stats rows at FUN_FORMAT.
            stale = any(
                mid not in failed and (
                    (needs_demo and path is not None and Path(path).is_file())
                    or (needs_fun and bundle_path(ctx.events_dir, mid).is_file()))
                for mid, path, needs_demo, needs_fun in conn.execute(
                    "SELECT m.id, m.demo_path, "
                    "m.demo_path IS NOT NULL AND (m.events_format IS NULL "
                    "  OR m.events_format < ? OR EXISTS ("
                    "  SELECT 1 FROM match_player mp WHERE mp.match_id=m.id "
                    "  AND mp.team IN ('mine','enemy') AND mp.utility_dmg IS NULL)), "
                    "m.events_format = ? AND NOT EXISTS ("
                    "  SELECT 1 FROM fun_stats f WHERE f.match_id=m.id AND f.fun_format=?) "
                    "FROM match m WHERE m.source IN ('demo','both')",
                    (EVENTS_FORMAT, EVENTS_FORMAT, FUN_FORMAT)))
            last_update = db.get_setting(conn, "last_update_at") or None
            auto = auto_update_on(conn)
        return {"app_version": APP_VERSION, "parser_version": parser,
                "data_dir": str(data_dir.resolve()), "reparse_suggested": stale,
                "last_update_at": last_update, "auto_update": auto,
                "job_running": ctx.runner.busy()}

    @app.post("/api/backup")
    def backup():
        with ctx.db() as conn:
            return {"path": str(db.backup(conn, ctx.backups_dir))}

    @app.post("/api/reparse", dependencies=[need_owner])
    def reparse_all():
        def run(job: Job) -> None:
            def progress(done: int, total: int, res: dict) -> None:
                job.done, job.total = done, total
                job.results.append(res)
            with ctx.db() as conn:
                results = reparse.reparse_all(conn, ctx.me(conn), ctx.backups_dir,
                                              parse=ctx.parse, on_progress=progress,
                                              events_dir=ctx.events_dir, extract=ctx.extract)
                # remember unparseable demos (and unreadable bundles) so they don't keep the
                # "re-read" notice on forever
                tried = {r["match_id"] for r in results if r["status"] != "missing"}
                failed = (_failed_reparse_ids(conn) - tried) | {
                    r["match_id"] for r in results if r["status"] == "failed" or (
                        r["status"] == "missing"
                        and bundle_path(ctx.events_dir, r["match_id"]).is_file())}
                db.set_setting(conn, "reparse_failed_ids", json.dumps(sorted(failed)))
        return {"job_id": ctx.runner.submit("reparse", run)}

    return app
