"""Which matches count: only Competitive, Premier and FACEIT (spec 2026-11-26-counted-modes)."""
import sqlite3
from datetime import datetime, timezone

import pytest

from cs2tracker import db


def _match(conn, game_mode, mode, mine, enemy, source="demo"):
    mid = db.insert_match(conn, db.NewMatch(
        source=source, map="de_dust2", mode=mode, game_mode=game_mode,
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc), my_team_score=13, enemy_score=5,
        result="win"))
    rows = [{"steam_id": f"7656119800{mid:03d}1{i:02d}", "name": f"m{i}", "team": "mine", "won": True}
            for i in range(mine)]
    rows += [{"steam_id": f"7656119800{mid:03d}2{i:02d}", "name": f"e{i}", "team": "enemy", "won": False}
             for i in range(enemy)]
    db.insert_match_player_rows(conn, mid, rows)
    return mid


@pytest.mark.parametrize("game_mode, mode, mine, enemy, counts", [
    ("premier", None, 5, 5, True),
    ("competitive", None, 0, 0, True),
    (None, "FACEIT", 5, 5, True),
    (None, "matchmaking", 0, 0, True),
    (None, " Premier ", 5, 5, True),
    (None, "Wingman", 2, 2, False),
    (None, "matchmaking_wingman", 5, 5, False),
    (None, "Casual", 10, 10, False),
    (None, None, 5, 5, True),
    (None, None, 5, 4, True),
    (None, None, 2, 2, False),
    (None, None, 10, 10, False),
    (None, None, 5, 0, True),
    (None, None, 0, 0, True),
])
def test_rule(make_db, game_mode, mode, mine, enemy, counts):
    conn = make_db()
    mid = _match(conn, game_mode, mode, mine, enemy)
    assert db.counted(conn, mid) is counts


def test_flip_when_mode_known(make_db):
    conn = make_db()
    mid = _match(conn, None, None, 2, 2)
    assert not db.counted(conn, mid)
    conn.execute("UPDATE match SET game_mode='premier' WHERE id=?", (mid,))
    assert db.counted(conn, mid)


def test_counted_sql_lists_ids(make_db):
    conn = make_db()
    a = _match(conn, "premier", None, 5, 5)
    _match(conn, None, "Wingman", 2, 2)
    assert [r[0] for r in conn.execute(db.COUNTED_SQL)] == [a]


def test_migrates_v4_db(tmp_path):
    path = tmp_path / "t.db"
    conn = db.connect(path, tmp_path / "b")
    conn.execute("DROP VIEW counted_match")
    conn.execute("PRAGMA user_version=4")
    conn.commit()
    conn.close()
    conn = db.connect(path, tmp_path / "b")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='counted_match'").fetchone()
    assert list((tmp_path / "b").glob("cs2tracker-pre-v5*"))
