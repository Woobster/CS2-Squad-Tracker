"""The Update button backend: ask Steam for recent matches, download new demos, import."""
import bz2
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from cs2tracker import db, history, importer, retention, sharecode
from cs2tracker.demo import parse_demo
from cs2tracker.events import extract_events
from cs2tracker.importer import ImportResult
from cs2tracker.matchinfo import MatchInfo, parse_match_list

EXIT_MESSAGES = {
    1: "Something went wrong talking to Steam.",
    3: "Couldn't reach CS2's servers — try again in a minute.",
    4: "Close CS2 and press Update again.",
    5: "Restart Steam and press Update again.",
    6: "Start Steam and log in, then press Update.",
    7: "Steam is in offline mode — go online and press Update.",
    8: "Steam has no recent matches for this account.",
    9: "Couldn't write a temporary file.",
}
TIMEOUT_MESSAGE = "Steam didn't answer in time."
TIMEOUT_CODE = -1
CHUNK = 1 << 20
FETCH_LIMIT = 8  # boiler-writter returns at most the 8 most recent matches
MIN_FREE_BYTES = 1_000_000_000  # stop downloading under 1 GB free on the replays drive
MSG_CS2_RUNNING = "CS2 is running — demos will be fetched after you close it."
MSG_LOW_DISK = "Not enough disk space — free some space or set 'Delete demos after they're read'."
EXPIRED = "{what} {date}: demo no longer available on Valve's servers"
WAIT_NO_INFO = "Match {id}: couldn't get match info from Steam yet — will retry"
WAIT_NO_URL = "Match {id}: Steam has no demo link for it yet — will retry"
WAIT_STOPPED = ("Steam didn't answer for {n} matches in a row — {rest} more will be "
                "tried next Update")
SKIPPED = "Match {id}: Steam has no demo for this match — skipped"
MSG_NEED_CODE = ("Paste one match sharing code in Settings → Match history access to start "
                 "fetching your match history.")
MAX_FETCH_MISSES = 2  # consecutive "Steam unavailable" results before the queue waits
MAX_CODE_MISSES = 3  # Updates in which Steam answered without a demo before a code is dropped
# Settings keys of the chain. CHAIN_MARK is internal: the last_share_code value the
# app has already queued or reached itself, so a code pasted in Settings (stored as
# last_share_code) is queued exactly once.
PENDING = "pending_share_codes"
LAST_CODE = "last_share_code"
CHAIN_MARK = "chain_queued_code"
MISSES = "pending_misses"  # {code: Updates in which Steam answered without a demo}

# Why fetch_match got no usable match. Only STEAM_UNAVAILABLE stops the queue; the
# others mean Steam answered, so the code moves to the back of the queue instead.
OK = "ok"
STEAM_UNAVAILABLE = "steam_unavailable"
NOT_FOUND = "not_found"
NO_URL = "no_url"
NOT_FOUND_EXIT_CODES = {8}  # "no matches": Steam answered, but not with this match


class SteamSyncError(Exception):
    def __init__(self, code: int, message: str | None = None):
        self.code = code
        self.message = message or EXIT_MESSAGES.get(code, EXIT_MESSAGES[1])
        super().__init__(self.message)


@dataclass
class UpdateReport:
    steam_error: str | None = None
    scan_error: str | None = None
    scan_warnings: list[str] = field(default_factory=list)
    downloaded: list[str] = field(default_factory=list)
    download_errors: list[str] = field(default_factory=list)
    imported: list[ImportResult] = field(default_factory=list)
    gap_warning: bool = False
    history_error: str | None = None
    queued: int = 0
    expired: list[str] = field(default_factory=list)
    cs2_running: bool = False
    low_disk: bool = False
    waiting: list[str] = field(default_factory=list)
    freed_bytes: int = 0
    skipped: list[str] = field(default_factory=list)
    history_hint: str | None = None


def _run_boiler(boiler_cmd: list[str], out: Path, extra: list[str], timeout: int) -> bytes:
    """Run boiler-writter writing to `out`; return what it wrote. Raises SteamSyncError."""
    out.unlink(missing_ok=True)
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    # The Steam API reads steam_appid.txt from the working directory: run next to the exe,
    # and name the app in the environment as well.
    cwd = Path(boiler_cmd[0]).resolve().parent
    env = {**os.environ, "SteamAppId": "730", "SteamGameId": "730"}
    try:
        try:
            proc = subprocess.run([*boiler_cmd, str(out), *extra], timeout=timeout, cwd=cwd,
                                  env=env, **kwargs)
        except subprocess.TimeoutExpired:
            raise SteamSyncError(TIMEOUT_CODE, TIMEOUT_MESSAGE) from None
        except OSError as e:
            raise SteamSyncError(1) from e
        if proc.returncode != 0:
            raise SteamSyncError(proc.returncode)
        if not out.is_file():
            raise SteamSyncError(1)
        try:
            return out.read_bytes()
        except OSError as e:
            raise SteamSyncError(1) from e
    finally:
        out.unlink(missing_ok=True)


