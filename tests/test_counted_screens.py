"""Every stats screen only uses counted matches (Competitive / Premier / FACEIT / unknown 5v5)."""
from datetime import datetime, timedelta, timezone

import pytest

from cs2tracker import db, records, rivals, stats

ME, PAL = "76561198000000001", "76561198000000002"
STRANGER_W, STRANGER_A = "76561198000000900", "76561198000000901"
T0 = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)


def _add(conn, at, game_mode=None, mode=None, mine=(), enemy=(), kills=10, source="demo"):
    mid = db.insert_match(conn, db.NewMatch(
        source=source, map="de_dust2", mode=mode, game_mode=game_mode, started_at=at,
        my_team_score=13, enemy_score=7, result="win"))
    rows = [{"steam_id": s, "name": s[-4:], "team": "mine", "won": True, "kills": kills,
             "deaths": 10, "assists": 1} for s in mine]
    rows += [{"steam_id": s, "name": s[-4:], "team": "enemy", "won": False, "kills": 5,
              "deaths": 10, "assists": 0} for s in enemy]
    db.insert_match_player_rows(conn, mid, rows)
    return mid


def _filler(prefix, n):
    return [f"7656119810{prefix}{i:05d}" for i in range(n)]


@pytest.fixture
def world(make_db):
    conn = make_db()
    db.set_setting(conn, "me_steam_id", ME)
    me_p = db.create_person(conn, "Me", tracked=True)
    pal_p = db.create_person(conn, "Pal", tracked=True)
    db.upsert_account(conn, ME, "Me")
    db.upsert_account(conn, PAL, "Pal")
    db.link_account(conn, ME, me_p)
    db.link_account(conn, PAL, pal_p)
    w = {}
    w["A"] = _add(conn, T0, "premier", mine=[ME, PAL, *_filler(1, 3)],
                  enemy=[STRANGER_A, *_filler(2, 4)])
    # Wingman demo half an hour later: 2v2, mode unknown, a huge kill line
    w["W"] = _add(conn, T0 + timedelta(minutes=30), mine=[ME, PAL], enemy=[STRANGER_W, _filler(3, 1)[0]],
                  kills=30)
    w["B"] = _add(conn, T0 + timedelta(hours=1), "premier", mine=[ME, PAL, *_filler(4, 3)],
                  enemy=_filler(5, 5))
    w["U"] = _add(conn, T0 + timedelta(days=1), mine=[ME, *_filler(6, 4)], enemy=_filler(7, 5))
    w["C"] = _add(conn, T0 + timedelta(days=2), "casual", mode="Casual", source="steam",
                  mine=[ME, PAL, *_filler(8, 8)], enemy=[STRANGER_W, *_filler(9, 9)])
    conn.commit()
    records.rebuild(conn)
    conn.commit()
    return conn, w


def test_sessions_ignore_uncounted(world):
    conn, w = world
    s = stats.sessions(conn)
    assert [w["A"], w["B"]] in s
    assert all(w["W"] not in g and w["C"] not in g for g in s)


def test_people_totals_ignore_uncounted(world):
    conn, w = world
    pal = next(r for r in stats.person_stats(conn) if r.name == "Pal")
    assert pal.matches == 2


def test_games_together_ignore_uncounted(world):
    conn, w = world
    assert stats.best_mates(conn, ME)[0]["games"] == 2


def test_records_ignore_uncounted(world):
    conn, w = world
    # every match is a win: at most A, B, U in a row = 3 (with W and C, Pal alone has 4)
    streak = [r for r in records.all_time(conn) if r["key"] == "win_streak"]
    assert streak and streak[0]["value"] <= 3
    assert all(r["match_id"] not in (w["W"], w["C"]) for r in records.all_time(conn))


def test_summary_drops_uncounted(world):
    conn, w = world
    out = stats.summary(conn, [w["A"], w["W"]], [], roast_level="off")
    assert [m["id"] for m in out["matches"]] == [w["A"]]


def test_rivals_ignore_uncounted(world):
    conn, w = world
    keys = {r["key"]: r for r in rivals.list(conn, ME, 1)}
    assert f"account:{STRANGER_W}" not in keys
    assert keys[f"account:{STRANGER_A}"]["total"] == 1


def test_matches_list_counted_and_mode(world, make_app):
    conn, w = world
    conn.close()
    client = make_app()
    ids = lambda q="": [m["id"] for m in client.get(f"/api/matches{q}").json()]   # noqa: E731
    assert sorted(ids()) == sorted([w["A"], w["B"], w["U"]])
    assert sorted(ids("?mode=premier")) == sorted([w["A"], w["B"]])
    assert ids("?mode=competitive") == []
    assert client.get("/api/matches?mode=wingman").status_code == 422
    assert client.get(f"/api/matches/{w['W']}").status_code == 200
