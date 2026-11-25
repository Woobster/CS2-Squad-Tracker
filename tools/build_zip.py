"""Build the release zip: python tools/build_zip.py [out_dir]

Stages "CS2 Squad Tracker/" from the repo (app/, the launcher files, licenses/ and the
git-ignored assets/wheels and assets/vendor), runs the privacy scan on exactly what will
ship, and refuses to write the zip when it finds anything.
"""
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import privacy_scan  # noqa: E402

TOP = "CS2 Squad Tracker"
FILES = ("README.txt", "Start.bat", "bootstrap.ps1", "install_wheels.py")
DIRS = {"app": "app", "licenses": "licenses", "assets/wheels": "wheels", "assets/vendor": "vendor"}
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")


def version(repo: Path) -> str:
    src = (repo / "app/cs2tracker/app.py").read_text(encoding="utf-8")
    m = re.search(r'^APP_VERSION = "([^"]+)"', src, re.M)
    return m.group(1) if m else "0"


def build(repo: Path, out_dir: Path, denylist: list[str] | None = None) -> Path:
    repo, out_dir = Path(repo), Path(out_dir)
    if denylist is None:
        denylist = privacy_scan.load_denylist(Path(__file__).resolve().parent / "denylist.txt")
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / TOP
        stage.mkdir()
        for f in FILES:
            shutil.copy2(repo / f, stage / f)
        for src, dst in DIRS.items():
            shutil.copytree(repo / src, stage / dst, ignore=IGNORE)
        hits = privacy_scan.scan(stage, denylist)
        if hits:
            raise SystemExit("Personal data found, no zip built:\n" + "\n".join(hits))
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"CS2SquadTracker-v{version(repo)}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(stage.rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(stage.parent).as_posix())
    return out


if __name__ == "__main__":
    repo = Path(__file__).resolve().parents[1]
    print(build(repo, Path(sys.argv[1]) if len(sys.argv) > 1 else repo / "dist"))