def _tmp(tmp_dir: Path) -> Path:
    tmp_dir = Path(tmp_dir).resolve()  # absolute: the child runs in another folder
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir


def fetch_match_list(boiler_cmd: list[str], tmp_dir: Path, timeout: int = 90) -> list[MatchInfo]:
    data = _run_boiler(boiler_cmd, _tmp(tmp_dir) / "boiler-matches.bin", [], timeout)
    try:
        return parse_match_list(data)
    except Exception as e:
        raise SteamSyncError(1) from e


@dataclass
class FetchResult:
    """fetch_match's answer: the match (reason OK or NO_URL) or why there is none."""
    info: MatchInfo | None
    reason: str


def fetch_match(boiler_cmd: list[str], tmp_dir: Path, match_id: int, outcome_id: int,
                tv_port: int, timeout: int = 90) -> FetchResult:
    """One match by the values in its sharing code (`boiler-writter <out> <matchId>
    <outcomeId> <token>`; upstream's token is the TV port).

    STEAM_UNAVAILABLE: timeout, couldn't run, or an exit code other than "no matches"
    (Steam not running, logged out, offline, CS2 open, ...): try again later.
    NOT_FOUND: Steam answered (exit 0 or "no matches") without this match.
    NO_URL: the match, but without a demo link (info is set)."""
    out = _tmp(tmp_dir) / f"boiler-match-{int(match_id)}.bin"
    try:
        data = _run_boiler(boiler_cmd, out, [str(int(match_id)), str(int(outcome_id)),
                                             str(int(tv_port))], timeout)
    except SteamSyncError as e:
        return FetchResult(None, NOT_FOUND if e.code in NOT_FOUND_EXIT_CODES else STEAM_UNAVAILABLE)
    except Exception:
        return FetchResult(None, STEAM_UNAVAILABLE)
    try:
        matches = parse_match_list(data) if data else []
    except Exception:  # an unreadable answer: nothing usable for this code
        return FetchResult(None, NOT_FOUND)
    info = next((m for m in matches if m.match_id == match_id), None)
    if info is None:
        return FetchResult(None, NOT_FOUND)
    return FetchResult(info, OK if info.demo_url else NO_URL)


TASKLIST = ["tasklist", "/FI", "IMAGENAME eq cs2.exe", "/NH"]


def _tasklist_has_cs2(run=subprocess.run) -> bool:
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:  # localized Windows may print bytes outside the default code page
        proc = run(TASKLIST, capture_output=True, text=True, errors="replace", timeout=15,
                   **kwargs)
        return "cs2.exe" in (proc.stdout or "").lower()
    except Exception:
        return False


def cs2_running() -> bool:
    """Is CS2 open? (boiler-writter starts the Steam API as app 730, so it must wait.)"""
    return os.name == "nt" and _tasklist_has_cs2()


def _target_name(info: MatchInfo) -> str:
    if info.demo_url is None:
        raise ValueError("match has no demo URL")
    url = urlparse(info.demo_url)
    base = url.path.rsplit("/", 1)[-1]
    base = base[: -len(".dem.bz2")] if base.endswith(".dem.bz2") else base
    if not re.fullmatch(r"[\w.-]+", base) or base.strip(".") == "":
        raise ValueError(f"unsafe demo file name in URL: {base!r}")
    m = re.fullmatch(r"replay(\d+)\.valve\.net", url.hostname or "")
    return f"match730_{base}_{m.group(1) if m else 0}.dem"


def download_demo(info: MatchInfo, replays_dir: Path, opener=urllib.request.urlopen) -> Path:
    if info.demo_url is None:
        raise ValueError("match has no demo URL")
    final = Path(replays_dir) / _target_name(info)
    if final.parent != Path(replays_dir):
        raise ValueError("demo path escapes the replays folder")
    part = final.with_name(final.name + ".part")
    try:
        dec = bz2.BZ2Decompressor()
        with opener(info.demo_url, timeout=60) as src, open(part, "wb") as dst:
            while True:
                chunk = src.read(CHUNK)
                if not chunk:
                    break
                if not dec.eof:
                    dst.write(dec.decompress(chunk))
            if not dec.eof:
                raise EOFError("download ended before the end of the compressed stream")
        os.replace(part, final)
        final.with_name(final.name + ".info").write_bytes(info.raw)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return final


