"""Read a Steam ID from what a user types: the 17-digit number or a profile link.

- the 17-digit Steam64 number (7656119…)
- ``steamcommunity.com/profiles/<17 digits>`` (scheme, ``www.``, slash, query, fragment allowed)
- ``steamcommunity.com/id/<name>``: looked up with ISteamUser/ResolveVanityURL, which needs
  the Web API key. Errors are rebuilt from the status code, never from the exception text
  (it can hold the request URL, and with it the key).
"""
import http.client
import json
import logging
import re
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

log = logging.getLogger(__name__)

RESOLVE_URL = "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/"
TIMEOUT_S = 20

MSG_BAD = "That doesn't look like a Steam ID or profile link."
MSG_NEED_KEY = "Paste the number instead, or add your API key first."
MSG_UNKNOWN = "Steam doesn't know that profile name."
MSG_FORBIDDEN = "Steam didn't accept the API key. Check it in Settings, or paste the number instead."
MSG_FAILED = "Couldn't look up that profile name on Steam. Paste the number instead."

_ID = re.compile(r"^\d{17}$")
_LINK = re.compile(r"^(?:https?://)?(?:www\.)?steamcommunity\.com/(profiles|id)/([^/?#\s]+)",
                   re.IGNORECASE)


class SteamIdError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def parse(text: str, api_key: str | None, opener=urllib.request.urlopen) -> str:
    text = (text or "").strip()
    if _ID.match(text):
        return text
    m = _LINK.match(text)
    if not m:
        raise SteamIdError(MSG_BAD)
    if not re.match(r"^/?(?:[?#].*)?$", text[m.end():]):   # "/id/Some Name" is not "/id/Some"
        raise SteamIdError(MSG_BAD)
    kind, value = m.group(1).lower(), m.group(2)
    if kind == "profiles":
        if _ID.match(value):
            return value
        raise SteamIdError(MSG_BAD)
    if not api_key:
        raise SteamIdError(MSG_NEED_KEY)
    return _resolve(value, api_key, opener)


def needs_key(text: str) -> bool:
    """Whether this input is a /id/<name> link (looked up with the Web API key)."""
    m = _LINK.match((text or "").strip())
    return bool(m) and m.group(1).lower() == "id"


def _resolve(name: str, api_key: str, opener) -> str:
    url = RESOLVE_URL + "?" + urlencode({"key": api_key, "vanityurl": name}, quote_via=quote)
    err = None
    try:
        with opener(url, timeout=TIMEOUT_S) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        status = e.code
        e.close()
        err = SteamIdError(MSG_FORBIDDEN if status in (401, 403) else MSG_FAILED)
    except (OSError, http.client.HTTPException):
        err = SteamIdError(MSG_FAILED)
    if err is not None:  # raised outside the except block: no context, no URL
        log.info("vanity lookup failed")
        raise err
    try:
        r = json.loads(body)["response"]
        success = r.get("success")
    except (ValueError, KeyError, TypeError, AttributeError):
        raise SteamIdError(MSG_FAILED) from None
    if success == 1 and isinstance(r.get("steamid"), str) and _ID.match(r["steamid"]):
        return r["steamid"]
    if success == 42:
        raise SteamIdError(MSG_UNKNOWN)
    raise SteamIdError(MSG_FAILED)
