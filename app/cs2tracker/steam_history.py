"""Steam match history import (spec v1.3 §1).

Steam's Personal Game Data page (Ranked Competitive and Premier tabs) is only visible to
its signed-in owner, so it comes in as a file: the JSON export (format 1) or a saved copy of the page.
Both parse to the same ``SteamMatch`` list; ``import_matches`` then attaches each row to
the match the app already has (start within ±2 minutes and the score agreeing in some
orientation) or adds it as a ``source='steam'`` match.

Bots (a name starting ``[BOT]``, or Steam ID ``0``) are dropped while parsing, so they are
never stored as accounts or players.
"""
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable

from cs2tracker import db, records

NOT_STEAM = "That file isn't a Steam match history export or page."
EMPTY_PAGE = ("This page was saved before any matches were loaded. Press 'Load more history' "
              "until it stops, then save with 'Webpage, Complete'.")
# Class of the table that holds the matches on Steam's Personal Game Data page. It is there
# even in a page saved before any matches were loaded (they arrive later through JavaScript).
PAGE_MARKER = "csgo_scoreboard_root"
# The Steam tabs we import (`matchhistorycompetitivepermap`, `matchhistorypremier`), by the
# mode Steam writes, and what match.game_mode stores for them. Other modes are skipped.
GAME_MODES = {"Competitive": "competitive", "Premier": "premier"}
STEAM_ID64_BASE = 76561197960265728
MATCH_WINDOW_S = 120
BATCH = 50
MAP_NAMES = {
    "Dust II": "de_dust2", "Mirage": "de_mirage", "Inferno": "de_inferno", "Nuke": "de_nuke",
    "Ancient": "de_ancient", "Anubis": "de_anubis", "Overpass": "de_overpass",
    "Vertigo": "de_vertigo", "Train": "de_train", "Cache": "de_cache",
    "Cobblestone": "de_cbble", "Office": "cs_office", "Agency": "cs_agency",
    "Thera": "de_thera", "Grail": "de_grail",
}
# Modes as Steam writes them before the map name ("Competitive Dust II"); longest first.
KNOWN_MODES = ("Danger Zone", "Competitive", "Wingman", "Premier", "Casual", "Deathmatch")
DATE_RE = re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d GMT")


@dataclass(frozen=True)
class SteamPlayer:
    steam_id: str
    name: str
    ping: int | None
    kills: int | None
    assists: int | None
    deaths: int | None
    mvps: int
    hs_percent: int | None
    score: int | None


@dataclass(frozen=True)
class SteamMatch:
    mode: str
    map: str                      # Steam's name ("Dust II"); see map_name()
    started_at: datetime          # UTC
    wait_seconds: int | None
    duration: int | None          # seconds
    replay: bool
    score: tuple[int, int]        # in the same order as teams
    teams: tuple[tuple[SteamPlayer, ...], tuple[SteamPlayer, ...]]


@dataclass
class ImportReport:
    imported: int = 0
    attached: int = 0
    skipped: int = 0
    skipped_mode: int = 0


def map_name(steam_map: str) -> str:
    return MAP_NAMES.get(steam_map, steam_map)


def is_bot(steam_id: str, name: str) -> bool:
    return str(steam_id) == "0" or (name or "").startswith("[BOT]")


def report_message(rep: ImportReport) -> str:
    msg = (f"Imported {rep.imported} new matches · attached {rep.attached} to matches you "
           f"already had · skipped {rep.skipped}")
    if rep.skipped_mode > 0:
        msg += f" · {rep.skipped_mode} other modes skipped"
    return msg


# ---------- parsing ----------

def _seconds(text: str | None) -> int | None:
    """'mm:ss' or 'h:mm:ss' as seconds; None when empty or unreadable."""
    parts = (text or "").strip().split(":")
    if not all(p.strip().isdigit() for p in parts) or not 2 <= len(parts) <= 3:
        return None
    total = 0
    for p in parts:
        total = total * 60 + int(p)
    return total


def _date(text: str) -> datetime:
    return datetime.strptime(text.strip(), "%Y-%m-%d %H:%M:%S GMT").replace(tzinfo=timezone.utc)


