import io
import json
import urllib.error
from urllib.parse import parse_qs, urlparse

import pytest

from cs2tracker import steamid
from cs2tracker.steamid import SteamIdError

ID = "76561197960287930"
KEY = "SECRETKEY1234567890ABCDEF"
NEED_KEY = "Paste the number instead, or add your API key first."
BAD = "That doesn't look like a Steam ID or profile link."
UNKNOWN = "Steam doesn't know that profile name."


class FakeResp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def opener_returning(payload, seen=None):
    def _open(url, timeout=None):
        if seen is not None:
            seen.append(url)
        return FakeResp(json.dumps(payload).encode())
    return _open


def test_bare_id():
    assert steamid.parse(ID, None) == ID


def test_profiles_link_with_noise():
    text = f" https://steamcommunity.com/profiles/{ID}/?l=en "
    assert steamid.parse(text, None) == ID
    assert steamid.parse(f"www.steamcommunity.com/profiles/{ID}#x", None) == ID


def test_vanity_link_resolved_on_steam_api():
    seen = []
    got = steamid.parse("steamcommunity.com/id/gabelogannewell/", KEY,
                        opener=opener_returning({"response": {"success": 1, "steamid": ID}}, seen))
    assert got == ID
    u = urlparse(seen[0])
    assert (u.scheme, u.hostname) == ("https", "api.steampowered.com")
    assert u.path == "/ISteamUser/ResolveVanityURL/v1/"
    assert parse_qs(u.query)["vanityurl"] == ["gabelogannewell"]


def test_vanity_without_key():
    with pytest.raises(SteamIdError) as e:
        steamid.parse("https://steamcommunity.com/id/someone", None)
    assert e.value.message == NEED_KEY


def test_vanity_not_found():
    with pytest.raises(SteamIdError) as e:
        steamid.parse("steamcommunity.com/id/nobody-here", KEY,
                      opener=opener_returning({"response": {"success": 42, "message": "No match"}}))
    assert e.value.message == UNKNOWN


@pytest.mark.parametrize("text", ["hello", "7656119796028793", "", "steamcommunity.com/id/"])
def test_unreadable(text):
    with pytest.raises(SteamIdError) as e:
        steamid.parse(text, KEY)
    assert e.value.message == BAD


def test_http_error_never_leaks_key():
    def boom(url, timeout=None):
        raise urllib.error.HTTPError(url, 403, f"Forbidden {url}", {}, None)
    with pytest.raises(SteamIdError) as e:
        steamid.parse("steamcommunity.com/id/someone", KEY, opener=boom)
    assert KEY not in str(e.value) and KEY not in e.value.message
    assert e.value.__context__ is None and e.value.__cause__ is None


def test_api_sets_owner_from_link(make_app):
    client = make_app()
    r = client.post("/api/owner", json={"input": f"https://steamcommunity.com/profiles/{ID}/"})
    assert r.status_code == 200 and r.json() == {"steam_id": ID}
    assert client.get("/api/owner").json()["status"] == "set"
    assert client.get("/api/people").status_code == 200


def test_api_bad_input(make_app):
    r = make_app().post("/api/owner", json={"input": "hello"})
    assert r.status_code == 400 and r.json()["detail"] == BAD
