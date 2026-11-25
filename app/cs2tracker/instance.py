"""Single-instance lock: ``<data_dir>/app.lock`` holds ``{"pid", "port"}`` of the running copy."""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

LOCK_NAME = "app.lock"


def _tasklist_has_pid(pid: int, run=subprocess.run) -> bool | None:
    """Does tasklist list `pid`? None when it couldn't tell (couldn't run, or output
    that doesn't decode, as on some localized Windows)."""
    try:
        out = run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                  capture_output=True, text=True, errors="replace", timeout=10,
                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return f'"{pid}"' in (out or "")
    except Exception:
        return None


def _pid_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == "win32":
        found = _tasklist_has_pid(pid)
        return True if found is None else found  # unknown: the port check decides
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _port_answers(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/about", timeout=2).close()
    except urllib.error.HTTPError:
        return True  # it answered, even if with an error status
    except (OSError, ValueError):
        return False
    return True


def _read(path: Path) -> tuple[int, int] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        pid, port = data["pid"], data["port"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if isinstance(pid, int) and isinstance(port, int) and not isinstance(pid, bool) \
            and not isinstance(port, bool):
        return pid, port
    return None


def _write_tmp(data_dir: Path, port: int) -> Path:
    tmp = data_dir / f"{LOCK_NAME}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps({"pid": os.getpid(), "port": port}), encoding="utf-8")
    return tmp


def acquire(data_dir: Path, port: int, alive=_pid_alive, answers=_port_answers,
            sleep=time.sleep, clock=time.monotonic, grace: float = 10.0) -> int | None:
    """None if this process took the lock, else the port of the live copy.

    A live pid whose port is silent may still be starting up, so it gets ``grace`` seconds
    (polled every 0.5 s) to answer before its lock is taken over.
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / LOCK_NAME
    tmp = _write_tmp(data_dir, port)
    try:
        try:
            os.link(tmp, path)  # atomic and exclusive: only one simultaneous start wins
            return None
        except FileExistsError:
            pass
        except OSError:  # filesystem without hard links
            if not path.exists():
                os.replace(tmp, path)
                return None
        held = _read(path)
        if held is not None and alive(held[0]):
            deadline = clock() + grace
            while True:
                if answers(held[1]):
                    return held[1]
                if clock() >= deadline:
                    break
                sleep(0.5)
        os.replace(tmp, path)  # corrupt, dead or never-answering lock: take over
        return None
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def release(data_dir: Path) -> None:
    path = Path(data_dir) / LOCK_NAME
    held = _read(path)
    if held is not None and held[0] == os.getpid():
        try:
            path.unlink()
        except OSError:
            pass