def _score(text: str) -> tuple[int, int]:
    a, b = (int(x) for x in text.split(":"))
    return a, b


def _int(v) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool):
        raise ValueError("not a number")
    if isinstance(v, int):
        return v
    s = str(v).strip().rstrip("%").strip()
    return int(s) if s else None


def _player(steam_id, name, ping, k, a, d, mvp, hsp, score) -> SteamPlayer:
    return SteamPlayer(str(steam_id), name, _int(ping), _int(k), _int(a), _int(d),
                       _int(mvp) or 0, _int(hsp), _int(score))


def _keep(players: list[SteamPlayer]) -> tuple[SteamPlayer, ...]:
    return tuple(p for p in players if not is_bot(p.steam_id, p.name))


def _parse_json(doc) -> list[SteamMatch]:
    if not isinstance(doc, dict) or doc.get("format") != 1 or not isinstance(doc.get("matches"), list):
        raise ValueError(NOT_STEAM)
    out = []
    for m in doc["matches"]:
        teams = m["teams"]
        if len(teams) != 2:
            raise ValueError(NOT_STEAM)
        out.append(SteamMatch(
            mode=str(m["mode"]), map=str(m["map"]), started_at=_date(m["date"]),
            wait_seconds=_seconds(m.get("wait")), duration=_seconds(m.get("duration")),
            replay=bool(m.get("replay")), score=_score(m["score"]),
            teams=tuple(_keep([_player(p["steam_id"], str(p["name"]), p.get("ping"), p.get("k"),
                                       p.get("a"), p.get("d"), p.get("mvp"), p.get("hsp"),
                                       p.get("score")) for p in t]) for t in teams)))
    return out


class _Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict, parent: "_Node | None") -> None:
        self.tag, self.attrs, self.children, self.parent = tag, attrs, [], parent

    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()

    def text(self) -> str:
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)

    def walk(self):
        for c in self.children:
            if isinstance(c, _Node):
                yield c
                yield from c.walk()

    def find_all(self, pred) -> list["_Node"]:
        return [n for n in self.walk() if pred(n)]

    def has_class(self, cls: str) -> bool:
        return cls in self.classes()


VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
        "track", "wbr"}


class _TreeBuilder(HTMLParser):
    """A small, forgiving DOM: void elements never open, a stray end tag is ignored, and a
    new row or cell closes the open one (as browsers do)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {}, None)
        self.cur = self.root

    def _close_until(self, tags: set[str], stop: set[str]) -> None:
        n = self.cur
        while n is not self.root and n.tag not in stop:
            if n.tag in tags:
                self.cur = n.parent
                return
            n = n.parent

    def handle_starttag(self, tag, attrs):
        if tag in ("td", "th"):
            self._close_until({"td", "th"}, {"tr", "table"})
        elif tag == "tr":
            self._close_until({"tr"}, {"table", "tbody", "thead", "tfoot"})
        node = _Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(_Node(tag, {k: (v or "") for k, v in attrs}, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root:
            if n.tag == tag:
                self.cur = n.parent
                return
            n = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def _rows(table: _Node) -> list[_Node]:
    """The table's own rows (not those of tables nested in it)."""
    out = []

    def visit(n: _Node) -> None:
        for c in n.children:
            if not isinstance(c, _Node):
                continue
            if c.tag == "tr":
                out.append(c)
            elif c.tag in ("tbody", "thead", "tfoot"):
                visit(c)
    visit(table)
    return out


def _cells(row: _Node) -> list[_Node]:
    return [c for c in row.children if isinstance(c, _Node) and c.tag in ("td", "th")]


def _mode_map(text: str) -> tuple[str, str]:
    text = " ".join(text.split())
    for mode in KNOWN_MODES:
        if text == mode or text.startswith(mode + " "):
            return mode, text[len(mode):].strip()
    mode, _, rest = text.partition(" ")
    return mode, rest.strip()


