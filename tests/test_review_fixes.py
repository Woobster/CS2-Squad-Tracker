import json
import zipfile

import pytest

from cs2tracker import db, owner, records, steamid
from cs2tracker.steamid import SteamIdError
from test_owner import A, B, add_match
from test_steamid import KEY, FakeResp, opener_returning


def streak_holders(conn):
    return {r[0] for r in conn.execute(
        "SELECT a.steam_id FROM record r JOIN account a ON a.person_id=r.person_id "
        "WHERE r.key='win_streak'")}


def two_friends(conn):
    for i in range(3):
        add_match(conn, i, [A, B])
    for sid, name in ((A, "Ann"), (B, "Bo")):
        db.link_account(conn, sid, db.create_person(conn, name, True))
    records.rebuild(conn)
    conn.commit()
    assert streak_holders(conn) == {A, B}   # no owner yet: both are ordinary players


def test_set_owner_rebuilds_records(make_db):
    conn = make_db()
    two_friends(conn)
    owner.set_owner(conn, A)
    assert streak_holders(conn) == {B}


def test_settings_owner_change_rebuilds_and_clears_notice(make_app, tmp_path):
    conn = db.connect(tmp_path / "tracker.db", tmp_path / "backups")
    two_friends(conn)
    conn.close()
    client = make_app()                      # A is inferred (3 of 3) with a notice
    assert client.get("/api/owner").json()["notice"]
    r = client.put("/api/settings", json={"me_steam_id": B})
    assert r.status_code == 200
    assert client.get("/api/owner").json()["notice"] is None
    conn = db.connect(tmp_path / "tracker.db", tmp_path / "backups")
    assert streak_holders(conn) == {A}


def test_welcome_accepts_api_key_for_vanity(make_app):
    calls = []
    client = make_app(history_opener=opener_returning(
        {"response": {"success": 1, "steamid": A}}, calls))
    r = client.post("/api/owner", json={"input": "steamcommunity.com/id/ann", "api_key": f" {KEY} "})
    assert r.status_code == 200 and r.json() == {"steam_id": A}
    assert client.get("/api/settings").json()["steam_api_key_masked"].endswith(KEY[-4:])
    assert len(calls) == 1


def test_bad_key_from_welcome_not_saved(make_app):
    client = make_app(history_opener=opener_returning({"response": {"success": 42}}))
    r = client.post("/api/owner", json={"input": "steamcommunity.com/id/nobody", "api_key": KEY})
    assert r.status_code == 400
    assert client.get("/api/settings").json()["steam_api_key_masked"] is None


def test_welcome_html_has_key_field():
    from conftest import ROOT
    html = (ROOT / "app/cs2tracker/static/index.html").read_text(encoding="utf-8")
    assert 'id="welcome-key"' in html


@pytest.mark.parametrize("text", ["steamcommunity.com/id/Some Name/",
                                  "https://steamcommunity.com/id/ann extra"])
def test_vanity_with_inner_space_rejected(text):
    seen = []
    with pytest.raises(SteamIdError) as e:
        steamid.parse(text, KEY, opener=opener_returning({"response": {"success": 1, "steamid": A}}, seen))
    assert e.value.message == steamid.MSG_BAD and seen == []


def test_scan_blocks_archives_and_db_files(tmp_path):
    import privacy_scan
    for name in ("a.dem.gz", "b.bz2", "c.zip", "d.sqlite", "tracker.db-wal", "e.db.gz"):
        (tmp_path / name).write_bytes(b"x")
    assert len(privacy_scan.scan(tmp_path, [])) == 6


def test_scan_reads_utf16_and_nfd(tmp_path):
    import unicodedata
    import privacy_scan
    (tmp_path / "a.ps1").write_text("# Jöhnny was here\n", encoding="utf-16")
    (tmp_path / "b.txt").write_text(unicodedata.normalize("NFD", "hi Jöhnny"), encoding="utf-8")
    found = privacy_scan.scan(tmp_path, ["Jöhnny"])
    assert sorted(found) == ["a.ps1: name 'Jöhnny'", "b.txt: name 'Jöhnny'"]
