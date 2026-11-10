#!/usr/bin/env python3
"""Rebuild the 2099 card goldens and compare them to the committed files.

  python3 scripts/check_generated_cards.py --check
  python3 scripts/check_generated_cards.py --write
"""

from __future__ import annotations

import argparse
import difflib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "pipeline"
FIXTURES = PIPELINE / "tests" / "fixtures" / "cards"
GOLDEN = REPO / "tests" / "fixtures" / "generated-cards"
AS_OF = "2099-09-18T16:00:00Z"
SEASON = "2099"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="fail if committed cards differ")
    mode.add_argument("--write", action="store_true", help="rewrite the committed cards")
    args = parser.parse_args()

    actual = render()
    if args.write:
        GOLDEN.mkdir(parents=True, exist_ok=True)
        for name, payload in actual.items():
            (GOLDEN / name).write_bytes(payload)
            print(f"wrote tests/fixtures/generated-cards/{name}")
        return 0

    failed = False
    expected_names = {path.name for path in GOLDEN.glob("*.json")}
    if expected_names != set(actual):
        print(
            f"golden names {sorted(expected_names)} != generator {sorted(actual)}",
            file=sys.stderr,
        )
        failed = True
    for name, payload in sorted(actual.items()):
        failed |= report_diff(GOLDEN / name, payload)
    if failed:
        print(
            "generated cards are stale. Regenerate with: python3 scripts/check_generated_cards.py --write",
            file=sys.stderr,
        )
        return 1
    print("generated cards match the card generator")
    return 0


def render() -> dict[str, bytes]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        (root / "data").mkdir(parents=True)
        database = root / "football.duckdb"
        _run(
            [
                "build-db",
                "--root",
                str(root),
                "--season",
                SEASON,
                "--out",
                str(database),
                "--fixtures",
                str(FIXTURES),
            ]
        )
        _run(
            [
                "cards",
                "--root",
                str(root),
                "--db",
                str(database),
                "--as-of",
                AS_OF,
                "--nws-fixtures",
                str(FIXTURES / "nws"),
            ]
        )
        out = root / "src" / "content" / "games"
        files = sorted(path for path in out.glob("*.json"))
        if not files:
            raise SystemExit("card generator wrote no files")
        return {path.name: path.read_bytes() for path in files}


def _run(args: list[str]) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is required (0.13.7, the version pinned in ci.yml)")
    env = os.environ.copy()
    env["NFLREADPY_CACHE"] = "off"
    env.pop("GITHUB_TOKEN", None)
    env.pop("GITHUB_SHA", None)
    env.pop("GITHUB_REPOSITORY", None)
    result = subprocess.run(
        [uv, "run", "--directory", str(PIPELINE), "--frozen", "python", "-m", "football_pipeline", *args],
        cwd=PIPELINE,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr + result.stdout)


def report_diff(path: Path, actual: bytes) -> bool:
    if not path.is_file():
        print(f"missing {path.relative_to(REPO)}", file=sys.stderr)
        return True
    expected = path.read_bytes()
    if expected == actual:
        return False
    diff = difflib.unified_diff(
        expected.decode("utf-8").splitlines(True),
        actual.decode("utf-8").splitlines(True),
        fromfile=str(path.relative_to(REPO)),
        tofile="generator",
    )
    sys.stderr.writelines(diff)
    return True


if __name__ == "__main__":
    raise SystemExit(main())
