"""SQLite schema and repository functions."""
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cs2tracker.demo import KillRow, MultiKill, PlayerLine, RoundRow

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
USER_VERSION = 5
DEFAULT_GROUPS: tuple[str, ...] = ()  # none: users make their own groups
LEETIFY_CUTOFF_DEFAULT = "2026-11-23T08:00:00+00:00"
GROUP_NAME_MAX = 40
BACKUPS_KEPT = 10
NEWER_DATABASE_MESSAGE = ("This data was made by a newer version of CS2 Squad Tracker. "
                          "Please use the newer version.")


@dataclass
class NewMatch:
    source: str  # 'demo' | 'leetify' | 'steam' | 'both'
    leetify_match_id: str | None = None
    valve_match_id: int | None = None
    demo_sha256: str | None = None
    demo_path: str | None = None
    map: str | None = None
    mode: str | None = None
    started_at: datetime | None = None
    start_estimated: bool = False
    duration: int | None = None
    score_ct: int | None = None
    score_t: int | None = None
    my_team_score: int | None = None
    enemy_score: int | None = None
    result: str | None = None
    server: str | None = None
    my_kills: int | None = None
    my_deaths: int | None = None
    info_raw: bytes | None = None
    wait_seconds: int | None = None
    game_mode: str | None = None          # 'competitive' / 'premier'; None = unknown


def _utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


class NewerDatabaseError(Exception):
    """The database was written by a newer version of the app than this one."""

    def __init__(self) -> None:
        super().__init__(NEWER_DATABASE_MESSAGE)


