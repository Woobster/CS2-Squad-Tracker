"""Delete demos the app has fully read (spec §4), and count what's on disk.

A demo is deleted only when its match's event bundle is in the current format,
reads back as this match and the stats can be computed from it, the match started more than `days` ago, and the file is
the match's recorded demo_path inside one of the allowed folders (the replays
folder and the app's own imported folder). Nothing else is ever touched.
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cs2tracker import db, importer
from cs2tracker.demo import stats_from_bundle
from cs2tracker.events import EVENTS_FORMAT, read_bundle
from cs2tracker.reparse import bundle_expect

RETENTION_DAYS = (7, 30, 90)


def parse_days(value) -> int | None:
    """The demo_retention_days setting as an allowed number of days, else None (never)."""
    try:
        days = int(value)
    except (TypeError, ValueError):
        return None
    return days if days in RETENTION_DAYS else None


def _inside(path: Path, roots: list[Path]) -> bool:
    return any(path.is_relative_to(r) and path != r for r in roots)


def delete_old_demos(conn: sqlite3.Connection, days: int | None, events_dir, allowed_dirs: list[Path],
                     now: datetime | None = None,
                     match_ids: list[int] | None = None) -> tuple[int, int]:
    """(files_deleted, bytes_freed). match_ids=None checks every match."""
    if days is None or events_dir is None or (match_ids is not None and not match_ids):
        return 0, 0
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    roots = []
    for d in allowed_dirs:
        try:
            roots.append(Path(d).resolve())
        except OSError:
            continue
    sql = ("SELECT id, demo_path, started_at FROM match "
           "WHERE demo_path IS NOT NULL AND events_format=?")
    args: list = [EVENTS_FORMAT]
    if match_ids is not None:
        sql += f" AND id IN ({','.join('?' * len(match_ids))})"
        args += [int(m) for m in match_ids]
    files = freed = 0
    for mid, demo_path, started_at in conn.execute(sql, args).fetchall():
        started = db._parse_dt(started_at)
        if started is None or started >= cutoff:
            continue
        try:
            path = Path(demo_path).resolve()
            if not _inside(path, roots) or not path.is_file():
                continue
        except OSError:
            continue
        bundle = read_bundle(Path(events_dir), mid, bundle_expect(conn, mid))
        if bundle is None:
            continue
        try:  # the stats must come back from the bundle as read from disk
            stats_from_bundle(bundle)
        except Exception:
            continue
        info = importer._info_path(path)
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:  # locked or gone: keep the row as it is
            continue
        files += 1
        freed += size
        try:
            if info.is_file():
                isize = info.stat().st_size
                info.unlink()
                files += 1
                freed += isize
        except OSError:
            pass
        conn.execute("UPDATE match SET demo_path=NULL WHERE id=?", (mid,))
        conn.commit()
    return files, freed


def disk_summary(conn: sqlite3.Connection, events_dir) -> dict:
    """{demos, demo_bytes, bundles, bundle_bytes}: demo files the matches point at
    that exist, and bundle files in the events folder."""
    demos = demo_bytes = 0
    for (p,) in conn.execute("SELECT DISTINCT demo_path FROM match WHERE demo_path IS NOT NULL"):
        try:
            st = Path(p).stat()
        except OSError:
            continue
        demos += 1
        demo_bytes += st.st_size
    bundles = bundle_bytes = 0
    if events_dir is not None and Path(events_dir).is_dir():
        for f in Path(events_dir).glob("*.json.gz"):
            try:
                bundle_bytes += f.stat().st_size
                bundles += 1
            except OSError:
                continue
    return {"demos": demos, "demo_bytes": demo_bytes, "bundles": bundles,
            "bundle_bytes": bundle_bytes}
