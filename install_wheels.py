"""Install wheels into a folder using only the standard library.

Runs under the embedded Python, which has no pip. Usage:
    python install_wheels.py <wheels_dir> <site_packages_dir>
"""
import sys
import zipfile
from pathlib import Path, PurePosixPath

INSTALL_SCHEMES = {"purelib", "platlib"}


def _target(name: str, site_packages: Path) -> Path | None:
    parts = PurePosixPath(name).parts
    if not parts or name.endswith("/"):
        return None
    if "\\" in name or ".." in parts or PurePosixPath(name).is_absolute() or ":" in parts[0]:
        raise ValueError(f"unsafe path in wheel: {name}")
    if parts[0].endswith(".data"):
        # purelib/platlib go to site-packages; scripts, headers, data are skipped
        if len(parts) < 3 or parts[1] not in INSTALL_SCHEMES:
            return None
        parts = parts[2:]
    dest = site_packages.joinpath(*parts)
    if not dest.resolve().is_relative_to(site_packages.resolve()):
        raise ValueError(f"unsafe path in wheel: {name}")
    return dest


def install(wheels_dir: Path, site_packages: Path) -> list[str]:
    wheels_dir, site_packages = Path(wheels_dir), Path(site_packages)
    site_packages.mkdir(parents=True, exist_ok=True)
    installed = []
    for whl in sorted(wheels_dir.glob("*.whl")):
        with zipfile.ZipFile(whl) as z:
            targets = [(info, _target(info.filename, site_packages)) for info in z.infolist()]
            for info, dest in targets:
                if dest is None:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(dest, "wb") as out:
                    out.write(src.read())
        installed.append(whl.name)
    return installed


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: install_wheels.py <wheels_dir> <site_packages_dir>")
        return 2
    for name in install(Path(argv[1]), Path(argv[2])):
        print("  installed", name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
