import re
import shutil
import subprocess

import pytest

from conftest import ROOT

STATIC = ROOT / "app/cs2tracker/static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
BANNER = ("To fetch your matches automatically, add these three from Steam: Web API key, "
          "authentication code, and your most recent match's sharing code (shown on the same "
          "Steam page as the code).")


def js(name):
    return (STATIC / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("text", [
    'id="view-welcome"', "Welcome to CS2 Squad Tracker",
    "Stats, awards and roasts from your CS2 matches, for you and your friends.",
    "Your Steam ID or profile link", "Where do I find this?",
    "On Steam, open your profile and copy the page address.", ">Next<",
    "Want your older matches too?", BANNER, "Skip for now", 'id="owner-notice"'])
def test_html_has_welcome_texts(text):
    assert text in HTML


def test_owner_checked_before_auto_update():
    src = js("app.js")
    start_app = src[src.index("function startApp("):src.index("async function init()")]
    outside = src.replace(start_app, "").replace("async function maybeAutoUpdate(", "")
    assert "maybeAutoUpdate(" in start_app and "maybeAutoUpdate(" not in outside
    body = src[src.index("async function init()"):]
    assert body.index('"/api/owner"') < body.index("startApp(owner)")
    assert "showWelcome(owner); return;" in body


def test_api_sends_428_to_welcome():
    src = js("app.js")
    assert "428" in src and "no_owner" in src


def test_welcome_calls_routes():
    src = js("welcome.js")
    for route in ('"/api/owner"', '"/api/onboarding"', '"/api/owner/notice-seen"'):
        assert route in src


@pytest.mark.parametrize("name", ["welcome.js", "app.js"])
def test_no_html_injection(name):
    assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML", js(name))


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("name", ["app.js", "welcome.js", "rivals.js"])
def test_node_check(name):
    r = subprocess.run(["node", "--check", str(STATIC / name)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
