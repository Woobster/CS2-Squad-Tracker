"""Re-read stored matches, replacing parsed rows but keeping match ids.

With the demo on disk, it is extracted again and its event bundle rewritten (so an
older bundle format is upgraded). Without it, a bundle that matches the match row is
read instead (status "from-events"). With neither, the match is reported "missing".
Either way the match's fun_stats rows are recomputed from the bundle, and the records of its
tracked players (records.rebuild_for), in the same transaction.
"""
from pathlib import Path
from typing import Callable

from cs2tracker import db, funstats, importer, records
from cs2tracker.demo import ParsedDemo, parse_demo, stats_from_bundle
from cs2tracker.events import EventBundle, bundle_path, extract_events, read_bundle


def bundle_expect(conn, match_id: int) -> dict:
    """What a match's bundle header must say for read_bundle to accept it."""
    m = conn.execute("SELECT map, valve_match_id FROM match WHERE id=?", (match_id,)).fetchone()
    last = conn.execute("SELECT max(end_tick) FROM round WHERE match_id=?", (match_id,)).fetchone()
    return {"map": m[0], "valve_match_id": None if m[1] is None else str(m[1]),
            "last_tick": last[0]}


def _write_rows(conn, mid: int, demo: ParsedDemo, me: str) -> None:
    # Leetify ratings and Steam scoreboard numbers live on match_player rows: carry them over
    restore = importer.keep_summary_fields(conn, mid)
    for t in ("kill", "round", "multikill"):
        conn.execute(f"DELETE FROM {t} WHERE match_id=?", (mid,))
    importer.write_demo_rows(conn, mid, demo, me)
    restore()


def reparse_all(conn, me: str, backups_dir: Path, parse=parse_demo,
                on_progress: Callable[[int, int, dict], None] | None = None,
                events_dir: Path | None = None, extract=extract_events) -> list[dict]:
    rows = conn.execute("SELECT id, demo_path, valve_match_id FROM match "
                        "WHERE demo_path IS NOT NULL OR events_format IS NOT NULL "
                        "ORDER BY id").fetchall()
    todo: list[tuple[int, str | None, str | None]] = []  # (id, demo path or None, valve id)
    missing: list[dict] = []
    for mid, p, vid in rows:
        if p is not None and Path(p).is_file():
            todo.append((mid, p, vid))
        elif events_dir is not None and bundle_path(events_dir, mid).is_file():
            todo.append((mid, None, vid))
        else:
            # neither demo nor bundle: reported (not silently skipped), not in `total`
            missing.append({"path": p, "match_id": mid, "status": "missing"})
    results: list[dict] = []
    if todo:
        db.backup(conn, backups_dir)
    for i, (mid, p, vid) in enumerate(todo, 1):
        res = {"path": p, "match_id": mid}
        try:
            bundle: EventBundle | None = None
            stored: EventBundle | None = None
            if p is not None:
                with importer.as_dem(Path(p)) as dem:
                    if importer.uses_bundles(parse, events_dir):
                        bundle = extract(dem, valve_match_id=None if vid is None else str(vid))
                        demo = stats_from_bundle(bundle)
                    else:
                        demo = parse(dem)
                status = "reparsed"
            else:
                stored = read_bundle(events_dir, mid, bundle_expect(conn, mid))
                demo = stats_from_bundle(stored) if stored is not None else None
                status = "from-events"
            if demo is None:
                res["status"] = "missing"
            else:
                _write_rows(conn, mid, demo, me)
                if bundle is not None:
                    importer.store_bundle(conn, mid, bundle, events_dir)  # + fun stats
                elif stored is not None:
                    funstats.store(conn, mid, funstats.compute(stored))
                records.rebuild_for(conn, [mid])
                conn.commit()
                res["status"] = status
        except Exception as e:
            conn.rollback()
            res.update(status="failed", reason=f"{type(e).__name__}: {e}")
        results.append(res)
        if on_progress:
            on_progress(i, len(todo), res)
    for res in missing:
        results.append(res)
        if on_progress:
            on_progress(len(todo), len(todo), res)
    return results
