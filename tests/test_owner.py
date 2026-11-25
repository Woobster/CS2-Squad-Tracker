from datetime import datetime, timedelta, timezone

from cs2tracker import db, owner

A, B, C = "76561198000000001", "76561198000000002", "76561198000000003"
NOTICE = "Using Ann (7656…0001) as you. Change it in Settings if that's wrong."


def add_match(conn, i, mine, enemy=(), source="demo"):
    mid = db.insert_match(conn, db.NewMatch(
        source=source, map="de_dust2",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=i),
        my_team_score=13, enemy_score=5, result="win"))
    names = {A: "Ann", B: "Bo", C: "Cy"}
    rows = [{"steam_id": s, "name": names.get(s, s), "team": "mine", "won": True} for s in mine]
    rows += [{"steam_id": s, "name": s, "team": "enemy", "won": False} for s in enemy]
    db.insert_match_player_rows(conn, mid, rows)
    return mid


def test_saved_owner_untouched(make_db):
    conn = make_db()
    db.set_setting(conn, "me_steam_id", B)
    for i in range(5):
        add_match(conn, i, [A])
    st = owner.resolve(conn)
    assert (st.status, st.steam_id, st.notice) == ("set", B, None)
    assert db.get_setting(conn, "owner_inferred") is None
    assert db.get_setting(conn, "onboarding_step") is None


def test_infer_owner(make_db):
    conn = make_db()
    for i in range(10):
        add_match(conn, i, [A, B] if i < 6 else [A])
    st = owner.resolve(conn)
    assert (st.status, st.steam_id, st.name) == ("set", A, "Ann")
    assert db.get_setting(conn, "me_steam_id") == A
    assert db.get_setting(conn, "owner_inferred") == "1"
    assert owner.current(conn).notice == NOTICE


def test_infer_from_leetify_only_rows(make_db):
    conn = make_db()
    for i in range(5):
        add_match(conn, i, [A], source="leetify")
    assert owner.resolve(conn).steam_id == A


def test_ambiguous(make_db):
    conn = make_db()
    for i in range(10):
        add_match(conn, i, [A, B] if i < 8 else [C], enemy=[B] if i >= 8 else ())
    st = owner.resolve(conn)
    assert st.status == "ambiguous" and st.steam_id is None
    assert [c["steam_id"] for c in st.candidates] == [A, B, C]
    assert st.candidates[0] == {"steam_id": A, "name": "Ann", "matches": 8}
    assert db.get_setting(conn, "me_steam_id") is None
    assert db.get_setting(conn, "onboarding_step") == "steam_id"


def test_candidates_at_most_three(make_db):
    conn = make_db()
    ids = [f"7656119800000001{n}" for n in range(5)]
    for i in range(5):
        add_match(conn, i, [x for j, x in enumerate(ids) if j != i])  # each in 4 of 5
    assert len(owner.resolve(conn).candidates) == 3


def test_too_few_matches(make_db):
    conn = make_db()
    for i in range(2):
        add_match(conn, i, [A])
    st = owner.resolve(conn)
    assert st.status == "needs_setup"
    assert db.get_setting(conn, "me_steam_id") is None


def test_set_owner_moves_step(make_db):
    conn = make_db()
    owner.resolve(conn)
    assert db.get_setting(conn, "onboarding_step") == "steam_id"
    owner.set_owner(conn, A)
    assert db.get_setting(conn, "onboarding_step") == "history"
    assert owner.current(conn).status == "set"


GETS = ["/api/settings", "/api/about", "/api/owner", "/api/people", "/api/matches",
        "/api/recurring", "/api/sessions", "/api/stats/best-mates", "/api/rivals",
        "/api/groups"]


def test_fresh_install_no_500(make_app):
    client = make_app()
    for url in GETS:
        r = client.get(url)
        assert r.status_code in (200, 428), (url, r.status_code, r.text)
        if r.status_code == 428:
            assert r.json() == {"detail": "Set your Steam ID first.", "code": "no_owner"}
    assert client.get("/api/settings").status_code == 200
    assert client.get("/api/about").status_code == 200
    assert client.get("/api/people").status_code == 428
    r = client.post("/api/summary", json={"match_ids": [], "person_ids": []})
    assert r.status_code == 428
    o = client.get("/api/owner").json()
    assert o["status"] == "needs_setup" and o["onboarding_step"] == "steam_id"


def test_update_without_owner(make_app):
    calls = []
    client = make_app(history_opener=lambda *a, **k: calls.append(a))
    r = client.post("/api/update")
    assert r.status_code == 428
    assert r.json()["detail"] == "Set your Steam ID first."
    assert client.post("/api/settings/test-history", json={}).status_code == 428
    assert calls == []


def test_notice_seen_and_onboarding(make_app, tmp_path):
    conn = db.connect(tmp_path / "tracker.db", tmp_path / "backups")
    for i in range(4):
        add_match(conn, i, [A])
    conn.close()
    client = make_app()
    assert client.get("/api/owner").json()["notice"] == NOTICE
    assert client.post("/api/owner/notice-seen").status_code == 200
    assert client.get("/api/owner").json()["notice"] is None
    assert client.put("/api/onboarding", json={"step": "done"}).status_code == 200
    assert client.get("/api/owner").json()["onboarding_step"] == "done"
    assert client.put("/api/onboarding", json={"step": "nope"}).status_code == 400
    assert client.get("/api/people").status_code == 200