class GroupExistsError(Exception):
    """A group with this name (case-insensitive) already exists."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


def _migrate_1_to_2(conn: sqlite3.Connection) -> None:
    """v1 -> v2: custom groups, new match_player stat columns, leetify cutoff.

    Runs in one transaction; any error rolls everything back (user_version included).
    """
    conn.commit()
    conn.execute("BEGIN")
    try:
        conn.execute("CREATE TABLE team_group(id INTEGER PRIMARY KEY, "
                     "name TEXT NOT NULL UNIQUE COLLATE NOCASE)")
        conn.execute(
            "CREATE TABLE person_group("
            "person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE, "
            "group_id INTEGER NOT NULL REFERENCES team_group(id) ON DELETE CASCADE, "
            "PRIMARY KEY(person_id, group_id))")
        for col in ("utility_dmg", "self_dmg", "knife_kills"):
            conn.execute(f"ALTER TABLE match_player ADD COLUMN {col} INTEGER")
        ids = {name.lower(): create_group(conn, name, commit=False) for name in DEFAULT_GROUPS}

        def gid(name: str) -> int:
            key = name.lower()
            if key not in ids:
                ids[key] = create_group(conn, name, commit=False)
            return ids[key]

        for pid, grp in conn.execute('SELECT id, "group" FROM person').fetchall():
            grp = (grp or "").strip()[:40].strip()   # group names are capped at 40 characters
            if not grp or grp == "None":
                continue
            names = [grp]
            for n in names:
                conn.execute("INSERT OR IGNORE INTO person_group(person_id, group_id) "
                             "VALUES (?, ?)", (pid, gid(n)))
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('leetify_cutoff', ?)",
                     (LEETIFY_CUTOFF_DEFAULT,))
        conn.execute("PRAGMA user_version=2")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _migrate_2_to_3(conn: sqlite3.Connection) -> None:
    """v2 -> v3: match.events_format (NULL = no event bundle). One transaction."""
    conn.commit()
    conn.execute("BEGIN")
    try:
        conn.execute("ALTER TABLE match ADD COLUMN events_format INTEGER NULL")
        conn.execute("PRAGMA user_version=3")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


# The v4 match table: the v3 columns in the same order (events_format came last in v3),
# `source` also allowing 'steam', and the new wait_seconds and game_mode columns at the end.
_V3_MATCH_COLUMNS = (
    "id", "source", "leetify_match_id", "valve_match_id", "demo_sha256", "demo_path", "map",
    "mode", "started_at", "start_estimated", "duration", "score_ct", "score_t",
    "my_team_score", "enemy_score", "result", "server", "my_kills", "my_deaths", "info_raw",
    "imported_at", "events_format")
_V4_MATCH_TABLE = """CREATE TABLE match_v4 (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL CHECK (source IN ('demo', 'leetify', 'steam', 'both')),
    leetify_match_id TEXT NULL,
    valve_match_id INTEGER UNIQUE NULL,
    demo_sha256 TEXT NULL,
    demo_path TEXT NULL,
    map TEXT NULL,
    mode TEXT NULL,
    started_at TEXT NULL,
    start_estimated INTEGER NOT NULL DEFAULT 0,
    duration INTEGER NULL,
    score_ct INTEGER NULL,
    score_t INTEGER NULL,
    my_team_score INTEGER NULL,
    enemy_score INTEGER NULL,
    result TEXT NULL,
    server TEXT NULL,
    my_kills INTEGER NULL,
    my_deaths INTEGER NULL,
    info_raw BLOB NULL,
    imported_at TEXT NOT NULL,
    events_format INTEGER NULL,
    wait_seconds INTEGER NULL,
    game_mode TEXT NULL
)"""
_V4_MATCH_INDEXES = (
    "CREATE INDEX idx_match_sha ON match(demo_sha256)",
    "CREATE INDEX idx_match_leetify ON match(leetify_match_id)",
    "CREATE INDEX idx_match_started ON match(started_at)",
)
_V4_NEW_TABLES = (
    "ALTER TABLE match_player ADD COLUMN mvps INTEGER",
    "ALTER TABLE match_player ADD COLUMN hs_percent INTEGER",
    "ALTER TABLE match_player ADD COLUMN scoreboard_score INTEGER",
    "CREATE TABLE fun_stats("
    "match_id INTEGER NOT NULL REFERENCES match(id) ON DELETE CASCADE, "
    "steam_id TEXT NOT NULL, fun_format INTEGER NOT NULL, data TEXT NOT NULL, "
    "PRIMARY KEY(match_id, steam_id))",
    "CREATE TABLE record("
    "key TEXT NOT NULL, "
    "person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE, "
    "value REAL NOT NULL, "
    "match_id INTEGER REFERENCES match(id) ON DELETE SET NULL, "
    "detail TEXT, achieved_at TEXT, PRIMARY KEY(key, person_id))",
)


def _migrate_3_to_4(conn: sqlite3.Connection) -> None:
    """v3 -> v4: rebuild `match` so source allows 'steam' (keeping every id, row and the
    foreign keys that point at it), add match.wait_seconds and match.game_mode, the match_player scoreboard
    columns, and the fun_stats and record tables (filled by records.rebuild).

    SQLite can't alter a CHECK, so this follows its documented 12-step procedure: foreign
    keys off (outside the transaction, where the pragma takes effect), create the new
    table, copy, drop, rename, recreate the indexes, foreign_key_check, commit, foreign
    keys back on. Any error rolls everything back, user_version included.
    """
    conn.commit()
    fk_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")
        try:
            conn.execute(_V4_MATCH_TABLE)
            cols = ", ".join(_V3_MATCH_COLUMNS)
            conn.execute(f"INSERT INTO match_v4({cols}) SELECT {cols} FROM match")
            conn.execute("DROP TABLE match")
            conn.execute("ALTER TABLE match_v4 RENAME TO match")
            for sql in _V4_MATCH_INDEXES:
                conn.execute(sql)
            for sql in _V4_NEW_TABLES:
                conn.execute(sql)
            bad = conn.execute("PRAGMA foreign_key_check").fetchall()
            if bad:
                raise sqlite3.IntegrityError(f"foreign key check failed after v4 rebuild: {bad[:5]}")
            # fill the new record table from the existing matches (records imports db: a
            # call-time import avoids the cycle)
            from cs2tracker import records
            conn.execute(_COUNTED_VIEW)   # records now read it (v5 keeps it)
            records.rebuild(conn)
            conn.execute("PRAGMA user_version=4")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    finally:
        if fk_on:
            conn.execute("PRAGMA foreign_keys=ON")


# Which matches count anywhere in the app's stats: Competitive, Premier and FACEIT
# (spec 2026-11-26-counted-modes). Import and housekeeping queries ignore this.
COUNTED_GAME_MODES = ("competitive", "premier")
COUNTED_MODE_TEXTS = ("competitive", "premier", "matchmaking", "faceit")
COUNTED_SQL = "SELECT id FROM counted_match"
_COUNTED_VIEW = f"""CREATE VIEW IF NOT EXISTS counted_match AS
SELECT m.id FROM match m WHERE CASE
  WHEN m.game_mode IS NOT NULL THEN m.game_mode IN ({",".join(repr(x) for x in COUNTED_GAME_MODES)})
  WHEN TRIM(COALESCE(m.mode, '')) <> ''
    THEN LOWER(TRIM(m.mode)) IN ({",".join(repr(x) for x in COUNTED_MODE_TEXTS)})
  ELSE (SELECT CASE WHEN COALESCE(SUM(team = 'enemy'), 0) = 0 THEN 1
               ELSE SUM(team = 'mine') BETWEEN 4 AND 5 AND SUM(team = 'enemy') BETWEEN 4 AND 5 END
        FROM match_player mp WHERE mp.match_id = m.id)