def _html_player(row: _Node) -> SteamPlayer:
    cells = _cells(row)
    name_cell = cells[0]
    profiled = name_cell.find_all(lambda n: "data-miniprofile" in n.attrs)
    named = [n for n in profiled if n.tag == "a" and n.text().strip()]
    links = named or name_cell.find_all(lambda n: n.has_class("linkTitle") and n.text().strip())
    name = (links[0].text() if links else name_cell.text()).strip()
    acc = (named or profiled)[0].attrs["data-miniprofile"].strip() if profiled else ""
    steam_id = str(int(acc) + STEAM_ID64_BASE) if acc.isdigit() and int(acc) > 0 else "0"
    vals = [c.text().strip() for c in cells[1:8]]
    vals += [""] * (7 - len(vals))
    ping, k, a, d, mvp, hsp, score = vals
    stars = mvp.replace("\u2605", "").strip()
    mvps = (int(stars) if stars.isdigit() else 1) if "\u2605" in mvp else 0
    return _player(steam_id, name, ping, k, a, d, mvps, hsp, score)


def _parse_html(text: str) -> list[SteamMatch]:
    tb = _TreeBuilder()
    tb.feed(text)
    tb.close()
    out = []
    lefts = tb.root.find_all(lambda n: n.has_class("csgo_scoreboard_inner_left"))
    rights = tb.root.find_all(lambda n: n.has_class("csgo_scoreboard_inner_right"))
    if len(lefts) != len(rights):
        raise ValueError(NOT_STEAM)
    for left in lefts:
        # its scoreboard sits in the same root row: exactly one, or the page is not Steam's
        row = left.parent
        while row is not None and row.tag != "tr":
            row = row.parent
        found = [] if row is None else row.find_all(
            lambda n: n.has_class("csgo_scoreboard_inner_right"))
        if len(found) != 1:
            raise ValueError(NOT_STEAM)
        right = found[0]
        lines = [" ".join(c.text().split()) for r in _rows(left) for c in _cells(r)]
        lines = [x for x in lines if x]
        mode, mp = _mode_map(lines[0])
        date = next(DATE_RE.search(x).group(0) for x in lines if DATE_RE.search(x))
        wait = next((x.split(":", 1)[1] for x in lines if x.startswith("Wait Time:")), None)
        dur = next((x.split(":", 1)[1] for x in lines if x.startswith("Match Duration:")), None)
        replay = bool(left.find_all(lambda n: n.has_class("csgo_scoreboard_btn_gotv"))) or any(
            "Download GOTV Replay" in x for x in lines)
        teams: list[list[SteamPlayer]] = [[]]
        score = None
        for r in _rows(right):
            sc = ([r] if r.has_class("csgo_scoreboard_score") else []) + r.find_all(
                lambda n: n.has_class("csgo_scoreboard_score"))
            if sc:
                score = _score(sc[0].text())
                teams.append([])
            elif r.find_all(lambda n: n.tag == "td" and n.has_class("inner_name")):
                # the header row ("Player Name", "Ping", ...) uses <th class="inner_name">
                teams[-1].append(_html_player(r))
        if score is None or len(teams) != 2:
            raise ValueError(NOT_STEAM)
        out.append(SteamMatch(mode=mode, map=mp, started_at=_date(date),
                              wait_seconds=_seconds(wait), duration=_seconds(dur), replay=replay,
                              score=score, teams=(_keep(teams[0]), _keep(teams[1]))))
    return out


def parse(data: bytes, filename: str = "") -> list[SteamMatch]:
    """Parse a Steam history JSON export (format 1) or a saved Personal Game Data page,
    told apart by content (the filename is not trusted). Steam's page with no matches in it:
    ValueError(EMPTY_PAGE); anything else: ValueError(NOT_STEAM)."""
    text = data.decode("utf-8", errors="replace").lstrip("\ufeff")
    head = text.lstrip()
    try:
        if head.startswith("{"):
            return _parse_json(json.loads(head))
        if "csgo_scoreboard_inner_left" in text:
            return _parse_html(text)
    except (ValueError, KeyError, TypeError, IndexError, StopIteration, AttributeError):
        raise ValueError(NOT_STEAM) from None
    if PAGE_MARKER in text:  # Steam's page, but saved with no matches in it
        raise ValueError(EMPTY_PAGE)
    raise ValueError(NOT_STEAM)


