CREATE TABLE IF NOT EXISTS person (
    id INTEGER PRIMARY KEY,
    display_name TEXT NOT NULL,
    "group" TEXT NULL,
    tracked INTEGER NOT NULL DEFAULT 0,
    notes TEXT NULL
);

CREATE TABLE IF NOT EXISTS account (
    steam_id TEXT PRIMARY KEY,
    person_id INTEGER NULL REFERENCES person(id),
    last_seen_name TEXT NULL
);

CREATE TABLE IF NOT EXISTS match (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL CHECK (source IN ('demo', 'leetify', 'both')),
    leetify_match_id TEXT NULL,
    valve_match_id INTEGER UNIQUE NULL,
    demo_sha256 TEXT NULL,
    demo_path TEXT NULL,
    map TEXT NULL,
    mode TEXT NULL,
    started_at TEXT NULL,                       -- ISO-8601 UTC, YYYY-MM-DDTHH:MM:SS+00:00
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
    imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_match_sha ON match(demo_sha256);
CREATE INDEX IF NOT EXISTS idx_match_leetify ON match(leetify_match_id);
CREATE INDEX IF NOT EXISTS idx_match_started ON match(started_at);

CREATE TABLE IF NOT EXISTS match_player (
    match_id INTEGER NOT NULL REFERENCES match(id) ON DELETE CASCADE,
    steam_id TEXT NOT NULL REFERENCES account(steam_id),
    name_in_match TEXT NULL,
    team TEXT NOT NULL CHECK (team IN ('mine', 'enemy')),
    won INTEGER NOT NULL DEFAULT 0,
    kills INTEGER NULL,
    assists INTEGER NULL,
    deaths INTEGER NULL,
    adr REAL NULL,
    kast REAL NULL,
    k2 INTEGER NULL,
    k3 INTEGER NULL,
    k4 INTEGER NULL,
    k5 INTEGER NULL,
    hs_pct REAL NULL,
    hs_kills INTEGER NULL,
    rounds_played INTEGER NULL,
    hltv_rating REAL NULL,
    leetify_rating REAL NULL,
    PRIMARY KEY (match_id, steam_id)
);
CREATE INDEX IF NOT EXISTS idx_mp_steam ON match_player(steam_id);

CREATE TABLE IF NOT EXISTS kill (
    id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES match(id) ON DELETE CASCADE,
    round INTEGER NOT NULL,
    tick INTEGER NOT NULL,
    attacker_steam_id TEXT NULL,
    victim_steam_id TEXT NOT NULL,
    assister_steam_id TEXT NULL,
    weapon TEXT NULL,
    headshot INTEGER NOT NULL DEFAULT 0,
    wallbang INTEGER NOT NULL DEFAULT 0,
    team_kill INTEGER NOT NULL DEFAULT 0,
    attacker_xyz TEXT NULL,                     -- JSON [x, y, z]
    victim_xyz TEXT NULL,                       -- JSON [x, y, z]
    attacker_side TEXT NULL
);
CREATE INDEX IF NOT EXISTS idx_kill_match ON kill(match_id);

CREATE TABLE IF NOT EXISTS multikill (
    match_id INTEGER NOT NULL REFERENCES match(id) ON DELETE CASCADE,
    steam_id TEXT NOT NULL,
    round INTEGER NOT NULL,
    kills INTEGER NOT NULL,
    first_tick INTEGER NOT NULL,
    last_tick INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_multikill_match ON multikill(match_id);

CREATE TABLE IF NOT EXISTS round (
    match_id INTEGER NOT NULL REFERENCES match(id) ON DELETE CASCADE,
    number INTEGER NOT NULL,
    winner_side TEXT NULL,
    reason TEXT NULL,
    end_tick INTEGER NULL,
    PRIMARY KEY (match_id, number)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS person_leetify_ref (
    person_id INTEGER NOT NULL REFERENCES person(id),
    steam_id TEXT NOT NULL,
    games INTEGER NULL,
    win_pct REAL NULL,
    my_rating REAL NULL,
    their_rating REAL NULL,
    PRIMARY KEY (person_id, steam_id)
);
