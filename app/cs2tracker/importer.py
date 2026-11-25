"""Import demo files into the database, applying the duplicate rules (spec 6.3)."""
import bz2
import gzip
import hashlib
import shutil
import sqlite3
import tempfile
import time
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Literal

from cs2tracker import db, funstats, records
from cs2tracker.demo import DemoParseError, ParsedDemo, parse_demo, stats_from_bundle
from cs2tracker.events import (
    EVENTS_FORMAT,
    EventBundle,
    bundle_path,
    extract_events,
    write_bundle,
)
from cs2tracker.matchinfo import MatchInfo, parse_match_info

Status = Literal["added", "attached", "skipped", "possible_duplicate", "failed", "not_ready"]
READY_AFTER_SECONDS = 10
COMPRESSED = (".zip", ".gz", ".bz2")


@dataclass
class ImportResult:
    path: str
    status: Status
    match_id: int | None
    reason: str
    candidates: list[int] = field(default_factory=list)


def _info_path(path: Path) -> Path:
    base = path.with_suffix("") if path.suffix.lower() in COMPRESSED else path
    return base.with_name(base.name + ".info")


def _read_info(path: Path) -> MatchInfo | None:
    ip = _info_path(path)
    if not ip.is_file():
        return None
    try:
        return parse_match_info(ip.read_bytes())
    except Exception:  # corrupt .info: fall back to estimating from the file
        return None


