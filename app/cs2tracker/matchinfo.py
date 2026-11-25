"""Decode CS2 `.dem.info` (CDataGCCStrike15_v2_MatchInfo) and match lists."""
from dataclasses import dataclass
from datetime import datetime, timezone

from cs2tracker import protowire

STEAM64_BASE = 76561197960265728


@dataclass
class InfoPlayer:
    steam_id: str
    kills: int
    assists: int
    deaths: int


@dataclass
class MatchInfo:
    match_id: int
    started_at: datetime  # tz-aware UTC
    scores: tuple[int, int]  # team of players[0:5] first
    players: list[InfoPlayer]
    demo_url: str | None
    raw: bytes

    def my_score(self, me: str) -> tuple[int, int] | None:
        for i, p in enumerate(self.players):
            if p.steam_id == me:
                return self.scores if i < 5 else (self.scores[1], self.scores[0])
        return None


def _ints(fields: list[tuple[int, int, int | bytes]], num: int) -> list[int]:
    """Collect a repeated int field, whether packed or unpacked."""
    out: list[int] = []
    for f, wt, v in fields:
        if f != num:
            continue
        if wt == 2:
            out.extend(protowire.packed_varints(v))  # type: ignore[arg-type]
        else:
            out.append(v)  # type: ignore[arg-type]
    return out


def parse_match_info(buf: bytes) -> MatchInfo:
    top = protowire.parse(buf)
    match_id = next((v for f, _, v in top if f == 1), 0)
    match_time = next((v for f, _, v in top if f == 2), 0)
    rounds = [v for f, wt, v in top if f == 5 and wt == 2]
    if not rounds:
        raise ValueError("match info has no round stats")
    rs = protowire.parse(rounds[-1])  # type: ignore[arg-type]

    account_ids: list[int] = []
    for f, wt, v in rs:
        if f == 2 and wt == 2:
            account_ids = _ints(protowire.parse(v), 1)  # type: ignore[arg-type]
            break

    demo_url = None
    for f, wt, v in rs:
        if f == 3 and wt == 2:
            s = v.decode("utf-8", "replace")  # type: ignore[union-attr]
            if s.startswith("http"):
                demo_url = s
            break

    kills, assists, deaths = _ints(rs, 5), _ints(rs, 6), _ints(rs, 7)
    players = [
        InfoPlayer(
            steam_id=str(aid + STEAM64_BASE),
            kills=kills[i] if i < len(kills) else 0,
            assists=assists[i] if i < len(assists) else 0,
            deaths=deaths[i] if i < len(deaths) else 0,
        )
        for i, aid in enumerate(account_ids)
    ]
    ts = _ints(rs, 12)
    scores = (ts[0], ts[1]) if len(ts) >= 2 else (0, 0)
    return MatchInfo(
        match_id=match_id,  # type: ignore[arg-type]
        started_at=datetime.fromtimestamp(match_time, tz=timezone.utc),  # type: ignore[arg-type]
        scores=scores,
        players=players,
        demo_url=demo_url,
        raw=buf,
    )


def parse_match_list(buf: bytes) -> list[MatchInfo]:
    return [
        parse_match_info(v)  # type: ignore[arg-type]
        for f, wt, v in protowire.parse(buf)
        if f == 4 and wt == 2
    ]
