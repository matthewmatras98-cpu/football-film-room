"""Odds client query shape and the rule that the key never rides along in errors."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

from football_pipeline.odds import BOOKMAKERS, OddsClient
from football_pipeline.schema import PipelineError

SENTINEL = "SENTINEL_KEY_do_not_leak_9f3a"


def test_odds_and_events_queries_use_params_and_log_only_usage_headers(monkeypatch, capsys):
    seen = []

    def fake(request, timeout=30):
        seen.append(request.full_url)
        if request.full_url.endswith("/odds") or "/odds?" in request.full_url:
            body = b"[]"
            last = "2"
        else:
            body = b"[]"
            last = "0"
        headers = {
            "X-Requests-Remaining": "8",
            "X-Requests-Used": "2",
            "X-Requests-Last": last,
        }
        return _Response(body, headers)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    client = OddsClient(SENTINEL)
    events, _headers = client.get_events()
    odds, _headers = client.get_odds()
    assert events == []
    assert odds == []
    event_query = urllib.parse.parse_qs(urllib.parse.urlsplit(seen[0]).query)
    odds_query = urllib.parse.parse_qs(urllib.parse.urlsplit(seen[1]).query)
    assert "/v4/sports/americanfootball_nfl/events" in seen[0]
    assert event_query["dateFormat"] == ["iso"]
    assert event_query["apiKey"] == [SENTINEL]
    assert "/v4/sports/americanfootball_nfl/odds" in seen[1]
    assert odds_query["markets"] == ["spreads,totals"]
    assert odds_query["bookmakers"] == [",".join(BOOKMAKERS)]
    assert odds_query["oddsFormat"] == ["american"]
    assert odds_query["dateFormat"] == ["iso"]
    assert odds_query["apiKey"] == [SENTINEL]
    logged = capsys.readouterr()
    assert SENTINEL not in logged.out
    assert SENTINEL not in logged.err
    assert "api.the-odds-api.com" not in logged.out
    assert "x-requests-remaining=8" in logged.out
    assert "x-requests-used=2" in logged.out
    assert "x-requests-last=0" in logged.out
    assert "x-requests-last=2" in logged.out


def test_http_and_network_errors_do_not_carry_the_url(monkeypatch):
    def http_error(request, timeout=30):
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "unauthorized",
            hdrs=None,
            fp=io.BytesIO(b"apiKey leak"),
        )

    monkeypatch.setattr(urllib.request, "urlopen", http_error)
    with pytest.raises(PipelineError) as caught:
        OddsClient(SENTINEL).get_odds()
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    text = str(caught.value)
    assert text == "HTTP 401 from odds"
    assert SENTINEL not in text

    def network_error(request, timeout=30):
        raise urllib.error.URLError(request.full_url)

    monkeypatch.setattr(urllib.request, "urlopen", network_error)
    with pytest.raises(PipelineError) as caught:
        OddsClient(SENTINEL).get_events()
    assert str(caught.value) == "request failed for events"
    assert SENTINEL not in str(caught.value)
    assert caught.value.__context__ is None


class _Response:
    def __init__(self, body: bytes, headers: dict[str, str], status: int = 200):
        self._body = body
        self.headers = headers
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_invalid_payload_does_not_echo_the_body(monkeypatch):
    def fake(request, timeout=30):
        return _Response(json.dumps({"apiKey": SENTINEL}).encode(), {})

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(PipelineError) as caught:
        OddsClient(SENTINEL).get_events()
    assert str(caught.value) == "invalid response from events"
    assert SENTINEL not in str(caught.value)
