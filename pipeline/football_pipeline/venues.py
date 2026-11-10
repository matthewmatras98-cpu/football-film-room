"""Stadium roof class and coordinates, keyed by nflverse stadium_id.

`roof_class` is the building: outdoors, dome, or retractable. A fixed roof
or a fixed canopy is dome. The open or closed state of a moving roof is
never stored. Coordinates are the Wikipedia stadium pin, rounded to 5
decimal degrees. Country is an ISO code; only US is covered by NWS.

2026 schedules stamp JAX00 on one Tottenham game. When the schedule's
stadium name is a different row in this table, that row wins.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

VENUES_PATH = Path(__file__).resolve().parent / "venues.csv"

ROOF_CLASSES = frozenset({"outdoors", "dome", "retractable"})


@dataclass(frozen=True)
class Venue:
    stadium_id: str
    stadium: str
    roof_class: str
    lat: float
    lon: float
    country: str


@dataclass(frozen=True)
class VenueTable:
    by_id: dict[str, Venue]
    by_name: dict[str, Venue]


@lru_cache(maxsize=1)
def load_venues() -> VenueTable:
    if not VENUES_PATH.is_file():
        raise FileNotFoundError(f"venues table not found: {VENUES_PATH}")
    by_id: dict[str, Venue] = {}
    by_name: dict[str, Venue] = {}
    with VENUES_PATH.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        expected = ["stadium_id", "stadium", "roof_class", "lat", "lon", "country"]
        if reader.fieldnames != expected:
            raise ValueError("venues.csv header must be " + ",".join(expected))
        for row in reader:
            venue = Venue(
                stadium_id=row["stadium_id"].strip(),
                stadium=row["stadium"].strip(),
                roof_class=row["roof_class"].strip(),
                lat=float(row["lat"]),
                lon=float(row["lon"]),
                country=row["country"].strip().upper(),
            )
            if venue.roof_class not in ROOF_CLASSES:
                raise ValueError(f"{venue.stadium_id} has roof_class {venue.roof_class}")
            if venue.stadium_id in by_id:
                raise ValueError(f"duplicate stadium_id {venue.stadium_id}")
            if venue.stadium in by_name:
                raise ValueError(f"duplicate stadium name {venue.stadium}")
            by_id[venue.stadium_id] = venue
            by_name[venue.stadium] = venue
    return VenueTable(by_id=by_id, by_name=by_name)


def lookup_venue(stadium_id: str | None, stadium_name: str | None) -> Venue | None:
    """Resolve a schedule row. An exact stadium-name match beats a reused id."""
    table = load_venues()
    by_id = table.by_id.get(stadium_id) if stadium_id else None
    named = table.by_name.get(stadium_name) if stadium_name else None
    if named is not None and named is not by_id:
        return named
    return by_id


def format_coord(value: float) -> str:
    """Stable lat/lon text for NWS paths. Trailing zeros are stripped."""
    text = f"{value:.5f}".rstrip("0").rstrip(".")
    if text in {"", "-0"}:
        return "0"
    return text