# ---------- import ----------

def _me_side(m: SteamMatch, me: str) -> int | None:
    for i, team in enumerate(m.teams):
        if any(p.steam_id == me for p in team):
            return i
    return None


def _existing(conn: sqlite3.Connection, started: datetime, a: int, b: int) -> list[tuple]:
    """Matches of any source starting within ±MATCH_WINDOW_S whose score agrees in some
    orientation (unknown scores don't rule a row out), closest first."""
    lo = db._utc_iso(started - timedelta(seconds=MATCH_WINDOW_S))
    hi = db._utc_iso(started + timedelta(seconds=MATCH_WINDOW_S))
    rows = conn.execute(
        "SELECT id, source, started_at FROM match WHERE started_at IS NOT NULL "
        "AND started_at BETWEEN ? AND ? "
        "AND (my_team_score IS NULL OR enemy_score IS NULL "
        "     OR (my_team_score=? AND enemy_score=?) OR (my_team_score=? AND enemy_score=?))",
        (lo, hi, a, b, b, a)).fetchall()
    return sorted(rows, key=lambda r: (abs((datetime.fromisoformat(r[2]) - started)
                                           .total_seconds()), r[0]))


def _leetify_day_row(conn, m: SteamMatch, mine: SteamPlayer, my_score, me: str) -> int | None:
    """A Leetify sheet row with no start time (it sits at 00:00:00) for the same day: same
    map, my score and my K/D. Only a single fit counts."""
    if mine.kills is None or mine.deaths is None:
        return None
    cands = db.leetify_candidates(conn, map_name(m.map), my_score, mine.kills, mine.deaths,
                                  m.started_at, days=1, me=me)
    cands = [c for c in cands if conn.execute(
        "SELECT started_at FROM match WHERE id=?", (c,)).fetchone()[0].endswith("T00:00:00+00:00")]
    return cands[0] if len(cands) == 1 else None


def _has_scoreboard(conn, match_id: int) -> bool:
    return conn.execute("SELECT 1 FROM match_player WHERE match_id=? AND scoreboard_score "
                        "IS NOT NULL LIMIT 1", (match_id,)).fetchone() is not None


def _account(conn, p: SteamPlayer) -> None:
    """Add the account; an existing name is kept (Steam history is older than the demos)."""
    conn.execute("INSERT OR IGNORE INTO account(steam_id, last_seen_name) VALUES (?, ?)",
                 (p.steam_id, p.name))
    conn.execute("UPDATE account SET last_seen_name=? WHERE steam_id=? AND last_seen_name IS NULL",
                 (p.name, p.steam_id))


def _players(m: SteamMatch, side: int):
    """(player, team, won) for every player; team from my side, won from Steam's score."""
    mine_s, enemy_s = m.score[side], m.score[1 - side]
    for i, team in enumerate(m.teams):
        is_mine = i == side
        won = mine_s > enemy_s if is_mine else enemy_s > mine_s
        for p in team:
            yield p, "mine" if is_mine else "enemy", won


def _write_players(conn, match_id: int, m: SteamMatch, side: int) -> None:
    for p, team, won in _players(m, side):
        _account(conn, p)
        cur = conn.execute(
            "UPDATE match_player SET mvps=?, hs_percent=?, scoreboard_score=?, "
            "kills=COALESCE(kills, ?), assists=COALESCE(assists, ?), deaths=COALESCE(deaths, ?), "
            "name_in_match=COALESCE(name_in_match, ?) WHERE match_id=? AND steam_id=?",
            (p.mvps, p.hs_percent, p.score, p.kills, p.assists, p.deaths, p.name, match_id,
             p.steam_id))
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO match_player(match_id, steam_id, name_in_match, team, won, kills, "
                "assists, deaths, mvps, hs_percent, scoreboard_score) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (match_id, p.steam_id, p.name, team, int(won), p.kills, p.assists, p.deaths,
                 p.mvps, p.hs_percent, p.score))


