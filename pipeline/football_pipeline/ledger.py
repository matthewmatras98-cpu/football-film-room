"""Append-only pick ledger keyed by GitHub's server push time.

GET /repos/{owner}/{repo}/activity?activity_type=push is polled until a row
with after == GITHUB_SHA shows up, or about two minutes pass. The timestamp
on that activity is first_pushed_at. Git author and committer dates are
client-set and are never read. A second run with the same rows appends nothing.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from football_pipeline.schema import (
    LEDGER_COLUMNS,
    PipelineError,
    format_ts,
    load_pick_rows,
    parse_ts,
)

RETRY_SECONDS = 120.0
RETRY_PAUSE_SECONDS = 5.0
# Newest supported REST API version. The supported list is 2026-03-10 and
# 2022-11-28: https://docs.github.com/en/rest/about-the-rest-api/api-versions
API_VERSION = "2026-03-10"


class TemporaryError(PipelineError):
    """The activity endpoint did not return a usable list. Retry."""


@dataclass(frozen=True)
class LedgerRow:
    pick_id: str
    row_sha: str
    first_pushed_at: datetime


def load_ledger(path: Path) -> list[LedgerRow]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    import csv

    rows: list[LedgerRow] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            return []
        if tuple(header) != LEDGER_COLUMNS:
            raise PipelineError(
                "pick_ledger.csv header must be exactly: " + ",".join(LEDGER_COLUMNS)
            )
        for line_no, raw in enumerate(reader, start=2):
            if not any(cell.strip() for cell in raw):
                continue
            if len(raw) != 3:
                raise PipelineError(f"{path}:{line_no}: expected 3 columns, found {len(raw)}")
            pick_id, row_sha, pushed = (cell.strip() for cell in raw)
            try:
                pushed_at = parse_ts(pushed)
            except ValueError as exc:
                raise PipelineError(f"{path}:{line_no}: {exc}") from exc
            rows.append(LedgerRow(pick_id, row_sha, pushed_at))
    return rows


def lookup_push(rows: list[LedgerRow], pick_id: str, row_sha: str) -> datetime | None:
    """Earliest server time recorded for this exact row. Older shas do not count."""
    matches = [
        row.first_pushed_at
        for row in rows
        if row.pick_id == pick_id and row.row_sha == row_sha
    ]
    if not matches:
        return None
    return min(matches)


def append_new_rows(path: Path, picks_path: Path, pushed_at: datetime) -> int:
    """Append one row per new (pick_id, row_sha). Existing bytes are kept."""
    picks = load_pick_rows(picks_path)
    existing = load_ledger(path)
    known = {(row.pick_id, row.row_sha) for row in existing}
    fresh: list[LedgerRow] = []
    for pick in picks:
        key = (pick.pick_id, pick.row_sha)
        if key in known:
            continue
        fresh.append(LedgerRow(pick.pick_id, pick.row_sha, pushed_at))
        known.add(key)
    if not fresh:
        if not path.exists():
            _write_header(path)
        return 0
    _append_rows(path, fresh)
    return len(fresh)


def find_push_timestamp(activities: list[dict], sha: str) -> datetime | None:
    """Match activity_type=push and after == sha. Other date fields are ignored."""
    for activity in activities:
        if activity.get("activity_type") != "push":
            continue
        if activity.get("after") != sha:
            continue
        timestamp = activity.get("timestamp")
        if not timestamp:
            raise PipelineError(
                f"push activity for {sha} has no timestamp; "
                "refusing to use git author or committer dates"
            )
        return parse_ts(str(timestamp))
    return None


def poll_push_timestamp(
    sha: str,
    fetch_pages,
    sleep,
    monotonic,
    timeout_s: float = RETRY_SECONDS,
) -> datetime:
    """Retry until the push is listed or timeout_s elapses."""
    deadline = monotonic() + timeout_s
    last_temporary: Exception | None = None
    while True:
        try:
            activities = fetch_pages()
        except TemporaryError as exc:
            last_temporary = exc
            activities = []
        found = find_push_timestamp(activities, sha) if activities else None
        if found is not None:
            return found
        if monotonic() >= deadline:
            detail = f"; last error: {last_temporary}" if last_temporary else ""
            raise PipelineError(
                f"no push activity with after={sha} within {int(timeout_s)}s"
                f"{detail}; refusing to use git author or committer dates"
            )
        sleep(min(RETRY_PAUSE_SECONDS, max(0.0, deadline - monotonic())))


def fetch_push_timestamp_from_github(
    owner: str,
    repo: str,
    sha: str,
    token: str,
    *,
    sleep=time.sleep,
    monotonic=time.monotonic,
    timeout_s: float = RETRY_SECONDS,
) -> datetime:
    if not token:
        raise PipelineError("GITHUB_TOKEN is required to read the activity API")
    if not sha:
        raise PipelineError("GITHUB_SHA is required")

    def fetch_pages() -> list[dict]:
        return _list_push_activities(owner, repo, token)

    return poll_push_timestamp(sha, fetch_pages, sleep, monotonic, timeout_s)


def load_activity_file(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise PipelineError(f"{path} must be a JSON list of activities")
    return payload


def github_repository() -> tuple[str, str]:
    raw = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" not in raw:
        raise PipelineError("GITHUB_REPOSITORY must be owner/repo")
    owner, repo = raw.split("/", 1)
    return owner, repo


def http_json(url: str, headers: dict[str, str]) -> tuple[object, dict[str, str], int]:
    """GET JSON. Returns payload, headers, and status. Non-2xx raises HTTPError."""
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            header_map = {key: value for key, value in response.headers.items()}
            status = getattr(response, "status", 200)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise _http_status_error(exc.code, detail) from exc
    except urllib.error.URLError as exc:
        raise TemporaryError(f"activity API request failed: {exc}") from exc
    if not body:
        return [], header_map, status
    return json.loads(body.decode("utf-8")), header_map, status


def _list_push_activities(owner: str, repo: str, token: str) -> list[dict]:
    # `after` on this endpoint is a pagination cursor, not the commit SHA.
    # The SHA is matched against the response field of the same name.
    url: str | None = (
        f"https://api.github.com/repos/{owner}/{repo}/activity"
        "?activity_type=push&per_page=100&direction=desc"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "football-pipeline",
    }
    collected: list[dict] = []
    pages = 0
    while url and pages < 10:
        payload, response_headers, status = http_json(url, headers)
        if status != 200 or not isinstance(payload, list):
            raise TemporaryError(f"activity API returned HTTP {status}")
        collected.extend(payload)
        url = _next_link(response_headers.get("Link") or response_headers.get("link"))
        pages += 1
    return collected


def _http_status_error(status: int, detail: str) -> Exception:
    if status in {401, 403, 404}:
        return PipelineError(f"activity API returned HTTP {status}")
    return TemporaryError(f"activity API returned HTTP {status}: {detail[:200]}")


def _next_link(header: str | None) -> str | None:
    if not header:
        return None
    for part in header.split(","):
        piece = part.strip()
        if 'rel="next"' not in piece:
            continue
        start = piece.find("<")
        end = piece.find(">")
        if start != -1 and end != -1:
            return piece[start + 1 : end]
    return None


def _write_header(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((",".join(LEDGER_COLUMNS) + "\n").encode("utf-8"))


def _append_rows(path: Path, rows: list[LedgerRow]) -> None:
    import io
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size == 0:
        prefix = b""
        header = True
    else:
        prefix = path.read_bytes()
        if not prefix.endswith(b"\n"):
            prefix += b"\n"
        header = False
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    if header:
        writer.writerow(list(LEDGER_COLUMNS))
    for row in rows:
        writer.writerow([row.pick_id, row.row_sha, format_ts(row.first_pushed_at)])
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(prefix + buffer.getvalue().encode("utf-8"))
    temporary.replace(path)
