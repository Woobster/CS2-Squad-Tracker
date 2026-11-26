"""GET /api/dashboard: the owner's counted matches in the dashboard page's compact format."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from cs2tracker import dashboard, db

ME, ME_ALT, PAL, PAL_ALT, FOE = ("76561198000000001", "76561198000000011", "76561198000000002",
                                 "76561198000000022", "76561198000000900")
T0 = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)


def _add(conn, at, mine, enemy, game_mode="premier", mode=None, my=13, en=7, **kw):
    mid = db.insert_match(conn, db.NewMatch(
        source="steam", map="de_dust2", mode=mode, game_mode=game_mode, started_at=at,
        my_team_score=my, enemy_score=en, result="win" if my > en else "loss",
        duration=1800, wait_seconds=40, **kw))
    rows = [{"steam_id": s, "name": n, "team": "mine", "won": my > en, "kills": 20, "deaths": 10,
             "assists": 3} for s, n in mine]
    rows += [{"steam_id": s, "name": n, "team": "enemy", "won": en > my, "kills": 8, "deaths": 15,
              "assists": 1} for s, n in enemy]
    db.insert_match_player_rows(conn, mid, rows)
    return mid


def _five(prefix):
    return [(f"7656119810{prefix}{i:05d}", f"x{prefix}{i}") for i in range(5)]


@pytest.fixture
def conn(make_db):
    c = make_db()
    db.set_setting(c, "me_steam_id", ME)
    me_p, pal_p = db.create_person(c, "Woob", tracked=True), db.create_person(c, "Pal", tracked=True)
    for sid, pid in ((ME, me_p), (ME_ALT, me_p), (PAL, pal_p), (PAL_ALT, pal_p)):
        db.upsert_account(c, sid, "acct" + sid[-2:])
        db.link_account(c, sid, pid)
    c.commit()
    return c


def test_dashboard_shape(conn):
    a = _add(conn, T0, [(ME, "woob_now"), (PAL, "pal1"), *_five(1)[:3]], [(FOE, "<b>x</b>"), *_five(2)[:4]],
             my=9, en=13)
    out = dashboard.build(conn, ME)
    assert out["me"] == ME
    (m,) = out["matches"]
    assert m["id"] == a and m["t"] == int(T0.timestamp())
    assert (m["mo"], m["mp"], m["w"], m["du"], m["s"]) == ("Premier", "Dust II", 40, 1800, [9, 13])
    mine, foes = m["T"]
    assert any(p[0] == ME for p in mine) and len(mine) == 5 and len(foes) == 5
    me_row = next(p for p in mine if p[0] == ME)
    assert me_row[1] == "Woob" and me_row[2] is None and me_row[3:6] == [20, 3, 10]
    assert "D" not in m


def test_dashboard_counted_only(conn):
    _add(conn, T0, [(ME, "w"), *_five(1)[:4]], _five(2))
    _add(conn, T0 + timedelta(hours=1), [(ME, "w"), (PAL, "p")], [(FOE, "f"), ("76561198000000901", "g")],
         game_mode=None)                                     # wingman demo (2v2, mode unknown)
    _add(conn, T0 + timedelta(hours=2), [(ME, "w"), *_five(3)[:4]], _five(4), game_mode="casual",
         mode="Casual")
    assert len(dashboard.build(conn, ME)["matches"]) == 1


def test_dashboard_unknown_5v5_mode_label(conn):
    _add(conn, T0, [(ME, "w"), *_five(1)[:4]], _five(2), game_mode=None)
    assert dashboard.build(conn, ME)["matches"][0]["mo"] == "Unknown 5v5"


def test_dashboard_alt_accounts_merge(conn):
    _add(conn, T0, [(ME_ALT, "smurf"), (PAL, "p"), *_five(1)[:3]], _five(2))
    _add(conn, T0 + timedelta(hours=1), [(ME, "w"), (PAL_ALT, "p2"), *_five(3)[:3]], _five(4))
    out = dashboard.build(conn, ME)
    assert len(out["matches"]) == 2
    for m in out["matches"]:
        ids = {p[0]: p[1] for p in m["T"][0]}
        assert ids[ME] == "Woob" and ids[PAL] == "Pal"


def test_dashboard_demo_block(conn):
    a = _add(conn, T0, [(ME, "w"), (PAL, "p"), *_five(1)[:3]], _five(2))
    _add(conn, T0 + timedelta(hours=1), [(ME, "w"), *_five(3)[:4]], _five(4))
    conn.execute("UPDATE match_player SET hltv_rating=1.21, adr=88.5 WHERE match_id=? AND steam_id=?",
                 (a, ME))
    fun = {"kills": 20, "damage": 1400, "rounds_played": 16, "rating": 0.0,
           "victims": {FOE: 2, PAL_ALT: 1}, "killers": {PAL_ALT: 1}}
    conn.execute("INSERT INTO fun_stats(match_id, steam_id, fun_format, data) VALUES (?,?,?,?)",
                 (a, ME, 1, json.dumps(fun)))
    ms = {m["id"]: m for m in dashboard.build(conn, ME)["matches"]}
    d = ms[a]["D"]
    assert d["damage"] == 1400 and d["rating"] == 1.21 and d["adr"] == 88.5
    assert d["victims"] == {FOE: 2, PAL: 1} and d["killers"] == {PAL: 1}   # alts merged
    assert all("D" not in m for i, m in ms.items() if i != a)


def test_dashboard_empty(conn):
    assert dashboard.build(conn, ME) == {"me": ME, "matches": []}


def test_dashboard_route_needs_owner(make_app):
    assert make_app().get("/api/dashboard").status_code != 200


def test_dashboard_route(conn, make_app):
    _add(conn, T0, [(ME, "w"), *_five(1)[:4]], _five(2))
    conn.close()
    r = make_app().get("/api/dashboard")
    assert r.status_code == 200 and len(r.json()["matches"]) == 1
