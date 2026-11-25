"""Saved Steam Personal Game Data pages (bug report 2026-11-25)."""
from pathlib import Path

import pytest

from cs2tracker import steam_history as sh

BASE = sh.STEAM_ID64_BASE
ME, FRIEND, ENEMY = 1000, 1001, 2000


def _player(acc: int, name: str, k: int, mvp: str = "") -> str:
    return (f'<tr><td class="inner_name"><div class="playerAvatar" data-miniprofile="{acc}">'
            f'</div><a class="linkTitle" data-miniprofile="{acc}" href="#">{name}</a></td>'
            f'<td>30</td><td>{k}</td><td>3</td><td>10</td><td>{mvp}</td><td>45%</td>'
            f'<td>{k * 2}</td></tr>')


HEADER = ('<tr><th class="inner_name">Player Name</th><th>Ping</th><th>K</th><th>A</th>'
          '<th>D</th><th>★</th><th>HSP</th><th>Score</th></tr>')


def _match(date: str) -> str:
    return (
        '<tr><td class="val_left"><table class="csgo_scoreboard_inner_left"><tbody>'
        '<tr><td>Premier Dust II</td></tr>'
        f'<tr><td>{date}</td></tr><tr><td>Wait Time: 01:02</td></tr>'
        '<tr><td>Match Duration: 35:10</td></tr></tbody></table></td>'
        '<td><table class="csgo_scoreboard_inner_right"><tbody>'
        + HEADER + _player(ME, "Me", 20, "★3") + _player(FRIEND, "Pal", 12)
        + '<tr><td class="csgo_scoreboard_score" colspan="8">13 : 9</td></tr>'
        + _player(ENEMY, "Them", 15, "★")
        + '</tbody></table></td></tr>')


def _page(*matches: str) -> bytes:
    return ('<html><body><table class="generic_kv_table csgo_scoreboard_root"><tbody>'
            '<tr><th>Map</th><th>Scoreboard</th></tr>' + "".join(matches)
            + '</tbody></table><div id="load_more_button">Load more history</div>'
            '</body></html>').encode()


def test_saved_page_with_header_rows_parses():
    ms = sh.parse(_page(_match("2026-11-20 19:00:00 GMT"), _match("2026-11-21 19:00:00 GMT")))
    assert len(ms) == 2
    m = ms[0]
    assert (m.mode, m.map, m.score) == ("Premier", "Dust II", (13, 9))
    assert [p.name for p in m.teams[0]] == ["Me", "Pal"]
    assert [p.name for p in m.teams[1]] == ["Them"]
    me = m.teams[0][0]
    assert (me.steam_id, me.kills, me.mvps, me.hs_percent) == (str(ME + BASE), 20, 3, 45)
    assert m.teams[1][0].mvps == 1


def test_page_saved_before_matches_loaded():
    with pytest.raises(ValueError) as e:
        sh.parse(_page())
    assert str(e.value) == sh.EMPTY_PAGE


def test_other_html_still_not_steam():
    with pytest.raises(ValueError) as e:
        sh.parse(b"<html><body><table><tr><td>hi</td></tr></table></body></html>")
    assert str(e.value) == sh.NOT_STEAM


def test_readme_says_webpage_complete_only():
    readme = (Path(__file__).resolve().parents[1] / "README.txt").read_text(encoding="utf-8")
    flat = " ".join(readme.split())
    assert 'choose "Webpage, Complete".' in flat
    assert 'or "HTML only"' not in flat
