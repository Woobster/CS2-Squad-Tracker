"""Launch the local web app: ``python -m cs2tracker``."""
import argparse
import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

HOST = "127.0.0.1"


def default_data_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "CS2SquadTracker" if appdata else Path.home() / ".cs2squadtracker"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def make_server_config(port: int = 0, app=None) -> uvicorn.Config:
    # With no app given the import string is only resolved when the server starts.
    return uvicorn.Config(app if app is not None else "cs2tracker.app:create_app",
                          host=HOST, port=port or free_port(), log_level="warning")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="cs2tracker")
    ap.add_argument("--data-dir", type=Path, default=default_data_dir())
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)

    from cs2tracker import instance
    port = free_port()
    other = instance.acquire(args.data_dir, port)
    if other is not None:
        print("CS2 Squad Tracker is already running", flush=True)
        if not args.no_browser:
            webbrowser.open(f"http://{HOST}:{other}/")
        return
    try:
        _serve(args, port)
    finally:
        instance.release(args.data_dir)


def _serve(args, port: int) -> None:
    from cs2tracker.app import create_app
    from cs2tracker.db import NewerDatabaseError
    try:
        app = create_app(args.data_dir)
    except NewerDatabaseError as e:
        print(e, flush=True)
        sys.exit(1)
    config = make_server_config(port, app=app)
    server = uvicorn.Server(config)
    url = f"http://{HOST}:{config.port}/"
    print("CS2 Squad Tracker running — close this window to quit")
    print(url, flush=True)
    if not args.no_browser:
        def open_when_up() -> None:
            while not server.started and not server.should_exit:
                threading.Event().wait(0.1)
            if server.started:
                webbrowser.open(url)
        threading.Thread(target=open_when_up, daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