def gap_warning(conn: sqlite3.Connection, fetched: list[MatchInfo]) -> bool:
    """All 8 fetched matches are new and the newest stored match predates them all:
    matches between were probably missed."""
    if len(fetched) < FETCH_LIMIT:
        return False
    if any(db.find_match_by_valve_id(conn, m.match_id) is not None for m in fetched):
        return False
    row = conn.execute("SELECT MAX(started_at) FROM match").fetchone()
    newest = db._parse_dt(row[0]) if row else None
    if newest is None:
        return False
    return newest < min(m.started_at for m in fetched)


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def load_pending(conn: sqlite3.Connection) -> list[str]:
    try:
        codes = json.loads(db.get_setting(conn, PENDING, "[]") or "[]")
    except ValueError:
        return []
    return [c for c in codes if isinstance(c, str)] if isinstance(codes, list) else []


def _save_pending(conn: sqlite3.Connection, codes: list[str]) -> None:
    db.set_setting(conn, PENDING, json.dumps(codes))


def load_misses(conn: sqlite3.Connection) -> dict[str, int]:
    try:
        data = json.loads(db.get_setting(conn, MISSES, "{}") or "{}")
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, int)
            and not isinstance(v, bool)}


def _save_misses(conn: sqlite3.Connection, misses: dict[str, int]) -> None:
    db.set_setting(conn, MISSES, json.dumps(misses))


def _newest_info_code(conn: sqlite3.Connection) -> str | None:
    for (raw,) in conn.execute("SELECT info_raw FROM match WHERE info_raw IS NOT NULL "
                               "ORDER BY started_at DESC"):
        code = sharecode.from_info(bytes(raw))
        if code:
            return code
    return None


def _valve_id(code: str) -> int | None:
    try:
        return sharecode.decode(code)[0]
    except ValueError:
        return None


def _pasted(conn: sqlite3.Connection, last: str) -> bool:
    """last_share_code was pasted in Settings and no walk has started from it yet."""
    return bool(last) and last != db.get_setting(conn, CHAIN_MARK)


def start_code(conn: sqlite3.Connection) -> str | None:
    """Where the next walk starts. After a walk: last_share_code. Before the first walk
    from a pasted code: the pasted code or the code of the newest match the app has a
    .dem.info for, whichever is the older match (Valve match ids grow over time), so
    matches between them aren't passed over. Without a pasted code: the .info code."""
    last = (db.get_setting(conn, LAST_CODE) or "").strip()
    if last and not _pasted(conn, last):
        return last
    derived = _newest_info_code(conn)
    if not last:
        return derived
    if derived is None:
        return last
    last_id, derived_id = _valve_id(last), _valve_id(derived)
    if last_id is None or (derived_id is not None and derived_id < last_id):
        return derived
    return last


def _walk_history(conn: sqlite3.Connection, settings: dict, report: UpdateReport,
                  progress, opener, sleep) -> None:
    """Step 1: queue the codes after the last known one. The pending list is saved
    before last_share_code moves, so no code is ever passed over unqueued."""
    key, auth = settings.get("steam_api_key"), settings.get("match_auth_code")
    if not (key and auth):
        return
    start = start_code(conn)
    if not start:
        report.history_hint = MSG_NEED_CODE
        return
    pend = load_pending(conn)
    last = (db.get_setting(conn, LAST_CODE) or "").strip()
    if _pasted(conn, last):  # pasted in Settings: import it too
        if last not in pend:
            pend.append(last)
            _save_pending(conn, pend)
        if start != last:  # walking from an older match: continue from there next time
            db.set_setting(conn, LAST_CODE, start)
            last = start
        db.set_setting(conn, CHAIN_MARK, last)
    progress("history", 0, 1)
    codes, err = history.next_codes(key, settings["me_steam_id"], auth, start,
                                    opener=opener, sleep=sleep)
    progress("history", 1, 1)
    if err is not None:
        report.history_error = err.message
    if codes:
        pend += [c for c in codes if c not in pend]
        _save_pending(conn, pend)
        db.set_setting(conn, LAST_CODE, codes[-1])
        db.set_setting(conn, CHAIN_MARK, codes[-1])


