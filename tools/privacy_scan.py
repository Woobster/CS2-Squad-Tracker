"""Find personal data in a folder that is about to ship (or was shipped).

Findings are "<relpath>: <reason>". Third-party wheels/ and vendor/ are skipped (binaries).
Checked:
- Steam64 IDs (7656119 + 10 digits), except 76561197960265728, the documented Steam ID base;
- names from the denylist (whole words, any case);
- real match sharing codes (the CSGO-xxxxx-… placeholder is fine);
- data files (databases and their side files, demos, archives, steam-*-history.json,
  leetify_seed.json) and seed/.
"""
import fnmatch
import re
import sys
import unicodedata
from pathlib import Path

STEAM_ID = re.compile(r"7656119\d{10}")
STEAM_ID_BASE = "76561197960265728"
SHARE_CODE = re.compile(r"CSGO(?:-[A-Za-z0-9]{5}){5}")
PLACEHOLDER = "CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx"
FORBIDDEN_FILES = ("*.db", "*.db-*", "*.sqlite", "*.sqlite3", "*.dem", "*.gz", "*.bz2", "*.zip",
                   "*.7z", "*.rar", "steam-*-history.json", "leetify_seed.json")
SKIP_DIRS = ("wheels", "vendor")


def load_denylist(path: Path) -> list[str]:
    """Names to block, one per line (# comments). The file is local and git-ignored; a
    missing file means no names (IDs, codes and data files are still checked)."""
    if not Path(path).is_file():
        return []
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


def _name_re(name: str) -> re.Pattern:
    name = unicodedata.normalize("NFC", name)
    return re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.IGNORECASE)


def read_text(p: Path) -> str:
    """UTF-8, or UTF-16 when the file starts with a byte-order mark; NFC-normalised so a
    decomposed "a" + accent still matches a name."""
    raw = p.read_bytes()
    enc = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
    return unicodedata.normalize("NFC", raw.decode(enc, errors="ignore"))


def scan(root: Path, denylist: list[str]) -> list[str]:
    root = Path(root)
    names = [(n, _name_re(n)) for n in denylist]
    found: list[str] = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if rel.parts[0] in SKIP_DIRS:
            continue
        r = rel.as_posix()
        if p.is_dir():
            if p.name == "seed":
                found.append(f"{r}: personal data folder")
            continue
        if any(fnmatch.fnmatch(p.name.lower(), pat) for pat in FORBIDDEN_FILES):
            found.append(f"{r}: data file")
            continue
        text = read_text(p)
        if any(m != STEAM_ID_BASE for m in STEAM_ID.findall(text)):
            found.append(f"{r}: Steam ID")
        if any(c != PLACEHOLDER for c in SHARE_CODE.findall(text)):
            found.append(f"{r}: match sharing code")
        for name, rx in names:
            if rx.search(text):
                found.append(f"{r}: name '{name}'")
    return found


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    hits = scan(Path(sys.argv[1]), load_denylist(here / "denylist.txt"))
    print("\n".join(hits) or "No personal data found.")
    sys.exit(1 if hits else 0)
