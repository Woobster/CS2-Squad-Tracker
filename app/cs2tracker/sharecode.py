"""CS:GO/CS2 match sharing codes (`CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx`).

Same algorithm as akiver/csgo-sharecode (src/match.ts, src/share-code.ts,
doc/code_layout.md): 18 bytes = match id (8, little-endian), reservation/outcome
id (8, little-endian), TV port (2, little-endian; only the low 16 bits are kept).
Those 18 bytes, read as one big-endian number, are written in base 57 with the
alphabet below, least significant character first, 25 characters. No checksum.
"""
import re

from cs2tracker import protowire

ALPHABET = "ABCDEFGHJKLMNOPQRSTUVWXYZabcdefhijkmnopqrstuvwxyz23456789"
_BASE = len(ALPHABET)
_CHARS = 25
_BYTES = 18
_U64 = 1 << 64
_RE = re.compile(rf"CSGO(?:-[{ALPHABET}]{{5}}){{5}}")


def decode(code: str) -> tuple[int, int, int]:
    """(match_id, outcome_id, tv_port). ValueError when the code isn't one."""
    code = code.strip() if isinstance(code, str) else ""
    if not _RE.fullmatch(code):
        raise ValueError("not a match sharing code")
    total = 0
    for ch in reversed(code[5:].replace("-", "")):
        total = total * _BASE + ALPHABET.index(ch)
    if total >= 1 << (8 * _BYTES):
        raise ValueError("not a match sharing code")
    raw = total.to_bytes(_BYTES, "big")
    return (int.from_bytes(raw[0:8], "little"), int.from_bytes(raw[8:16], "little"),
            int.from_bytes(raw[16:18], "little"))


def encode(match_id: int, outcome_id: int, tv_port: int) -> str:
    if not (0 <= match_id < _U64 and 0 <= outcome_id < _U64 and tv_port >= 0):
        raise ValueError("value out of range for a sharing code")
    raw = (match_id.to_bytes(8, "little") + outcome_id.to_bytes(8, "little")
           + (tv_port & 0xFFFF).to_bytes(2, "little"))
    total = int.from_bytes(raw, "big")
    chars = []
    for _ in range(_CHARS):
        total, rem = divmod(total, _BASE)
        chars.append(ALPHABET[rem])
    s = "".join(chars)
    return "CSGO-" + "-".join(s[i:i + 5] for i in range(0, _CHARS, 5))


def from_info(raw: bytes) -> str | None:
    """The sharing code of the match a `.dem.info` (CDataGCCStrike15_v2_MatchInfo)
    describes, or None when the fields aren't there.

    matchid = field 1; watchablematchinfo = field 3, whose tv_port = field 2;
    roundstatsall = field 5 (repeated), whose reservationid = field 1 is set on the
    final entry. Checked against the fixtures: Valve names the demo
    match730_<reservationid>_<tv_port>_<server>.dem.
    """
    try:
        top = protowire.parse(raw)
        match_id = next((v for f, wt, v in top if f == 1 and wt == 0), 0)
        tv_port = None
        for f, wt, v in top:
            if f == 3 and wt == 2:
                tv_port = next((u for g, w, u in protowire.parse(v) if g == 2 and w == 0), None)
        resv = None
        for f, wt, v in top:
            if f == 5 and wt == 2:
                r = next((u for g, w, u in protowire.parse(v) if g == 1 and w == 0), None)
                if r:
                    resv = r
        if not match_id or not resv or tv_port is None:
            return None
        return encode(match_id, resv, tv_port)
    except ValueError:
        return None