def _enough_space(free_bytes, replays: Path) -> bool:
    try:
        return free_bytes(replays) >= MIN_FREE_BYTES
    except OSError:  # unknown: try; a full disk fails the download (queued codes stay)
        return True


def _expired_message(info: MatchInfo) -> str:
    what = getattr(info, "map", None) or f"Match {info.match_id}"
    return EXPIRED.format(what=what, date=info.started_at.strftime("%Y-%m-%d"))


def _work_queue(conn: sqlite3.Connection, settings: dict, report: UpdateReport, progress,
                import_one, after_import, *, opener, fetch_one, free_bytes,
                downloaded: list[Path]) -> None:
    """Step 2: each pending code in order, one match at a time: fetch its info,
    download, import, clean up, and only then drop it from the queue."""
    replays = Path(settings["replays_dir"])
    pend = load_pending(conn)
    total = len(pend)
    if not pend:
        if load_misses(conn):
            _save_misses(conn, {})
        return
    code_misses = load_misses(conn)
    unavailable = 0
    snapshot = list(pend)
    for i, code in enumerate(snapshot):
        progress("fetch", i, total)
        try:
            match_id, outcome_id, tv_port = sharecode.decode(code)
        except ValueError:  # not a code at all: nothing to fetch
            pend.remove(code)
            _save_pending(conn, pend)
            continue
        if db.demo_match_by_valve_id(conn, match_id) is not None:  # a Steam row doesn't count
            pend.remove(code)
            _save_pending(conn, pend)
            continue
        if not _enough_space(free_bytes, replays):
            report.low_disk = True
            break
        res = fetch_one(settings["boiler_cmd"], Path(settings["tmp_dir"]), match_id,
                        outcome_id, tv_port)
        if res.reason == STEAM_UNAVAILABLE:  # couldn't ask Steam: stays where it is
            report.waiting.append(WAIT_NO_INFO.format(id=match_id))
            unavailable += 1
            if unavailable >= MAX_FETCH_MISSES:  # don't spend up to 90 s on each of the rest
                rest = len(snapshot) - i - 1
                if rest:
                    report.waiting.append(WAIT_STOPPED.format(n=unavailable, rest=rest))
                break
            continue
        unavailable = 0
        info = res.info
        if res.reason != OK or info is None or not info.demo_url:
            # Steam answered without a demo: to the back of the queue, so it can't hold
            # up the codes behind it; dropped after MAX_CODE_MISSES Updates like this.
            n = code_misses.get(code, 0) + 1
            pend.remove(code)
            if n >= MAX_CODE_MISSES:
                code_misses.pop(code, None)
                report.skipped.append(SKIPPED.format(id=match_id))
            else:
                code_misses[code] = n
                pend.append(code)
                report.waiting.append((WAIT_NO_URL if res.reason == NO_URL else WAIT_NO_INFO)
                                      .format(id=match_id))
            _save_pending(conn, pend)
            _save_misses(conn, code_misses)
            continue
        try:
            target = replays / _target_name(info)
            info_path = target.with_name(target.name + ".info")
            if target.exists() and info_path.exists():  # already here (earlier run)
                path = target
            else:
                path = download_demo(info, replays, opener=opener)
        except urllib.error.HTTPError as e:
            code_no = e.code
            e.close()
            if code_no == 404:
                report.expired.append(_expired_message(info))
                pend.remove(code)
                _save_pending(conn, pend)
            else:
                report.download_errors.append(f"{info.match_id}: HTTP {code_no}")
            continue
        except Exception as e:
            report.download_errors.append(f"{info.match_id}: {e}")
            continue
        downloaded.append(path)
        imp = import_one(path)
        if imp.match_id is not None and imp.status in ("added", "attached"):
            after_import(imp.match_id)
        # imported, or on disk for the folder scan to retry: either way it's not lost
        pend.remove(code)
        _save_pending(conn, pend)
    kept = {c: n for c, n in code_misses.items() if c in pend}
    if kept != load_misses(conn):
        _save_misses(conn, kept)
    progress("fetch", total, total)


