"""CLI: python -m football_pipeline {refresh|validate|ledger|grade}."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from football_pipeline.close import read_snapshot_closes
from football_pipeline.grade import grade_picks, write_outputs
from football_pipeline.ledger import (
    append_new_rows,
    fetch_push_timestamp_from_github,
    find_push_timestamp,
    github_repository,
    load_activity_file,
    load_ledger,
)
from football_pipeline.nflv import load_games, load_schedule_frame, write_cache
from football_pipeline.schema import PipelineError, format_ts, parse_pick_file, parse_ts
from football_pipeline.validate import Issue, emit_annotations, validate_rows


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "refresh":
            return _refresh(args)
        if args.command == "validate":
            return _validate(args)
        if args.command == "ledger":
            return _ledger(args)
        if args.command == "grade":
            return _grade(args)
    except (PipelineError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    parser.error(f"unknown command {args.command}")
    return 2


def find_root(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.resolve()
    env = os.environ.get("FOOTBALL_REPO_ROOT")
    if env:
        return Path(env).resolve()
    cwd = Path.cwd().resolve()
    for candidate in [cwd, *cwd.parents]:
        if (candidate / "pipeline" / "pyproject.toml").is_file() and (candidate / "data").is_dir():
            return candidate
        if (candidate / "pyproject.toml").is_file() and (candidate / "football_pipeline").is_dir():
            parent = candidate.parent
            if (parent / "data").is_dir() or (parent / ".git").exists():
                return parent
    raise PipelineError("could not find the repo root; pass --root")


def _refresh(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    frame, source = load_schedule_frame(fixture=args.schedules)
    cache = root / "data" / ".cache" / "schedules.parquet"
    write_cache(frame, cache)
    print(f"refreshed {frame.height} schedule rows from {source}")
    return 0


def _validate(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    picks_path = root / "data" / "picks.csv"
    rows, games, issues = _validated(root, picks_path, args.schedules)
    if issues:
        emit_annotations(root, picks_path, issues)
        print(f"error: {len(issues)} validation problem(s)", file=sys.stderr)
        return 1
    print(f"validated {len(rows)} pick(s) against {len(games)} game(s)")
    return 0


def _ledger(args: argparse.Namespace) -> int:
    root = find_root(args.root)
    picks_path = root / "data" / "picks.csv"
    ledger_path = root / "data" / "pick_ledger.csv"
    rows, _games, issues = _validated(root, picks_path, args.schedules)
    if issues:
        emit_annotations(root, picks_path, issues)
        print(f"error: {len(issues)} validation problem(s)", file=sys.stderr)
        return 1
    if not rows:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        if not ledger_path.exists():
            ledger_path.write_text("pick_id,row_sha,first_pushed_at\n", encoding="utf-8")
        print("ledger unchanged (no picks)")
        return 0
    existing = {(row.pick_id, row.row_sha) for row in load_ledger(ledger_path)}
    pending = [row for row in rows if (row.pick_id, row.row_sha) not in existing]
    if not pending:
        print("ledger unchanged (0 rows appended)")
        return 0
    pushed_at = _resolve_push_time(args)
    added = append_new_rows(ledger_path, picks_path, pushed_at)
    print(f"ledger appended {added} row(s) at {format_ts(pushed_at)}")
    return 0


def _grade(args: argparse.Namespace) -> int:
    if not args.as_of:
        raise PipelineError("grade requires --as-of so generated_at is deterministic")
    as_of = parse_ts(args.as_of)
    root = find_root(args.root)
    picks_path = root / "data" / "picks.csv"
    rows, games, issues = _validated(root, picks_path, args.schedules)
    if issues:
        emit_annotations(root, picks_path, issues)
        print(f"error: {len(issues)} validation problem(s)", file=sys.stderr)
        return 1
    snapshots = read_snapshot_closes(args.snapshots) if args.snapshots else []
    ledger = load_ledger(root / "data" / "pick_ledger.csv")
    graded = grade_picks(rows, games, ledger, snapshots, as_of)
    write_outputs(root, graded, as_of)
    print(f"graded {len(graded)} pick(s) as of {format_ts(as_of)}")
    return 0


def _validated(root: Path, picks_path: Path, schedules: Path | None):
    del root
    if not picks_path.is_file():
        return [], {}, [Issue(1, f"picks file not found: {picks_path}")]
    rows, structural = parse_pick_file(picks_path)
    issues = [Issue(line, message) for line, message in structural]
    if not rows:
        return [], {}, issues
    # Load every game in the schedule so a wrong season or week is reported
    # against the real game instead of looking like an unknown id.
    games, _source = load_games(fixture=schedules, seasons=None)
    by_id = {game.game_id: game for game in games}
    issues.extend(validate_rows(rows, by_id))
    return rows, by_id, issues


def _resolve_push_time(args: argparse.Namespace):
    sha = os.environ.get("GITHUB_SHA", "")
    if args.activity is not None:
        if not sha:
            raise PipelineError("GITHUB_SHA is required")
        found = find_push_timestamp(load_activity_file(args.activity), sha)
        if found is None:
            raise PipelineError(
                f"no push activity with after={sha}; refusing to use git author or committer dates"
            )
        return found
    owner, repo = github_repository()
    token = os.environ.get("GITHUB_TOKEN", "")
    return fetch_push_timestamp_from_github(owner, repo, sha, token)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="football_pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    refresh = sub.add_parser("refresh", help="re-read nflverse schedules")
    validate = sub.add_parser("validate", help="validate data/picks.csv")
    ledger = sub.add_parser("ledger", help="append the pick ledger from the activity API")
    grade = sub.add_parser("grade", help="write graded.csv, summary.json, and picks_view.json")

    for command in (refresh, validate, ledger, grade):
        command.add_argument("--root", type=Path, default=None, help="repo root (default: auto)")
        command.add_argument(
            "--schedules",
            type=Path,
            default=None,
            help="offline schedules fixture (csv or parquet); otherwise re-read nflverse",
        )
    ledger.add_argument(
        "--activity",
        type=Path,
        default=None,
        help="JSON list of activity-API objects; skips the network (tests)",
    )
    grade.add_argument("--as-of", dest="as_of", default=None, help="ISO-8601 timestamp with offset")
    grade.add_argument(
        "--snapshots",
        type=Path,
        default=None,
        help="optional per-book close table (fixtures; T2 supplies real snapshots)",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
