# CS2 Squad Tracker

A small Windows app that turns your Counter-Strike 2 matches into stats, awards and roasts for you and your friends. It runs on your own PC and opens in your browser. Your data stays on your computer.

- Fetches your matches and demos from Steam automatically while it runs
- Reads every demo: K/D, ADR, HS%, KAST, opening duels, clutches, utility, multi-kills and more
- **Session summary** for a night of games: scoreboard, awards of the night, roast of the night (trash talk, friendly or off), records and milestones, ready to copy or save as an image
- People and groups: track your friends across their alt accounts, by Steam ID
- **Rivals:** the players you keep meeting, with you or against you
- Imports your older matches from Steam's match history page

## Download and start

1. Download `CS2SquadTracker-v<version>.zip` from [Releases](../../releases).
2. Unzip it anywhere and double-click `Start.bat`. The first start downloads Python once (about 25 MB), so it needs internet that one time.
3. Follow the welcome screen: your Steam ID, then your Steam Web API key, match history authentication code and latest match sharing code (all explained in the app).

The full user guide is in [README.txt](README.txt), which also ships inside the zip.

## Where your data lives

Everything is stored in `%APPDATA%\CS2SquadTracker` on your PC. The app talks only to Steam/Valve. Your API key and authentication code never leave your computer.

## For developers

- Python 3.12, FastAPI, SQLite, plain JavaScript (no build step). Demos are read with [demoparser2](https://github.com/LaihoE/demoparser).
- Run the tests: `pip install fastapi uvicorn python-multipart httpx pytest demoparser2==0.42.2`, then `python -m pytest -q tests`.
- Run it locally: `cd app && python -m cs2tracker`.
- Build the release zip: put the Windows wheels in `assets/wheels/` and boiler-writter in `assets/vendor/boiler-writter/`, then run `python tools/build_zip.py`. The build runs `tools/privacy_scan.py` on everything it ships and refuses to write the zip if it finds a Steam ID, a match sharing code, a database or demo file, or a name from the optional, git-ignored `tools/denylist.txt`.

Third-party licences are in [licenses/](licenses).
