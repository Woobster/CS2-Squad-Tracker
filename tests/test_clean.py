import importlib.util

from conftest import ROOT


def test_fresh_db_has_no_groups(make_db):
    conn = make_db()
    assert conn.execute("SELECT COUNT(*) FROM team_group").fetchone()[0] == 0


def test_no_seed_module():
    assert importlib.util.find_spec("cs2tracker.seed") is None
    assert not (ROOT / "seed").exists()


def test_no_default_me():
    from cs2tracker import db
    assert not hasattr(db, "DEFAULT_ME")


def test_hints_neutral():
    html = (ROOT / "app/cs2tracker/static/index.html").read_text(encoding="utf-8")
    assert "Leetify" not in html
