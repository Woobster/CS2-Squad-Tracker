import sys
import zipfile

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / "tools"))
import build_zip  # noqa: E402
import privacy_scan  # noqa: E402

DENY = ["Zorblax", "Quinn Q"]   # made-up names: the real list stays off GitHub
PLANTED_ID = "7656119" + "8012345678"


def tree(tmp_path, files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


def test_planted_steam_id(tmp_path):
    found = privacy_scan.scan(tree(tmp_path, {"app/x.py": f"# owner {PLANTED_ID}\n"}), DENY)
    assert len(found) == 1 and found[0].startswith("app/x.py:")


def test_steam_id_base_allowed(tmp_path):
    assert privacy_scan.scan(tree(tmp_path, {"app/x.py": "BASE = 76561197960265728\n"}), DENY) == []


def test_denylisted_name(tmp_path):
    found = privacy_scan.scan(tree(tmp_path, {"README.txt": "Thanks zorblax!\n"}), DENY)
    assert found == ["README.txt: name 'Zorblax'"]


def test_name_must_be_whole_word(tmp_path):
    deny = ["Zed"]
    assert privacy_scan.scan(tree(tmp_path, {"a.txt": "Zedonk and zedzed\n"}), deny) == []
    assert privacy_scan.scan(tree(tmp_path, {"b.txt": "ask Zed.\n"}), deny) == ["b.txt: name 'Zed'"]


def test_seed_folder(tmp_path):
    found = privacy_scan.scan(tree(tmp_path, {"seed/x.txt": "hi"}), DENY)
    assert "seed: personal data folder" in found


def test_forbidden_files(tmp_path):
    t = tree(tmp_path, {"foo.db": "", "a/b.dem": "", "events/1.json.gz": "",
                        "steam-match-history.json": "{}", "leetify_seed.json": "{}"})
    assert len(privacy_scan.scan(t, DENY)) == 5


def test_share_codes(tmp_path):
    real = tree(tmp_path / "r", {"a.js": "CSGO-a2B3c-D4e5F-g6H7j-K8m9N-p2Q3r\n"})
    assert len(privacy_scan.scan(real, DENY)) == 1
    fake = tree(tmp_path / "f", {"a.js": 'placeholder="CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx"\n'})
    assert privacy_scan.scan(fake, DENY) == []


def test_vendor_and_wheels_skipped(tmp_path):
    t = tree(tmp_path, {f"wheels/w.txt": PLANTED_ID, f"vendor/v.txt": PLANTED_ID})
    assert privacy_scan.scan(t, DENY) == []


def test_denylist_is_optional(tmp_path):
    # tools/denylist.txt is a local, git-ignored file; without it the list is empty
    assert privacy_scan.load_denylist(tmp_path / "missing.txt") == []
    (tmp_path / "d.txt").write_text("# comment\nZorblax\n\n", encoding="utf-8")
    assert privacy_scan.load_denylist(tmp_path / "d.txt") == ["Zorblax"]


def test_readme_texts():
    text = (ROOT / "README.txt").read_text(encoding="utf-8")
    assert "FIRST START" in text and "WHAT'S NEW IN V1.3.2" in text


def test_version():
    from cs2tracker.app import APP_VERSION
    assert APP_VERSION == "1.3.2"


@pytest.mark.skipif(not (ROOT / "assets/wheels").is_dir(), reason="assets/ not present")
def test_build_real_repo(tmp_path):
    out = build_zip.build(ROOT, tmp_path)
    assert out.name == "CS2SquadTracker-v1.3.2.zip"
    names = zipfile.ZipFile(out).namelist()
    assert "CS2 Squad Tracker/Start.bat" in names
    assert "CS2 Squad Tracker/app/cs2tracker/static/welcome.js" in names
    assert any(n.startswith("CS2 Squad Tracker/wheels/") for n in names)
    for bad in ("tests/", "tools/", "seed/", ".pyc", "__pycache__", ".git", "docs/"):
        assert not any(bad in n for n in names), bad


def test_build_refuses_personal_data(tmp_path, monkeypatch):
    repo = tree(tmp_path / "repo", {"app/cs2tracker/x.py": f"ME = '{PLANTED_ID}'\n",
                                    "README.txt": "hi", "Start.bat": "", "bootstrap.ps1": "",
                                    "install_wheels.py": "", "licenses/a.txt": "",
                                    "assets/wheels/w.whl": "", "assets/vendor/v.exe": ""})
    with pytest.raises(SystemExit) as e:
        build_zip.build(repo, tmp_path / "out", denylist=DENY)
    assert "x.py" in str(e.value)
    assert not (tmp_path / "out" / "CS2SquadTracker-v1.3.2.zip").exists()
