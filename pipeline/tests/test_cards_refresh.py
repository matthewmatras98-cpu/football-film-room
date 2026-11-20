"""Saturday and Sunday rebuild game cards when injury reports arrive.

A row with no report_status stays off the card. Once that team's report_status
is present, the next card lists the player. A later rebuild that does not
change the report moves only generated_at, and the commit step skips it.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
from pathlib import Path

from tests.conftest import run_cli
from tests.test_cards import AS_OF, CARDS, _copy_tree
from tests.test_generated_at_guard import SCRIPT, _commit_step
from tests.test_workflows import _git_add_pathspecs, _on_keys

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
LATER = "2099-09-19T11:07:00Z"
GAME = "2099_03_CHI_DET"
CARD = f"src/content/games/{GAME}.json"
COMMIT = 'git commit -m "Refresh game cards (injuries)"'


def test_cards_refresh_workflow_is_card_only():
    text = (WORKFLOWS / "cards-refresh.yml").read_text(encoding="utf-8")
    assert re.findall(r'(?m)^    - cron: "([^"]+)"$', text) == [
        "7 11 * * 6",
        "7 11 * * 0",
    ]
    assert "4:07 AM Phoenix" in text
    assert "Phoenix does not observe DST" in text
    assert _on_keys(text) == ["schedule", "workflow_dispatch"]
    assert "group: weekly-grade" in text
    assert "cancel-in-progress: false" in text
    assert _git_add_pathspecs(text) == ["src/content/games/*.json"]
    assert "data/lines" not in _commit_step(text, COMMIT)
    assert "data/graded.csv" not in text
    assert "src/data/" not in text
    run = _command_block(text)
    assert run.index("football_pipeline refresh") < run.index("football_pipeline pull-snapshots")
    assert run.index("football_pipeline pull-snapshots") < run.index("football_pipeline lines-export")
    assert run.index("football_pipeline lines-export") < run.index("football_pipeline build-db")
    assert run.index("football_pipeline build-db") < run.index("football_pipeline cards")
    assert "football_pipeline grade" not in text
    assert "football_pipeline dispatch" not in text
    names = re.findall(r"(?m)^      - name: (.+)$", text)
    assert names == [
        "Install uv",
        "Sync",
        "Refresh, pull snapshots, export lines, and build cards",
        "Commit game cards if they changed",
    ]
    assert all("grade" not in name.lower() for name in names)
    assert re.search(r"(?m)^  grade:\s*$", text) is None
    assert "ODDS_API_KEY" not in text
    assert "secrets.ODDS" not in text
    for expr in (
        "${{ secrets.LEDGER_DEPLOY_KEY }}",
        "${{ secrets.CF_API_TOKEN }}",
        "${{ vars.CF_ACCOUNT_ID }}",
        "${{ vars.D1_DATABASE_ID }}",
    ):
        assert expr in text
    for line in text.splitlines():
        if "echo" in line:
            assert "secrets." not in line
            assert "vars." not in line
    assert "set -x" not in text
    assert "bash scripts/push_with_retry.sh" in text
    assert "git push" not in text
    assert "contents: write" not in text
    assert text.count("contents: read") == 2
    for name in ("weekly.yml", "grade-only.yml", "cards-refresh.yml"):
        body = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert "xargs -0 -r git restore --source=HEAD --staged --worktree --" in body
        assert "xargs -0 git restore" not in body


def test_report_status_turns_a_pending_card_reported_and_a_rerun_does_not_commit(tmp_path: Path):
    root = tmp_path / "repo"
    (root / "src" / "content" / "games").mkdir(parents=True)
    fixtures = tmp_path / "fixtures"
    _copy_tree(CARDS, fixtures)
    injuries = fixtures / "injuries.csv"
    _set_status(injuries, {("3", "CHI"), ("3", "DET")}, "")
    raw = injuries.read_text(encoding="utf-8")
    assert "A. Tackle" in raw
    assert "B. Safety" in raw

    database = tmp_path / "football.duckdb"
    _build(root, fixtures, database, AS_OF)
    pending_path = root / CARD
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    assert pending["injuries_status"] == "pending"
    assert pending["sections"]["injuries"] == {"home": [], "away": []}
    assert "A. Tackle" not in pending_path.read_text(encoding="utf-8")
    assert "B. Safety" not in pending_path.read_text(encoding="utf-8")

    _git_init(root)
    pending_head = _head(root)

    _set_status(injuries, {("3", "CHI"): "Questionable", ("3", "DET"): "Out"})
    _build(root, fixtures, database, AS_OF)
    reported = json.loads(pending_path.read_text(encoding="utf-8"))
    assert reported["injuries_status"] == "reported"
    assert reported["sections"]["injuries"]["away"] == [
        {"player": "A. Tackle", "position": "T", "status": "Questionable", "detail": "Ankle"}
    ]
    assert reported["sections"]["injuries"]["home"] == [
        {"player": "B. Safety", "position": "S", "status": "Out", "detail": "Hamstring"}
    ]
    assert reported["generated_at"] == AS_OF

    published = _run_commit(root)
    assert published.returncode == 0, published.stderr + published.stdout
    assert "no card changes" not in published.stdout
    assert _head(root) != pending_head
    assert _subject(root) == "Refresh game cards (injuries)"
    committed = json.loads(_show(root, CARD))
    assert committed["injuries_status"] == "reported"
    assert [row["player"] for row in committed["sections"]["injuries"]["away"]] == ["A. Tackle"]
    assert [row["player"] for row in committed["sections"]["injuries"]["home"]] == ["B. Safety"]
    assert _changed(root) == [CARD]
    assert (root / "push.log").read_text(encoding="utf-8") == "pushed\n"

    reported_head = _head(root)
    _build(root, fixtures, database, LATER)
    rebuilt = (root / CARD).read_text(encoding="utf-8")
    assert rebuilt != _show(root, CARD)
    assert _show(root, CARD).replace(AS_OF, LATER) == rebuilt
    again = json.loads(rebuilt)
    assert again["injuries_status"] == "reported"
    assert [row["player"] for row in again["sections"]["injuries"]["away"]] == ["A. Tackle"]
    assert [row["player"] for row in again["sections"]["injuries"]["home"]] == ["B. Safety"]

    skipped = _run_commit(root)
    assert skipped.returncode == 0, skipped.stderr + skipped.stdout
    assert "no card changes" in skipped.stdout
    assert _head(root) == reported_head
    assert _status(root) == ""
    assert AS_OF in (root / CARD).read_text(encoding="utf-8")
    assert LATER not in (root / CARD).read_text(encoding="utf-8")
    assert (root / "push.log").read_text(encoding="utf-8") == "pushed\n"


def _command_block(text: str) -> str:
    for block in _run_blocks(text):
        if "football_pipeline cards" in block:
            return block
    raise AssertionError("cards command")


def _run_blocks(text: str) -> list[str]:
    lines = text.splitlines()
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if re.search(r"\brun:\s*\|\s*$", line):
            indent = len(line) - len(line.lstrip())
            body: list[str] = []
            index += 1
            while index < len(lines):
                current = lines[index]
                if current.strip() and (len(current) - len(current.lstrip())) <= indent:
                    break
                body.append(current)
                index += 1
            blocks.append("\n".join(body))
            continue
        index += 1
    return blocks


def _set_status(path: Path, updates: dict[tuple[str, str], str] | set[tuple[str, str]], status: str | None = None) -> None:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        rows = list(reader)
    assert fieldnames is not None
    if isinstance(updates, set):
        mapping = {key: "" if status is None else status for key in updates}
    else:
        mapping = updates
    for row in rows:
        key = (row["week"], row["team"])
        if key in mapping:
            row["report_status"] = mapping[key]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _build(root: Path, fixtures: Path, database: Path, as_of: str) -> None:
    built = run_cli(
        [
            "build-db",
            "--root",
            str(root),
            "--season",
            "2099",
            "--out",
            str(database),
            "--fixtures",
            str(fixtures),
        ]
    )
    assert built.returncode == 0, built.stderr
    written = run_cli(
        [
            "cards",
            "--root",
            str(root),
            "--db",
            str(database),
            "--as-of",
            as_of,
            "--nws-fixtures",
            str(fixtures / "nws"),
        ]
    )
    assert written.returncode == 0, written.stderr + written.stdout


def _git_init(repo: Path) -> None:
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "core.autocrlf", "false")
    scripts = repo / "scripts"
    scripts.mkdir()
    shutil.copy(SCRIPT, scripts / "only_generated_at_changed.py")
    (scripts / "push_with_retry.sh").write_text("#!/bin/bash\necho pushed >> push.log\nexit 0\n", encoding="utf-8")
    (repo / ".gitignore").write_text("push.log\n", encoding="utf-8")
    _git(repo, "add", "--", ".")
    _git(repo, "commit", "-m", "init")


def _run_commit(repo: Path) -> subprocess.CompletedProcess[str]:
    text = (WORKFLOWS / "cards-refresh.yml").read_text(encoding="utf-8")
    step = _commit_step(text, COMMIT)
    return subprocess.run(["bash", "-c", step], cwd=repo, capture_output=True, text=True, check=False)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _head(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _subject(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%s"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _show(repo: Path, path: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"HEAD:{path}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _status(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _changed(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]