def _attach(conn, match_id: int, m: SteamMatch, side: int, mine: SteamPlayer) -> None:
    """Fill the match's empty fields and add the scoreboard; demo stats stay as they are.
    A Leetify sheet row without a start time (00:00:00) takes Steam's exact start."""
    conn.execute(
        "UPDATE match SET duration=COALESCE(duration, ?), wait_seconds=COALESCE(wait_seconds, ?), "
        "map=COALESCE(map, ?), mode=COALESCE(mode, ?), game_mode=COALESCE(game_mode, ?), "
        "my_kills=COALESCE(my_kills, ?), my_deaths=COALESCE(my_deaths, ?), "
        "started_at=CASE WHEN source='leetify' AND started_at LIKE '%T00:00:00+00:00' "
        "                THEN ? ELSE started_at END, "
        "start_estimated=CASE WHEN source='leetify' AND started_at LIKE '%T00:00:00+00:00' "
        "                THEN 0 ELSE start_estimated END WHERE id=?",
        (m.duration, m.wait_seconds, map_name(m.map), m.mode, GAME_MODES[m.mode], mine.kills, mine.deaths,
         db._utc_iso(m.started_at), match_id))
    _write_players(conn, match_id, m, side)


def _insert(conn, m: SteamMatch, side: int, mine: SteamPlayer) -> int:
    my_s, en_s = m.score[side], m.score[1 - side]
    result = "win" if my_s > en_s else "loss" if my_s < en_s else "tie"
    mid = db.insert_match(conn, db.NewMatch(
        source="steam", map=map_name(m.map), mode=m.mode, started_at=m.started_at,
        duration=m.duration, wait_seconds=m.wait_seconds, game_mode=GAME_MODES[m.mode], my_team_score=my_s, enemy_score=en_s,
        result=result, my_kills=mine.kills, my_deaths=mine.deaths), commit=False)
    _write_players(conn, mid, m, side)
    return mid


def _one(conn, m: SteamMatch, me: str, rep: ImportReport) -> int | None:
    """Import one row; returns the id of the match it inserted or attached to."""
    if m.mode not in GAME_MODES:
        rep.skipped_mode += 1
        return None
    side = _me_side(m, me)
    if side is None:  # not my match (another account's page): nothing to orient it by
        rep.skipped += 1
        return None
    mine = next(p for p in m.teams[side] if p.steam_id == me)
    my_score = (m.score[side], m.score[1 - side])
    found = _existing(conn, m.started_at, *my_score)
    target = found[0][0] if found else _leetify_day_row(conn, m, mine, my_score, me)
    if target is not None:
        source = conn.execute("SELECT source FROM match WHERE id=?", (target,)).fetchone()[0]
        if source == "steam" or _has_scoreboard(conn, target):
            conn.execute("UPDATE match SET game_mode=? WHERE id=? AND game_mode IS NULL",
                         (GAME_MODES[m.mode], target))
            rep.skipped += 1
            return None
        _attach(conn, target, m, side, mine)
        rep.attached += 1
        return target
    mid = _insert(conn, m, side, mine)
    rep.imported += 1
    return mid


def import_matches(conn: sqlite3.Connection, matches: list[SteamMatch], me: str,
                   backup: Path | None = None,
                   progress: Callable[[int, int], None] | None = None) -> ImportReport:
    """Import Steam rows (see module docstring). With backup (a backups folder) the DB is
    backed up first; rows are written in transactions of BATCH matches, and a failure
    rolls back only the batch it happened in."""
    rep = ImportReport()
    total = len(matches)
    if backup is not None and matches:
        db.backup(conn, backup)
    conn.commit()
    for start in range(0, total, BATCH):
        try:
            changed = [mid for m in matches[start:start + BATCH]
                       if (mid := _one(conn, m, str(me), rep)) is not None]
            records.rebuild_for(conn, changed)  # same transaction as the batch
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        if progress is not None:
            progress(min(start + BATCH, total), total)
    return rep
