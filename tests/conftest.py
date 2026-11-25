import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tools"))
try:
    import demoparser2  # noqa: F401
except ImportError:  # the real parser isn't needed by these tests
    sys.path.insert(0, str(Path(__file__).parent / "stubs"))


@pytest.fixture
def make_db(tmp_path):
    from cs2tracker import db

    def _make():
        return db.connect(tmp_path / "tracker.db", tmp_path / "backups")
    return _make


@pytest.fixture
def make_app(tmp_path):
    from fastapi.testclient import TestClient

    from cs2tracker.app import create_app

    def _make(**kw):
        return TestClient(create_app(tmp_path, boiler_cmd=[], **kw))
    return _make