END"""


def counted(conn: sqlite3.Connection, match_id: int) -> bool:
    """True when the match counts in stats (Competitive / Premier / FACEIT or unknown 5v5)."""
    return conn.execute("SELECT 1 FROM counted_match WHERE id=?", (match_id,)).fetchone() is not None


def _migrate_4_to_5(conn: sqlite3.Connection) -> None:
    """v4 -> v5: the counted_match view, then rebuild records so other modes' records go."""
    from cs2tracker import records  # records imports db
    try:
        conn.execute(_COUNTED_VIEW)
        conn.execute("PRAGMA user_version=5")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    records.rebuild(conn)
    conn.commit()


def migrate(conn: sqlite3.Connection, backups_dir: Path | None = None) -> None:
    """Bring the database to USER_VERSION. 0 = new database; refuse versions we don't know."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > USER_VERSION:
        raise NewerDatabaseError()
    if version == 0:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA user_version=1")
        conn.commit()
    elif version < 2 and backups_dir is not None:
        backup(conn, backups_dir, prefix="cs2tracker-pre-v2")  # existing data: back up before the v2 step
    if version < 2:
        _migrate_1_to_2(conn)
    if 0 < version < 3 and backups_dir is not None:
        backup(conn, backups_dir, prefix="cs2tracker-pre-v3")  # existing data: back up before the v3 step
    if version < 3:
        _migrate_2_to_3(conn)
    if 0 < version < 4 and backups_dir is not None:
        backup(conn, backups_dir, prefix="cs2tracker-pre-v4")  # existing data: back up before the v4 step
    if version < 4:
        _migrate_3_to_4(conn)
    if 0 < version < 5 and backups_dir is not None:
        backup(conn, backups_dir, prefix="cs2tracker-pre-v5")  # existing data: back up before the v5 step
    if version < 5:
        _migrate_4_to_5(conn)


def connect(path: Path | str, backups_dir: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        migrate(conn, backups_dir)
    except BaseException:
        conn.close()
        raise
    return conn


def backup(conn: sqlite3.Connection, backups_dir: Path, prefix: str = "cs2tracker") -> Path:
    """Copy the database to backups_dir. Only the plain `cs2tracker-*` backups are pruned
    to BACKUPS_KEPT; a custom prefix (e.g. the pre-migration one) is never pruned."""
    backups_dir = Path(backups_dir)
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%f")
    dest = backups_dir / f"{prefix}-{stamp}.db"
    out = sqlite3.connect(str(dest))
    try:
        conn.backup(out)
    finally:
        out.close()
    if prefix == "cs2tracker":
        old = sorted(p for p in backups_dir.glob("cs2tracker-*.db")
                     if not p.name.startswith("cs2tracker-pre-"))[:-BACKUPS_KEPT]
        for p in old:
            p.unlink()
    return dest


def upsert_account(conn: sqlite3.Connection, steam_id: str, name: str,
                   commit: bool = True) -> None:
    conn.execute(
        "INSERT INTO account(steam_id, last_seen_name) VALUES (?, ?) "
        "ON CONFLICT(steam_id) DO UPDATE SET last_seen_name=excluded.last_seen_name",
        (str(steam_id), name),
    )
    if commit:
        conn.commit()


def _clean_group_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("Group name can't be empty")
    if len(name) > GROUP_NAME_MAX:
        raise ValueError(f"Group name is too long (max {GROUP_NAME_MAX})")
    return name


def list_groups(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT g.id, g.name, COUNT(pg.person_id) FROM team_group g "
        "LEFT JOIN person_group pg ON pg.group_id=g.id GROUP BY g.id "
        "ORDER BY g.name COLLATE NOCASE, g.id").fetchall()
    return [{"id": i, "name": n, "members": m} for i, n, m in rows]


def create_group(conn: sqlite3.Connection, name: str, commit: bool = True) -> int:
    name = _clean_group_name(name)
    try:
        cur = conn.execute("INSERT INTO team_group(name) VALUES (?)", (name,))
    except sqlite3.IntegrityError:
        raise GroupExistsError(name) from None
    if commit:
        conn.commit()
    return cur.lastrowid


def rename_group(conn: sqlite3.Connection, group_id: int, name: str) -> bool:
    name = _clean_group_name(name)
    try:
        cur = conn.execute("UPDATE team_group SET name=? WHERE id=?", (name, group_id))
    except sqlite3.IntegrityError:
        conn.rollback()
        raise GroupExistsError(name) from None
    conn.commit()
    return cur.rowcount > 0


def delete_group(conn: sqlite3.Connection, group_id: int) -> bool:
    conn.execute("DELETE FROM person_group WHERE group_id=?", (group_id,))
    cur = conn.execute("DELETE FROM team_group WHERE id=?", (group_id,))
    conn.commit()
    return cur.rowcount > 0


def set_person_groups(conn: sqlite3.Connection, person_id: int, group_ids: list[int],
                      commit: bool = True) -> None:
    ids = list(dict.fromkeys(group_ids))
    known = {r[0] for r in conn.execute("SELECT id FROM team_group")}
    unknown = [g for g in ids if g not in known]
    if unknown:
        raise ValueError(f"Unknown group id {unknown[0]}")
    conn.execute("DELETE FROM person_group WHERE person_id=?", (person_id,))
    conn.executemany("INSERT INTO person_group(person_id, group_id) VALUES (?, ?)",
                     [(person_id, g) for g in ids])
    if commit:
        conn.commit()


def person_groups(conn: sqlite3.Connection) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for pid, gid, name in conn.execute(
            "SELECT pg.person_id, g.id, g.name FROM person_group pg "
            "JOIN team_group g ON g.id=pg.group_id ORDER BY g.name COLLATE NOCASE, g.id"):
        out.setdefault(pid, []).append({"id": gid, "name": name})
    return out


def create_person(conn: sqlite3.Connection, name: str, tracked: bool = False,
                  commit: bool = True, group_ids: list[int] | None = None) -> int:
    cur = conn.execute("INSERT INTO person(display_name, tracked) VALUES (?, ?)",
                       (name, int(tracked)))
    if group_ids:
        try:
            set_person_groups(conn, cur.lastrowid, group_ids, commit=False)
        except ValueError:
            if commit:
                conn.rollback()
            raise
    if commit:
        conn.commit()
    return cur.lastrowid


def link_account(conn: sqlite3.Connection, steam_id: str, person_id: int,
                 commit: bool = True) -> None:
    conn.execute("UPDATE account SET person_id=? WHERE steam_id=?", (person_id, steam_id))
    if commit:
        conn.commit()


def insert_match_player_rows(conn: sqlite3.Connection, match_id: int, rows: list[dict],
                             commit: bool = True) -> None:
    """Insert raw match_player dicts (keys: steam_id, name, team, won, and optional stats)."""
    cols = ("kills", "assists", "deaths", "adr", "kast", "k2", "k3", "k4", "k5",
            "hltv_rating", "leetify_rating", "mvps", "hs_percent", "scoreboard_score")
    for r in rows:
        upsert_account(conn, r["steam_id"], r.get("name"), commit=False)
        conn.execute(
            "INSERT INTO match_player(match_id, steam_id, name_in_match, team, won, "
            + ", ".join(cols) + ") VALUES (?,?,?,?,?," + ",".join("?" * len(cols)) + ")",
            (match_id, r["steam_id"], r.get("name"), r["team"], int(r["won"]),
             *(r.get(c) for c in cols)))
    if commit:
        conn.commit()


def insert_match(conn: sqlite3.Connection, m: NewMatch, commit: bool = True) -> int:
    cur = conn.execute(
        "INSERT INTO match(source, leetify_match_id, valve_match_id, demo_sha256, demo_path, map, "
        "mode, started_at, start_estimated, duration, score_ct, score_t, my_team_score, "
        "enemy_score, result, server, my_kills, my_deaths, info_raw, imported_at, wait_seconds, game_mode) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (m.source, m.leetify_match_id, m.valve_match_id, m.demo_sha256, m.demo_path, m.map,
         m.mode, _utc_iso(m.started_at) if m.started_at else None, int(m.start_estimated),
         m.duration, m.score_ct, m.score_t, m.my_team_score, m.enemy_score, m.result, m.server,
         m.my_kills, m.my_deaths, m.info_raw, _utc_iso(datetime.now(timezone.utc)),
         m.wait_seconds, m.game_mode),
    )
    if commit:
        conn.commit()
    return cur.lastrowid


def get_match(conn: sqlite3.Connection, match_id: int) -> dict | None:
    cur = conn.execute("SELECT * FROM match WHERE id=?", (match_id,))
    row = cur.fetchone()
    if row is None:
        return None
    d = dict(zip([c[0] for c in cur.description], row))
    d["started_at"] = _parse_dt(d["started_at"])
    d["imported_at"] = _parse_dt(d["imported_at"])
    d["start_estimated"] = bool(d["start_estimated"])
    return d


def delete_match(conn: sqlite3.Connection, match_id: int,
                 events_dir: Path | None = None) -> bool:
    """Delete a match and (by FK cascade) its players, kills, rounds and multikills.
    The records of the people on my team in it, or holding a record from it, are rebuilt
    in the same transaction. With events_dir, also remove its event bundle (a missing file
    is fine)."""
    from cs2tracker import records  # records imports db
    pids = sorted({r[0] for r in conn.execute(
        "SELECT a.person_id FROM match_player mp JOIN account a ON a.steam_id=mp.steam_id "
        "WHERE mp.match_id=? AND mp.team='mine' AND a.person_id IS NOT NULL "
        "UNION SELECT person_id FROM record WHERE match_id=?", (match_id, match_id))})
    try:
        cur = conn.execute("DELETE FROM match WHERE id=?", (match_id,))
        records.rebuild(conn, pids)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    if events_dir is not None:
        (Path(events_dir) / f"{match_id}.json.gz").unlink(missing_ok=True)
    return cur.rowcount > 0


def get_match_players(conn: sqlite3.Connection, match_id: int) -> list[dict]:
    cur = conn.execute("SELECT * FROM match_player WHERE match_id=? ORDER BY team, steam_id",
                       (match_id,))
    cols = [c[0] for c in cur.description]
    out = []
    for row in cur.fetchall():
        d = dict(zip(cols, row))
        d["won"] = bool(d["won"])
        out.append(d)
    return out


def replace_match_players(conn: sqlite3.Connection, match_id: int, lines: list[PlayerLine],
                          me: str, commit: bool = True) -> None:
    m = conn.execute("SELECT my_team_score, enemy_score FROM match WHERE id=?",
                     (match_id,)).fetchone()
    if m is None:
        raise sqlite3.IntegrityError(f"no such match {match_id}")
    mine_score, enemy_score = m
    scored = mine_score is not None and enemy_score is not None
    my_letter = next((p.team for p in lines if p.steam_id == me), None)
    try:
        conn.execute("DELETE FROM match_player WHERE match_id=?", (match_id,))
        for p in lines:
            conn.execute(
                "INSERT INTO account(steam_id, last_seen_name) VALUES (?, ?) "
                "ON CONFLICT(steam_id) DO UPDATE SET last_seen_name=excluded.last_seen_name",
                (p.steam_id, p.name))
            mine = my_letter is not None and p.team == my_letter
            if not scored:
                won = False
            elif mine:
                won = mine_score > enemy_score
            else:
                won = my_letter is not None and enemy_score > mine_score
            hs_pct = round(100.0 * p.hs_kills / p.kills, 1) if p.kills else None
            conn.execute(
                "INSERT INTO match_player(match_id, steam_id, name_in_match, team, won, kills, "
                "assists, deaths, adr, k2, k3, k4, k5, hs_pct, hs_kills, rounds_played, "
                "utility_dmg, self_dmg, knife_kills) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (match_id, p.steam_id, p.name, "mine" if mine else "enemy", int(won), p.kills,
                 p.assists, p.deaths, p.adr, p.k2, p.k3, p.k4, p.k5, hs_pct, p.hs_kills,
                 p.rounds_played, p.utility_dmg, p.self_dmg, p.knife_kills))
        if commit:
            conn.commit()
    except Exception:
        if commit:
            conn.rollback()
        raise


def _xyz(v) -> str | None:
    return None if v is None else json.dumps(list(v))


def insert_kills(conn: sqlite3.Connection, match_id: int, kills: list[KillRow],
                 commit: bool = True) -> None:
    conn.executemany(
        "INSERT INTO kill(match_id, round, tick, attacker_steam_id, victim_steam_id, "
        "assister_steam_id, weapon, headshot, wallbang, team_kill, attacker_xyz, victim_xyz) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(match_id, k.round, k.tick, k.attacker, k.victim, k.assister, k.weapon, int(k.headshot),
          int(k.wallbang), int(k.team_kill), _xyz(k.attacker_xyz), _xyz(k.victim_xyz))
         for k in kills])
    if commit:
        conn.commit()


def insert_rounds(conn: sqlite3.Connection, match_id: int, rounds: list[RoundRow],
                  commit: bool = True) -> None:
    conn.executemany(
        "INSERT INTO round(match_id, number, winner_side, reason, end_tick) VALUES (?,?,?,?,?)",
        [(match_id, r.number, r.winner_side, r.reason, r.end_tick) for r in rounds])
    if commit:
        conn.commit()


def insert_multikills(conn: sqlite3.Connection, match_id: int, mks: list[MultiKill],
                      commit: bool = True) -> None:
    conn.executemany(
        "INSERT INTO multikill(match_id, steam_id, round, kills, first_tick, last_tick) "
        "VALUES (?,?,?,?,?,?)",
        [(match_id, m.steam_id, m.round, m.kills, m.first_tick, m.last_tick) for m in mks])
    if commit:
        conn.commit()


def find_match_by_valve_id(conn: sqlite3.Connection, valve_id: int) -> int | None:
    r = conn.execute("SELECT id FROM match WHERE valve_match_id=?", (valve_id,)).fetchone()
    return r[0] if r else None


def summary_match_by_valve_id(conn: sqlite3.Connection, valve_id: int) -> int | None:
    """The match with this Valve id if it is a summary row (leetify/steam) with no demo."""
    r = conn.execute(
        "SELECT id FROM match WHERE valve_match_id=? AND source IN ('leetify', 'steam') "
        "AND demo_path IS NULL AND demo_sha256 IS NULL", (valve_id,)).fetchone()
    return r[0] if r else None


def demo_match_by_valve_id(conn: sqlite3.Connection, valve_id: int) -> int | None:
    """The match with this Valve id unless it is a summary row still waiting for its demo:
    such a row must not stop the demo from being downloaded or imported."""
    known = find_match_by_valve_id(conn, valve_id)
    if known is None or summary_match_by_valve_id(conn, valve_id) is not None:
        return None
    return known


def summary_match_candidates(conn: sqlite3.Connection, started_at: datetime,
                             my_score: tuple[int, int] | None, window_s: int = 120) -> list[int]:
    """Summary matches (source leetify or steam, no demo yet) starting within +-window_s of
    started_at whose score agrees with my_score in either orientation. When either side's
    score is unknown (my_score None, or the row's scores NULL) the score doesn't rule it out."""
    from datetime import timedelta
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    lo = _utc_iso(started_at - timedelta(seconds=window_s))
    hi = _utc_iso(started_at + timedelta(seconds=window_s))
    a, b = my_score if my_score is not None else (None, None)
    rows = conn.execute(
        "SELECT id FROM match WHERE source IN ('leetify', 'steam') "
        "AND demo_path IS NULL AND demo_sha256 IS NULL "
        "AND started_at IS NOT NULL AND started_at BETWEEN ? AND ? "
        "AND (? IS NULL OR ? IS NULL OR my_team_score IS NULL OR enemy_score IS NULL "
        "     OR (my_team_score=? AND enemy_score=?) OR (my_team_score=? AND enemy_score=?)) "
        "ORDER BY id",
        (lo, hi, a, b, a, b, b, a)).fetchall()
    return [r[0] for r in rows]


def find_match_by_sha(conn: sqlite3.Connection, sha: str) -> int | None:
    r = conn.execute("SELECT id FROM match WHERE demo_sha256=? ORDER BY id LIMIT 1",
                     (sha,)).fetchone()
    return r[0] if r else None


def leetify_candidates(conn: sqlite3.Connection, map: str | None, my_score: tuple[int, int],
                       my_k: int, my_d: int, around: datetime, days: int = 3, *,
                       ignore_map: bool = False, me: str,
                       sources: tuple[str, ...] = ("leetify",)) -> list[int]:
    """Summary matches (sources; Leetify-only by default) with no demo yet that fit my
    score and my K/D within +-days of around.

    My K/D is matched against match.my_kills/my_deaths or against my own match_player row
    (steam_id == me), never against a teammate's row. ignore_map skips the map check.
    """
    from datetime import timedelta
    if ignore_map:
        map = None
    if around.tzinfo is None:
        around = around.replace(tzinfo=timezone.utc)
    lo = _utc_iso(around - timedelta(days=days))
    hi = _utc_iso(around + timedelta(days=days))
    rows = conn.execute(
        f"SELECT m.id FROM match m WHERE m.source IN ({','.join('?' * len(sources))}) "
        "AND m.demo_path IS NULL AND m.demo_sha256 IS NULL "
        "AND (? IS NULL OR m.map IS NULL OR m.map=?) "
        "AND m.my_team_score=? AND m.enemy_score=? "
        "AND m.started_at IS NOT NULL AND m.started_at BETWEEN ? AND ? "
        "AND ((m.my_kills=? AND m.my_deaths=?) OR EXISTS ("
        "  SELECT 1 FROM match_player p WHERE p.match_id=m.id AND p.steam_id=? "
        "  AND p.kills=? AND p.deaths=?)) ORDER BY m.id",
        (*sources, map, map, my_score[0], my_score[1], lo, hi, my_k, my_d, str(me), my_k, my_d)
    ).fetchall()
    return [r[0] for r in rows]


def find_leetify_by_time(conn: sqlite3.Connection, started_at: datetime,
                         my_score: tuple[int, int], minutes: int = 2) -> list[int]:
    from datetime import timedelta
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    lo = _utc_iso(started_at - timedelta(minutes=minutes))
    hi = _utc_iso(started_at + timedelta(minutes=minutes))
    rows = conn.execute(
        "SELECT id FROM match WHERE source='leetify' AND my_team_score=? AND enemy_score=? "
        "AND started_at IS NOT NULL AND started_at BETWEEN ? AND ? ORDER BY id",
        (my_score[0], my_score[1], lo, hi)).fetchall()
    return [r[0] for r in rows]


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO settings(key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()


def settings_defaults() -> dict[str, str]:
    """Values to use for settings keys that are absent from the settings table."""
    return {"auto_update": "1"}
