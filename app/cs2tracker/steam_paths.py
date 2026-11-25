"""Find CS2's replays folder through Steam's registry entry and library list (Windows)."""
import os
import re
from pathlib import Path

GAME_DIR = ("steamapps", "common", "Counter-Strike Global Offensive", "game", "csgo")
_PATH_RE = re.compile(r'"path"\s+"([^"]+)"')


def parse_library_paths(vdf_text: str) -> list[Path]:
    """Library folders from libraryfolders.vdf ("path" values, with \\\\ unescaped)."""
    return [Path(m.replace("\\\\", "\\")) for m in _PATH_RE.findall(vdf_text)]


def steam_path_from_registry() -> Path | None:
    if os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value, _ = winreg.QueryValueEx(key, "SteamPath")
    except (ImportError, OSError):
        return None
    return Path(value) if value else None


def find_replays_dir(steam_path: Path | None = None) -> Path | None:
    """The first library's replays folder that exists, else the first CS2 game folder
    (its replays folder is created). None when Steam or CS2 can't be found."""
    steam = steam_path if steam_path is not None else steam_path_from_registry()
    if steam is None:
        return None
    try:
        libs = parse_library_paths((Path(steam) / "steamapps" / "libraryfolders.vdf")
                                   .read_text(encoding="utf-8", errors="replace"))
    except OSError:
        libs = []
    if Path(steam) not in libs:
        libs.append(Path(steam))
    games = [lib.joinpath(*GAME_DIR) for lib in libs]
    for game in games:
        if (game / "replays").is_dir():
            return game / "replays"
    for game in games:
        if game.is_dir():
            try:
                (game / "replays").mkdir(exist_ok=True)
            except OSError:
                continue
            return game / "replays"
    return None