def update(conn: sqlite3.Connection, settings: dict, on_progress: Callable[[str, int, int], None] | None = None,
           *, opener=urllib.request.urlopen, parse=parse_demo, extract=extract_events,
           fetch_one=fetch_match, fetch_list=None, cs2_running=cs2_running,
           free_bytes=_free_bytes, sleep=time.sleep, now=_utcnow) -> UpdateReport:
    """History chain → pending queue (one match at a time) → last-8 fetch and folder
    scan → demo retention. Everything outside the process is injectable for tests."""
    def progress(stage: str, done: int, total: int) -> None:
        if on_progress:
            on_progress(stage, done, total)

    fetch_list = fetch_list or fetch_match_list
    report = UpdateReport()
    replays = Path(settings["replays_dir"])
    me = settings["me_steam_id"]
    events_dir = Path(settings["events_dir"]) if settings.get("events_dir") else None
    days = retention.parse_days(settings.get("demo_retention_days"))
    allowed = [replays] + ([Path(settings["imported_dir"])] if settings.get("imported_dir") else [])
    downloaded: list[Path] = []

    def clean(match_ids: list[int] | None) -> None:
        if days is None:
            return
        _, freed = retention.delete_old_demos(conn, days, events_dir, allowed, now=now(),
                                              match_ids=match_ids)
        report.freed_bytes += freed

    def finish() -> UpdateReport:
        report.queued = len(load_pending(conn))
        db.set_setting(conn, "last_update_at", db._utc_iso(now()))
        return report

    _walk_history(conn, settings, report, progress, opener, sleep)

    if not replays.is_dir():  # nothing to download into or scan
        report.scan_error = f"Replays folder not found: {replays}. Set it in Settings."
        return finish()

    backed_up = False

    def backup_once() -> None:
        nonlocal backed_up
        if not backed_up:
            db.backup(conn, Path(settings["backups_dir"]))
            backed_up = True

    def import_one(path: Path) -> ImportResult:
        progress("import", 0, 1)
        res = importer.import_file(conn, path, me, parse=parse, now=None,
                                   before_write=backup_once, events_dir=events_dir,
                                   extract=extract)
        report.imported.append(res)
        progress("import", 1, 1)
        return res

    report.cs2_running = bool(cs2_running())
    boiler = settings.get("boiler_cmd") if not report.cs2_running else None
    if boiler:
        _work_queue(conn, settings, report, progress, import_one, lambda mid: clean([mid]),
                    opener=opener, fetch_one=fetch_one, free_bytes=free_bytes,
                    downloaded=downloaded)
    chained = {p.resolve() for p in downloaded}

    if boiler:
        progress("steam", 0, 1)
        try:
            fetched = fetch_list(boiler, Path(settings["tmp_dir"]))
        except SteamSyncError as e:
            report.steam_error = e.message
            fetched = []
        progress("steam", 1, 1)
        report.gap_warning = gap_warning(conn, fetched)
        todo = []
        for m in fetched:
            if not m.demo_url or db.demo_match_by_valve_id(conn, m.match_id) is not None:
                continue
            try:
                target = replays / _target_name(m)
                if target.exists():
                    if target.resolve() in chained:
                        continue
                    info_path = target.with_name(target.name + ".info")
                    if not info_path.exists():  # earlier .info write failed: repair, no re-download
                        info_path.write_bytes(m.raw)
                        downloaded.append(target)
                    continue
            except Exception as e:
                report.download_errors.append(f"{m.match_id}: {e}")
                continue
            todo.append(m)
        for i, m in enumerate(todo, 1):
            progress("download", i - 1, len(todo))
            if not _enough_space(free_bytes, replays):
                report.low_disk = True
                break
            try:
                downloaded.append(download_demo(m, replays, opener=opener))
            except Exception as e:
                report.download_errors.append(f"{m.match_id}: {e}")
            progress("download", i, len(todo))
    report.downloaded = [str(p) for p in downloaded]

    # Downloaded demos are complete: skip the not-ready check. Folder files keep it.
    fresh = {p.resolve() for p in downloaded}
    last8 = [p for p in downloaded if p.resolve() not in chained]
    scanned = [p for p in importer.scan_folder(conn, replays, report.scan_warnings)
               if p.resolve() not in fresh]
    total = len(last8) + len(scanned)
    base = 0

    def batch(paths: list[Path], now_fn) -> None:
        nonlocal base
        if not paths:
            return
        offset = base
        report.imported += importer.import_batch(
            conn, paths, me, Path(settings["backups_dir"]),
            on_progress=lambda done, _t, _r: progress("import", offset + done, total),
            parse=parse, now=now_fn, events_dir=events_dir, extract=extract)
        base += len(paths)

    progress("import", 0, total)
    batch(last8, None)
    batch(scanned, time.time)
    clean(None)
    return finish()