@contextmanager
def as_dem(path: Path) -> Iterator[Path]:
    """Yield a path to a plain .dem, decompressing into a temp dir when needed."""
    suffix = path.suffix.lower()
    if suffix not in COMPRESSED:
        yield path
        return
    with tempfile.TemporaryDirectory(prefix="cs2tracker-") as tmp:
        out = Path(tmp) / (path.stem if path.stem.lower().endswith(".dem") else path.stem + ".dem")
        if suffix == ".zip":
            with zipfile.ZipFile(path) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".dem")]
                if not names:
                    raise DemoParseError("zip contains no .dem file")
                with z.open(names[0]) as src, open(out, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        else:
            opener = gzip.open if suffix == ".gz" else bz2.open
            try:
                with opener(path, "rb") as src, open(out, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            except (OSError, EOFError) as e:
                raise DemoParseError(f"cannot decompress: {e}") from e
        yield out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _my_numbers(demo: ParsedDemo, me: str):
    """((my score, other), my_kills, my_deaths) or (score_a/b, None, None) if I'm absent."""
    mine = next((p for p in demo.players if p.steam_id == me), None)
    if mine is None:
        return (demo.score_a, demo.score_b), None, None, False
    score = (demo.score_a, demo.score_b) if mine.team == "A" else (demo.score_b, demo.score_a)
    return score, mine.kills, mine.deaths, True


def write_demo_rows(conn, match_id: int, demo: ParsedDemo, me: str) -> None:
    db.replace_match_players(conn, match_id, demo.players, me, commit=False)
    db.insert_kills(conn, match_id, demo.kills, commit=False)
    db.insert_rounds(conn, match_id, demo.rounds, commit=False)
    db.insert_multikills(conn, match_id, demo.multikills, commit=False)


def _add(conn, path: Path, sha: str, demo: ParsedDemo, info: MatchInfo | None, me: str,
         started: datetime) -> int:
    score, k, d, found = _my_numbers(demo, me)
    result = None
    if found:
        result = "win" if score[0] > score[1] else "loss" if score[0] < score[1] else "tie"
    mid = db.insert_match(conn, db.NewMatch(
        source="demo", valve_match_id=info.match_id if info else None, demo_sha256=sha,
        demo_path=str(path), map=demo.map, started_at=started, start_estimated=info is None,
        my_team_score=score[0], enemy_score=score[1], result=result, server=demo.server,
        my_kills=k, my_deaths=d, info_raw=info.raw if info else None), commit=False)
    write_demo_rows(conn, mid, demo, me)
    return mid


SUMMARY_SOURCES = ("leetify", "steam")
KEPT_FROM_SUMMARY = ("leetify_rating", "hltv_rating", "mvps", "hs_percent", "scoreboard_score")


def keep_summary_fields(conn, match_id: int) -> Callable[[], None]:
    """Remember the match's summary-only per-player numbers (KEPT_FROM_SUMMARY); the returned
    function puts them back after the demo rows have been rewritten. No commit."""
    kept = {r[0]: r[1:] for r in conn.execute(
        f"SELECT steam_id, {', '.join(KEPT_FROM_SUMMARY)} FROM match_player WHERE match_id=?",
        (match_id,))}
    assign = ", ".join(f"{c}=?" for c in KEPT_FROM_SUMMARY)

    def restore() -> None:
        for sid, vals in kept.items():
            if any(v is not None for v in vals):
                conn.execute(f"UPDATE match_player SET {assign} WHERE match_id=? AND steam_id=?",
                             (*vals, match_id, sid))
    return restore


def _valve_id(conn, match_id: int) -> int | None:
    return conn.execute("SELECT valve_match_id FROM match WHERE id=?", (match_id,)).fetchone()[0]


def _attach(conn, summary_id: int, path: Path, sha: str, demo: ParsedDemo,
            info: MatchInfo | None, me: str) -> int:
    """Attach a demo to a summary row (source leetify or steam, no demo yet): source becomes
    'both', the demo's stats replace the per-player rows, and the summary's own per-player
    numbers (Leetify/HLTV ratings, Steam scoreboard mvps/hs_percent/score) are kept.

    A Steam row's score may be stored the other way round (it matched in either
    orientation), so when I'm in the demo the demo's score, result and my K/D win."""
    row = conn.execute("SELECT source, demo_path, demo_sha256 FROM match WHERE id=?",
                       (summary_id,)).fetchone()
    if row is None or row[0] not in SUMMARY_SOURCES or row[1] is not None or row[2] is not None:
        raise ValueError(f"match {summary_id} is not a summary-only (leetify/steam) match")
    source = row[0]
    restore = keep_summary_fields(conn, summary_id)
    sets = ["source='both'", "demo_sha256=?", "demo_path=?", "map=?", "server=?"]
    args: list = [sha, str(path), demo.map, demo.server]
    if info is not None:
        sets += ["valve_match_id=?", "info_raw=?", "started_at=?", "start_estimated=0"]
        args += [info.match_id, info.raw, db._utc_iso(info.started_at)]
    score, k, d, found = _my_numbers(demo, me)
    if source == "steam" and found:
        result = "win" if score[0] > score[1] else "loss" if score[0] < score[1] else "tie"
        sets += ["my_team_score=?", "enemy_score=?", "result=?", "my_kills=?", "my_deaths=?"]
        args += [score[0], score[1], result, k, d]
    conn.execute(f"UPDATE match SET {', '.join(sets)} WHERE id=?", (*args, summary_id))
    write_demo_rows(conn, summary_id, demo, me)
    restore()
    return summary_id


def store_bundle(conn, match_id: int, bundle: EventBundle, events_dir: Path) -> None:
    """Write the match's event bundle, set match.events_format and replace its fun_stats
    rows (computed from the bundle); no commit, so all of it shares the caller's transaction.

    The header's valve_match_id is taken from the match row, so the bundle always
    matches what read_bundle will be asked for (an attach may keep Leetify's id)."""
    # Database first, file last: if anything before write_bundle raises, the caller's
    # rollback leaves no new bundle file behind (and an old one untouched).
    funstats.store(conn, match_id, funstats.compute(bundle))
    row = conn.execute("SELECT valve_match_id FROM match WHERE id=?", (match_id,)).fetchone()
    bundle.header["valve_match_id"] = None if row is None or row[0] is None else str(row[0])
    conn.execute("UPDATE match SET events_format=? WHERE id=?", (EVENTS_FORMAT, match_id))
    write_bundle(bundle, events_dir, match_id)


def uses_bundles(parse, events_dir: Path | None) -> bool:
    """Real imports extract a bundle; an injected parse= (a test fake) skips bundles."""
    return events_dir is not None and parse is parse_demo


def _run(conn: sqlite3.Connection, path: Path, me: str, parse, now,
         force: int | Literal["new"] | None = None,
         before_write: Callable[[], None] | None = None,
         events_dir: Path | None = None, extract=extract_events) -> ImportResult:
    path = Path(path)
    sp = str(path)
    try:
        mtime = path.stat().st_mtime
        if now is not None and now() - mtime < READY_AFTER_SECONDS:
            return ImportResult(sp, "not_ready", None, "file was modified in the last 10 seconds")
        info = _read_info(path)
        by_valve: int | None = None   # a summary row with this demo's Valve id: attach to it
        if info is not None:
            known = db.find_match_by_valve_id(conn, info.match_id)
            if known is not None:
                by_valve = db.summary_match_by_valve_id(conn, info.match_id)
                if by_valve is None:
                    return ImportResult(sp, "skipped", known, "match already imported")
        with as_dem(path) as dem:
            sha = _sha256(dem)
            known = db.find_match_by_sha(conn, sha)
            if known is not None:
                return ImportResult(sp, "skipped", known, "demo already imported")
            bundle: EventBundle | None = None
            if uses_bundles(parse, events_dir):
                bundle = extract(dem, valve_match_id=str(info.match_id) if info else None)
                demo = stats_from_bundle(bundle)
            else:
                demo = parse(dem)
        mtime_dt = datetime.fromtimestamp(mtime, tz=timezone.utc).replace(microsecond=0)
        started = info.started_at if info else mtime_dt
        score, k, d, found = _my_numbers(demo, me)

        target: int | None = None
        if isinstance(force, int):
            target = force
        elif by_valve is not None:  # "Add as new" too: the Valve id can only be on one row
            target = by_valve
        elif force is None and found:
            if info is not None:
                cands = [c for c in db.summary_match_candidates(conn, info.started_at, score)
                         if _valve_id(conn, c) in (None, info.match_id)]  # other id: other match
                if not cands:  # sheet rows without a start time sit at 00:00 (R15: any map)
                    cands = db.leetify_candidates(conn, None, score, k, d, info.started_at,
                                                  days=1, ignore_map=True, me=me)
            else:
                cands = db.leetify_candidates(conn, demo.map, score, k, d, mtime_dt, me=me,
                                              sources=SUMMARY_SOURCES)
            if len(cands) > 1:
                return ImportResult(sp, "possible_duplicate", None,
                                    "several summary matches fit this demo", cands)
            if cands:
                target = cands[0]

        if before_write is not None:
            before_write()
        mid = None
        target_source = None
        if target is not None:
            r = conn.execute("SELECT source FROM match WHERE id=?", (target,)).fetchone()
            target_source = r[0] if r else None
        try:
            if target is not None:
                mid = _attach(conn, target, path, sha, demo, info, me)
            else:
                mid = _add(conn, path, sha, demo, info, me, started)
            if bundle is not None:
                store_bundle(conn, mid, bundle, events_dir)
            records.rebuild_for(conn, [mid])  # after the rows and fun stats, same transaction
            conn.commit()
        except BaseException:
            conn.rollback()
            if bundle is not None and mid is not None:  # no row points at it any more
                bundle_path(events_dir, mid).unlink(missing_ok=True)
            raise
        if target is not None:
            return ImportResult(sp, "attached", mid, f"attached to {target_source} match")
        return ImportResult(sp, "added", mid, "")
    except DemoParseError as e:
        return ImportResult(sp, "failed", None, str(e))
    except Exception as e:
        return ImportResult(sp, "failed", None, f"{type(e).__name__}: {e}")


def import_file(conn: sqlite3.Connection, path: Path, me: str, parse=parse_demo,
                now=time.time, before_write: Callable[[], None] | None = None,
                events_dir: Path | None = None, extract=extract_events) -> ImportResult:
    return _run(conn, path, me, parse, now, before_write=before_write, events_dir=events_dir,
                extract=extract)


def import_batch(conn: sqlite3.Connection, paths: list[Path], me: str, backups_dir: Path,
                 on_progress: Callable[[int, int, ImportResult], None] | None = None,
                 parse=parse_demo, now=time.time, events_dir: Path | None = None,
                 extract=extract_events) -> list[ImportResult]:
    done = False

    def backup_once() -> None:
        nonlocal done
        if not done:
            db.backup(conn, backups_dir)
            done = True

    results = []
    for i, p in enumerate(paths, 1):
        r = import_file(conn, p, me, parse=parse, now=now, before_write=backup_once,
                        events_dir=events_dir, extract=extract)
        results.append(r)
        if on_progress:
            on_progress(i, len(paths), r)
    return results


def scan_folder(conn: sqlite3.Connection, folder: Path,
                warnings: list[str] | None = None) -> list[Path]:
    """New .dem files in folder. A file that can't be read is skipped, and
    "Couldn't read <name>" is appended to warnings when a list is given."""
    out = []
    for p in sorted(Path(folder).glob("*.dem")):
        try:
            if not p.is_file():
                continue
            info = _read_info(p)
            if info is not None and db.demo_match_by_valve_id(conn, info.match_id) is not None:
                continue
            if db.find_match_by_sha(conn, _sha256(p)) is not None:
                continue
        except OSError:
            if warnings is not None:
                warnings.append(f"Couldn't read {p.name}")
            continue
        out.append(p)
    return out


def attach(conn: sqlite3.Connection, demo_path: Path, leetify_match_id: int, me: str,
           parse=parse_demo, events_dir: Path | None = None,
           extract=extract_events) -> ImportResult:
    """Attach a demo to a chosen summary match (source leetify or steam, no demo yet)."""
    return _run(conn, demo_path, me, parse, None, force=leetify_match_id, events_dir=events_dir,
                extract=extract)


def add_as_new(conn: sqlite3.Connection, demo_path: Path, me: str, parse=parse_demo,
               events_dir: Path | None = None, extract=extract_events) -> ImportResult:
    return _run(conn, demo_path, me, parse, None, force="new", events_dir=events_dir,
                extract=extract)
