"""Client for The Odds API v4 NFL odds and events.

/odds with the fixed bookmaker list costs 2 credits. /events costs 0.
The key is sent only inside the params dict. Request URLs are never logged.
urllib's own errors include the URL, so those are replaced with the HTTP
status and the endpoint name before they leave this module.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from football_pipeline.schema import PipelineError

BASE_URL = "https://api.the-odds-api.com"
ODDS_PATH = "/v4/sports/americanfootball_nfl/odds"
EVENTS_PATH = "/v4/sports/americanfootball_nfl/events"
BOOKMAKERS: tuple[str, ...] = (
    "draftkings",
    "fanduel",
    "betmgm",
    "betrivers",
    "bovada",
    "betonlineag",
    "lowvig",
    "mybookieag",
    "betus",
)
USAGE_HEADERS: tuple[str, ...] = (
    "x-requests-remaining",
    "x-requests-used",
    "x-requests-last",
)


class OddsClient:
    def __init__(self, api_key: str, transport=None):
        self._api_key = api_key
        self._transport = transport

    def __repr__(self) -> str:
        return "OddsClient(api_key='***')"

    def get_events(self) -> tuple[list, dict[str, str]]:
        return self._get(
            "events",
            EVENTS_PATH,
            {"dateFormat": "iso", "apiKey": self._api_key},
        )

    def get_odds(self) -> tuple[list, dict[str, str]]:
        params = {
            "markets": "spreads,totals",
            "bookmakers": ",".join(BOOKMAKERS),
            "oddsFormat": "american",
            "dateFormat": "iso",
            "apiKey": self._api_key,
        }
        return self._get("odds", ODDS_PATH, params)

    def _get(self, endpoint: str, path: str, params: dict[str, str]) -> tuple[list, dict[str, str]]:
        status, header_map, body = self._call(endpoint, path, params)
        lowered = {str(key).lower(): str(value) for key, value in header_map.items()}
        _log_usage(lowered)
        if status < 200 or status >= 300:
            raise PipelineError(f"HTTP {status} from {endpoint}")
        return _decode_list(endpoint, body), lowered

    def _call(self, endpoint: str, path: str, params: dict[str, str]):
        if self._transport is not None:
            return self._transport(endpoint, path, params)
        return _urlopen_get(endpoint, path, params)


def _log_usage(headers: dict[str, str]) -> None:
    print(" ".join(f"{name}={headers.get(name, '')}" for name in USAGE_HEADERS))


def _decode_list(endpoint: str, body: bytes) -> list:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise PipelineError(f"invalid response from {endpoint}") from None
    if not isinstance(payload, list):
        raise PipelineError(f"invalid response from {endpoint}")
    return payload


def _urlopen_get(endpoint: str, path: str, params: dict[str, str]):
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{BASE_URL}{path}?{query}",
        headers={"Accept": "application/json", "User-Agent": "football-pipeline"},
        method="GET",
    )
    outcome = _perform(request)
    if outcome[0] == "http":
        raise PipelineError(f"HTTP {outcome[1]} from {endpoint}")
    if outcome[0] == "network":
        raise PipelineError(f"request failed for {endpoint}")
    return outcome[1]


def _perform(request):
    """Read a response. Failures return a tag so the caller can raise cleanly.

    Raising inside the except block would chain the urllib error, and that
    error's text contains the request URL.
    """
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            headers = {key: value for key, value in response.headers.items()}
            status = getattr(response, "status", 200)
            return ("ok", (status, headers, body))
    except urllib.error.HTTPError as exc:
        return ("http", exc.code)
    except urllib.error.URLError:
        return ("network", None)
