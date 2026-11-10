"""National Weather Service forecast for one kickoff.

`/points/{lat},{lon}` then `properties.forecastHourly`. The period that
contains kickoff (start inclusive, end exclusive) supplies the numbers.
No period, or a non-US / unknown roof, leaves the values null. An HTTP
or parse failure warns and does not raise.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from football_pipeline.schema import parse_ts
from football_pipeline.venues import format_coord

USER_AGENT = "football-film-room (https://github.com/matthewmatras98-cpu/football-film-room)"
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


class NWSError(Exception):
    """A forecast that could not be read. The card still gets null values."""


def warn(message: str) -> None:
    print(f"::warning::{message}")


def parse_wind_mph(text: str | None) -> float | None:
    """'10 mph' -> 10. '7 to 11 mph' -> 9. No number -> None."""
    if not text:
        return None
    numbers = [float(match) for match in _NUMBER.findall(text)]
    if not numbers:
        return None
    if len(numbers) == 1:
        return numbers[0]
    return sum(numbers) / len(numbers)


def period_covering(periods: list[dict], kickoff: datetime) -> dict | None:
    for period in periods:
        start_text = period.get("startTime")
        end_text = period.get("endTime")
        if not start_text or not end_text:
            continue
        try:
            start = parse_ts(str(start_text))
            end = parse_ts(str(end_text))
        except (TypeError, ValueError):
            continue
        if start <= kickoff < end:
            return period
    return None


def wind_mph(period: dict) -> float | None:
    raw = period.get("windSpeed")
    if isinstance(raw, dict):
        value = raw.get("value")
        return None if value is None else float(value)
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        return parse_wind_mph(raw)
    return None


def temperature_f(period: dict) -> float | None:
    raw = period.get("temperature")
    if isinstance(raw, dict):
        raw = raw.get("value")
    if raw is None:
        return None
    return float(raw)


def precip_percent(period: dict) -> float | None:
    raw = period.get("probabilityOfPrecipitation")
    if not isinstance(raw, dict):
        return None
    value = raw.get("value")
    if value is None:
        return None
    return float(value)


def forecast_period(
    lat: float,
    lon: float,
    kickoff: datetime,
    *,
    fixtures: Path | None = None,
    transport=None,
) -> dict | None:
    """Return the hourly period covering kickoff, or None when none does.

    Raises NWSError on HTTP and parse failures. A kickoff with no period
    is not an error.
    """
    points = _load_points(lat, lon, fixtures=fixtures, transport=transport)
    try:
        link = points["properties"]["forecastHourly"]
    except (KeyError, TypeError) as exc:
        raise NWSError("points response has no forecastHourly") from exc
    hourly = _load_hourly(str(link), fixtures=fixtures, transport=transport)
    try:
        periods = hourly["properties"]["periods"]
    except (KeyError, TypeError) as exc:
        raise NWSError("hourly response has no periods") from exc
    if not isinstance(periods, list):
        raise NWSError("hourly response has no periods")
    return period_covering(periods, kickoff)


def _load_points(lat: float, lon: float, *, fixtures: Path | None, transport) -> dict:
    key = f"{format_coord(lat)},{format_coord(lon)}"
    if fixtures is not None:
        path = fixtures / f"{key}.json"
        if not path.is_file():
            raise NWSError(f"missing points fixture {path.name}")
        return _read_json(path)
    url = f"https://api.weather.gov/points/{key}"
    return _decode(url, _fetch(url, transport))


def _load_hourly(link: str, *, fixtures: Path | None, transport) -> dict:
    if fixtures is not None:
        if not link.startswith("fixture:"):
            raise NWSError("fixture forecastHourly must start with fixture:")
        relative = link.removeprefix("fixture:")
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise NWSError("fixture forecastHourly escapes the fixture directory")
        path = (fixtures / relative).resolve()
        root = fixtures.resolve()
        if root != path and root not in path.parents:
            raise NWSError("fixture forecastHourly escapes the fixture directory")
        if not path.is_file():
            raise NWSError(f"missing hourly fixture {relative}")
        return _read_json(path)
    if not link.startswith("https://"):
        raise NWSError("forecastHourly URL is not https")
    return _decode(link, _fetch(link, transport))


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NWSError(f"invalid fixture {path.name}") from exc
    if not isinstance(payload, dict):
        raise NWSError(f"invalid fixture {path.name}")
    return payload


def _decode(url: str, payload: bytes) -> dict:
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise NWSError("invalid JSON from NWS") from exc
    if not isinstance(parsed, dict):
        raise NWSError("invalid JSON from NWS")
    return parsed


def _fetch(url: str, transport) -> bytes:
    if transport is not None:
        try:
            return transport(url)
        except NWSError:
            raise
        except Exception as exc:
            raise NWSError("request failed") from exc
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/geo+json, application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise NWSError(f"HTTP {exc.code}") from None
    except urllib.error.URLError:
        raise NWSError("request failed") from None
