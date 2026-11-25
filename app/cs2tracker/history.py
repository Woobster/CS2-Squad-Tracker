"""Walk a player's match history with Steam's GetNextMatchSharingCode Web API.

Confirmed on https://developer.valvesoftware.com/wiki/Counter-Strike:_Global_Offensive_Access_Match_History
(read 2026-11-24):

    GET https://api.steampowered.com/ICSGOPlayers_730/GetNextMatchSharingCode/v1
        ?key=<Web API key>&steamid=<SteamID64>&steamidkey=<game authentication code>
        &knowncode=<a sharing code of one of the user's matches>

    200 OK        {"result": {"nextcode": "CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx"}}  (the next match)
    202 Accepted  {"result": {"nextcode": "n/a"}}  (knowncode is the newest match: nothing newer)
    403 Forbidden            the steamidkey (auth code) isn't valid for this account/key
    412 Precondition Failed  the knowncode is invalid or not this user's; stop until it's fixed
    429 Too Many Requests    rate limited
    503 Service Unavailable  strict rate limiting / too many invalid steamidkey requests
    500, 504                 server failure / backend timeout: retry later

"n/a" is treated as the end whatever the status. Everything except 403 and 412 stops
the walk with a generic "try again next Update" message; no HTTP answer at all
(no connection, timeout) gives "Couldn't reach Steam's match history service...".

The request URL carries the key and the auth code, so it is never logged, put in an
exception or returned; errors are built from status codes only and never chained.
"""
import http.client
import json
import logging
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

from cs2tracker import sharecode

log = logging.getLogger(__name__)

API_URL = "https://api.steampowered.com/ICSGOPlayers_730/GetNextMatchSharingCode/v1"
MAX_CODES = 50
CALL_GAP_S = 1.0
TIMEOUT_S = 20
NO_STATUS = 0  # no HTTP answer at all (no connection, timeout)

MSG_FORBIDDEN = "Steam didn't accept the API key or authentication code. Check them in Settings."
MSG_BAD_CODE = "The match sharing code isn't valid for this account."
MSG_GENERIC = "Steam's match history service didn't answer (HTTP {status}). Will try again next Update."
MSG_UNREACHABLE = "Couldn't reach Steam's match history service. Will try again next Update."


class HistoryError(Exception):
    def __init__(self, message: str, status: int = NO_STATUS):
        super().__init__(message)
        self.message = message
        self.status = status

    @classmethod
    def for_status(cls, status: int) -> "HistoryError":
        if status == 403:
            return cls(MSG_FORBIDDEN, status)
        if status == 412:
            return cls(MSG_BAD_CODE, status)
        if status == NO_STATUS:
            return cls(MSG_UNREACHABLE, status)
        return cls(MSG_GENERIC.format(status=status), status)


def _ask(api_key: str, steam_id: str, auth_code: str, known_code: str, opener) -> str | None:
    """The next code, or None when there's no newer match. Raises HistoryError."""
    url = API_URL + "?" + urlencode({"key": api_key, "steamid": steam_id,
                                     "steamidkey": auth_code, "knowncode": known_code})
    err = None
    try:
        with opener(url, timeout=TIMEOUT_S) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            body = resp.read()
    except urllib.error.HTTPError as e:
        status = e.code
        e.close()
        err = HistoryError.for_status(status)
    # URLError and socket timeouts/resets are OSErrors; a truncated or garbled answer
    # (IncompleteRead, BadStatusLine, LineTooLong) is an HTTPException: all unreachable.
    except (OSError, http.client.HTTPException):
        err = HistoryError.for_status(NO_STATUS)
    if err is not None:  # raised outside the except block: no context, no URL
        log.info("match history: HTTP %s", err.status)
        raise err
    try:
        nextcode = json.loads(body)["result"]["nextcode"]
    except (ValueError, KeyError, TypeError):
        nextcode = None
    if nextcode == "n/a":
        log.info("match history: HTTP %s, no newer match", status)
        return None
    if status != 200 or not isinstance(nextcode, str):
        log.info("match history: unexpected answer, HTTP %s", status)
        raise HistoryError.for_status(status)
    try:
        sharecode.decode(nextcode)
    except ValueError:
        log.info("match history: malformed code, HTTP %s", status)
        raise HistoryError.for_status(status) from None
    return nextcode.strip()


def next_codes(api_key: str, steam_id: str, auth_code: str, known_code: str,
               limit: int = MAX_CODES, opener=urllib.request.urlopen,
               sleep=time.sleep) -> tuple[list[str], HistoryError | None]:
    """The sharing codes after known_code, oldest first, at most `limit`, and the
    error that stopped the walk (None when it reached the newest match or the limit)."""
    codes: list[str] = []
    current = known_code
    while len(codes) < limit:
        if codes:
            sleep(CALL_GAP_S)
        try:
            nxt = _ask(api_key, steam_id, auth_code, current, opener)
        except HistoryError as e:
            e.__traceback__ = None  # drop frames holding the key and URL
            return codes, e
        if nxt is None or nxt == current or nxt in codes:
            break
        codes.append(nxt)
        current = nxt
    return codes, None


def test_access(api_key: str, steam_id: str, auth_code: str, known_code: str,
                opener=urllib.request.urlopen) -> str | None:
    """One call. None when it works, otherwise the readable message."""
    try:
        _ask(api_key, steam_id, auth_code, known_code, opener)
    except HistoryError as e:
        e.__traceback__ = None
        return e.message
    return None


test_access.__test__ = False  # not a pytest test
