"""Workflow permissions, action pins, and the commands that are allowed to push."""

from __future__ import annotations

import re
from pathlib import Path

from tests.conftest import FIXTURES, run_cli

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
PINS = {
    "actions/checkout": ("3d3c42e5aac5ba805825da76410c181273ba90b1", "v7.0.1"),
    "actions/setup-python": ("6d4fd22b20465ccc0f7354476588e8df7c9ca0e0", "v7.1.0"),
    "actions/setup-node": ("949feb2413d6458794dcd2491c4babbbce0c15c1", "v7.1.0"),
    "astral-sh/setup-uv": ("68559e99cc807c1eb7e9238fd43a15bba460eec2", "v10.3.3"),
}
USES = re.compile(r"uses:\s+(\S+)@([0-9a-f]{40})\s+#\s+(v\d+\.\d+\.\d+)\s*$")
SENTINEL = "SENTINEL_KEY_do_not_leak_9f3a"


def test_every_action_is_pinned_to_the_b1_shas():
    seen = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        assert "set -x" not in text
        uses_lines = [line for line in text.splitlines() if "uses:" in line]
        assert uses_lines, path.name
        for line in uses_lines:
            match = USES.search(line)
            assert match, line
            action, sha, version = match.groups()
            assert PINS[action] == (sha, version)
            seen.append(action)
        # A movable tag is not a pin.
        assert not re.search(r"uses:\s+\S+@v\d", text)
    assert "actions/checkout" in seen
    assert "astral-sh/setup-uv" in seen


def test_ci_keeps_the_site_job_and_the_pipeline_pytest_job():
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert "name: ci" in text
    assert "name: pipeline" in text
    assert "uv run --frozen pytest" in text
    assert "npm test" in text


def test_odds_dispatch_reads_the_repo_and_does_not_push():
    text = (WORKFLOWS / "odds-dispatch.yml").read_text(encoding="utf-8")
    assert "permissions:\n  contents: read\n" in text
    assert "contents: write" not in text
    assert "git push" not in text
    assert "git commit" not in text
    assert "group: odds-dispatch" in text
    assert "cancel-in-progress: false" in text
    assert 'cron: "7,37 12-23 * * *"' in text
    assert 'cron: "7,37 0-3 * * *"' in text
    assert "dry_run" in text
    for expr in (
        "${{ secrets.ODDS_API_KEY }}",
        "${{ secrets.CF_API_TOKEN }}",
        "${{ vars.CF_ACCOUNT_ID }}",
        "${{ vars.D1_DATABASE_ID }}",
    ):
        assert expr in text


def test_only_weekly_and_the_ledger_push_and_they_use_the_helper():
    weekly = (WORKFLOWS / "weekly.yml").read_text(encoding="utf-8")
    ledger = (WORKFLOWS / "pick-ledger.yml").read_text(encoding="utf-8")
    for text in (weekly, ledger):
        assert "bash scripts/push_with_retry.sh" in text
        assert "git push" not in text
    assert "pull-snapshots" in weekly
    assert "lines-export" in weekly
    assert "data/lines" in weekly
    assert "grep -qx 'data/picks.csv'" not in weekly
    assert "${{ secrets.CF_API_TOKEN }}" in weekly
    assert "${{ vars.CF_ACCOUNT_ID }}" in weekly
    assert "${{ vars.D1_DATABASE_ID }}" in weekly
    assert "${{ secrets.ODDS_API_KEY }}" not in weekly


def test_deploy_key_pushes_are_read_only_and_cannot_stage_picks():
    weekly_path = WORKFLOWS / "weekly.yml"
    ledger_path = WORKFLOWS / "pick-ledger.yml"
    weekly = weekly_path.read_text(encoding="utf-8")
    ledger = ledger_path.read_text(encoding="utf-8")
    for path in sorted(WORKFLOWS.glob("*.yml")):
        assert "contents: write" not in path.read_text(encoding="utf-8"), path.name
    for text in (weekly, ledger):
        assert len(re.findall(r"(?m)^permissions:\n  contents: read\n", text)) == 1
        assert len(re.findall(r"(?m)^    permissions:\n      contents: read\n", text)) == 1
        assert text.count("LEDGER_DEPLOY_KEY") == 1
        assert "ssh-key: ${{ secrets.LEDGER_DEPLOY_KEY }}" in text
        assert "persist-credentials: false" not in text
        assert "pull_request" not in text
        assert "fetch-depth: 0" in text
    assert "push:" not in _top_block(weekly, "on")
    assert 'cron: "7 11 * * 4"' in weekly
    assert 'cron: "7 13 * * 2"' in weekly
    for command in ("refresh", "pull-snapshots", "grade", "lines-export", "build-db", "cards"):
        assert command in weekly
    ledger_on = [line.strip() for line in _top_block(ledger, "on").splitlines() if line.strip()]
    assert ledger_on == [
        "push:",
        "branches: [main]",
        "paths:",
        "- data/picks.csv",
    ]
    weekly_specs = _git_add_pathspecs(weekly)
    ledger_specs = _git_add_pathspecs(ledger)
    assert weekly_specs == [
        "data/graded.csv",
        "src/data/summary.json",
        "src/data/picks_view.json",
        "data/lines/*.csv",
        "src/content/games/*.json",
    ]
    assert ledger_specs == ["data/pick_ledger.csv"]
    allowed = set(weekly_specs) | set(ledger_specs)
    forbidden = {".", "-A", "--all", "-u", "data/", "data/*"}
    for spec in weekly_specs + ledger_specs:
        assert spec in allowed
        assert spec not in forbidden
        assert not _matches_picks(spec)


def _top_block(text: str, key: str) -> str:
    lines = text.splitlines()
    start = lines.index(f"{key}:")
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line and not line.startswith(" ") and not line.startswith("#"):
            break
        body.append(line)
    return "\n".join(body)


def _git_add_pathspecs(text: str) -> list[str]:
    specs: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "git add" not in stripped:
            continue
        match = re.search(r"\bgit add\b(.*)$", stripped)
        assert match is not None
        tokens = match.group(1).split()
        if tokens and tokens[0] == "--":
            tokens = tokens[1:]
        assert tokens
        specs.extend(tokens)
    return specs


def _matches_picks(spec: str) -> bool:
    import fnmatch

    return fnmatch.fnmatch("data/picks.csv", spec)


def test_gitignore_blocks_raw_odds_files():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for pattern in ("data/raw/", "data/.cache/", "*.odds.json", "*.d1dump*"):
        assert pattern in lines


def test_missing_secrets_fail_without_printing_them(tmp_path: Path):
    dispatch = run_cli(
        [
            "dispatch",
            "--root",
            str(tmp_path),
            "--schedules",
            str(FIXTURES / "schedules.csv"),
            "--now",
            "2099-09-13T14:07:00Z",
        ],
        env={
            "ODDS_API_KEY": "",
            "CF_API_TOKEN": "tok",
            "CF_ACCOUNT_ID": SENTINEL,
            "D1_DATABASE_ID": "db",
        },
    )
    assert dispatch.returncode == 1
    assert "ODDS_API_KEY" in dispatch.stderr
    assert SENTINEL not in dispatch.stdout + dispatch.stderr

    pull = run_cli(
        ["pull-snapshots", "--root", str(tmp_path), "--out", str(tmp_path / "out.csv")],
        env={
            "CF_API_TOKEN": "",
            "CF_ACCOUNT_ID": SENTINEL,
            "D1_DATABASE_ID": "db",
        },
    )
    assert pull.returncode == 1
    assert "CF_API_TOKEN" in pull.stderr
    assert SENTINEL not in pull.stdout + pull.stderr
