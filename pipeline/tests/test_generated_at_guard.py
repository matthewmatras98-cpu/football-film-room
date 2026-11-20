"""The weekly commit step skips a rebuild that only moves generated_at."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
SCRIPT = ROOT / "scripts" / "only_generated_at_changed.py"
OLD = "2099-09-21T11:07:00Z"
NEW = "2099-09-22T11:07:00Z"
WEEKLY_COMMIT = 'git commit -m "Grade picks, publish derived lines, and write game cards"'
GRADE_ONLY_COMMIT = 'git commit -m "Grade picks"\n'

SUMMARY = """{
  "generated_at": "2099-09-21T11:07:00Z",
  "w": 3
}
"""
CARD = """{
  "game_id": "2099_02_NE_MIA",
  "generated_at": "2099-09-21T11:07:00Z",
  "sections": {
    "epa": {
      "generated_at": "2099-09-21T11:07:00Z",
      "value": 0.1
    }
  }
}
"""
VIEW = """[
  {
    "pick_id": "2099_02_NE_MIA-spread",
    "result": "W"
  }
]
"""
FILES = {
    "data/graded.csv": "pick_id,result\n2099_02_NE_MIA-spread,W\n",
    "src/data/summary.json": SUMMARY,
    "src/data/picks_view.json": VIEW,
    "data/lines/2099_w02.csv": (
        "snapshot_ts,game_id,market,line,n_books\n"
        "2099-09-20T00:00:00Z,2099_02_NE_MIA,spread,-3,6\n"
    ),
    "data/lines/2099_w02_stamp.csv": (
        "snapshot_ts,game_id,market,line,n_books,generated_at\n"
        "2099-09-20T00:00:00Z,2099_02_NE_MIA,spread,-3,6,2099-09-21T11:07:00Z\n"
    ),
    "src/content/games/2099_02_NE_MIA.json": CARD,
}


def test_weekly_calls_the_guard_and_keeps_its_crons():
    weekly = (WORKFLOWS / "weekly.yml").read_text(encoding="utf-8")
    assert re.findall(r'(?m)^    - cron: "([^"]+)"$', weekly) == [
        "7 13 * * 2",
        "7 11 * * 4",
    ]
    assert "python3 scripts/only_generated_at_changed.py" in weekly
    assert "git restore --source=HEAD --staged --worktree --" in weekly
    assert weekly.count('echo "no grader changes"') == 2
    step = _commit_step(weekly, WEEKLY_COMMIT)
    assert step.index("python3 scripts/only_generated_at_changed.py") < step.index(WEEKLY_COMMIT)
    grade_only = (WORKFLOWS / "grade-only.yml").read_text(encoding="utf-8")
    assert "python3 scripts/only_generated_at_changed.py" in grade_only
    assert "old.pop" not in grade_only
    assert grade_only.count('echo "no grader changes"') == 2


def test_generated_at_only_rebuild_does_not_commit(tmp_path: Path):
    repo = _repo(tmp_path)
    before = _head(repo)
    card = repo / "src/content/games/2099_02_NE_MIA.json"
    assert card.read_text(encoding="utf-8").count(OLD) == 2
    for rel in (
        "src/data/summary.json",
        "src/content/games/2099_02_NE_MIA.json",
        "data/lines/2099_w02_stamp.csv",
    ):
        _replace(repo / rel, OLD, NEW, count=-1)
    result = _run(repo, "weekly.yml", WEEKLY_COMMIT)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "no grader changes" in result.stdout
    assert _head(repo) == before
    assert _status(repo) == ""
    assert OLD in (repo / "src/data/summary.json").read_text(encoding="utf-8")
    assert NEW not in (repo / "src/content/games/2099_02_NE_MIA.json").read_text(encoding="utf-8")
    assert NEW not in (repo / "data/lines/2099_w02_stamp.csv").read_text(encoding="utf-8")
    assert not (repo / "push.log").exists()


def test_real_grader_change_commits(tmp_path: Path):
    _assert_commits(tmp_path, "graded", "data/graded.csv", ",L\n")
    _assert_commits(tmp_path / "view", "view", "src/data/picks_view.json", '"L"')
    _assert_commits(tmp_path / "card", "card", "src/content/games/2099_02_NE_MIA.json", '"value": 0.2')
    _assert_commits(tmp_path / "lines", "lines", "data/lines/2099_w02_stamp.csv", "-7,6,")


def test_new_card_file_commits(tmp_path: Path):
    repo = _repo(tmp_path)
    before = _head(repo)
    (repo / "src/content/games/2099_03_NEW.json").write_text(
        '{\n  "game_id": "2099_03_NEW",\n  "generated_at": "2099-09-22T11:07:00Z"\n}\n',
        encoding="utf-8",
    )
    result = _run(repo, "weekly.yml", WEEKLY_COMMIT)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "no grader changes" not in result.stdout
    assert _head(repo) != before
    assert _subject(repo) == "Grade picks, publish derived lines, and write game cards"
    assert "src/content/games/2099_03_NEW.json" in _tree(repo)
    assert (repo / "push.log").read_text(encoding="utf-8") == "pushed\n"


def test_grade_only_still_skips_a_generated_at_only_summary(tmp_path: Path):
    repo = _repo(tmp_path)
    before = _head(repo)
    _replace(repo / "src/data/summary.json", OLD, NEW)
    result = _run(repo, "grade-only.yml", GRADE_ONLY_COMMIT)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "no grader changes" in result.stdout
    assert _head(repo) == before
    assert _status(repo) == ""
    assert not (repo / "push.log").exists()


def test_grade_only_still_commits_a_graded_row_change(tmp_path: Path):
    repo = _repo(tmp_path)
    before = _head(repo)
    _replace(repo / "data/graded.csv", ",W\n", ",L\n")
    _replace(repo / "src/data/summary.json", OLD, NEW)
    result = _run(repo, "grade-only.yml", GRADE_ONLY_COMMIT)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "no grader changes" not in result.stdout
    assert _head(repo) != before
    assert _subject(repo) == "Grade picks"
    assert ",L" in _show(repo, "data/graded.csv")
    assert NEW in _show(repo, "src/data/summary.json")


def _assert_commits(tmp_path: Path, kind: str, path: str, needle: str) -> None:
    repo = _repo(tmp_path)
    before = _head(repo)
    if kind == "graded":
        _replace(repo / "data/graded.csv", ",W\n", ",L\n")
        _replace(repo / "src/data/summary.json", OLD, NEW)
    elif kind == "view":
        _replace(repo / "src/data/picks_view.json", '"W"', '"L"')
    elif kind == "card":
        _replace(repo / "src/content/games/2099_02_NE_MIA.json", '"value": 0.1', '"value": 0.2')
    elif kind == "lines":
        _replace(
            repo / "data/lines/2099_w02_stamp.csv",
            "-3,6,2099-09-21T11:07:00Z",
            "-7,6,2099-09-22T11:07:00Z",
        )
    else:
        raise AssertionError(kind)
    result = _run(repo, "weekly.yml", WEEKLY_COMMIT)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "no grader changes" not in result.stdout
    assert _head(repo) != before
    assert _subject(repo) == "Grade picks, publish derived lines, and write game cards"
    assert needle in _show(repo, path), kind
    if kind == "graded":
        assert NEW in _show(repo, "src/data/summary.json")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "core.autocrlf", "false")
    for rel, text in FILES.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    scripts = repo / "scripts"
    scripts.mkdir()
    shutil.copy(SCRIPT, scripts / "only_generated_at_changed.py")
    (scripts / "push_with_retry.sh").write_text("#!/bin/bash\necho pushed >> push.log\nexit 0\n", encoding="utf-8")
    _git(repo, "add", "--", ".")
    _git(repo, "commit", "-m", "init")
    return repo


def _run(repo: Path, workflow: str, needle: str) -> subprocess.CompletedProcess[str]:
    text = (WORKFLOWS / workflow).read_text(encoding="utf-8")
    step = _commit_step(text, needle)
    return subprocess.run(
        ["bash", "-c", step],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def _commit_step(text: str, needle: str) -> str:
    for block in _run_blocks(text):
        if needle in block:
            return block
    raise AssertionError(needle)


def _run_blocks(text: str) -> list[str]:
    lines = text.splitlines()
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        folded = re.search(r"\brun:\s*\|\s*$", line)
        if folded:
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


def _replace(path: Path, old: str, new: str, count: int = 1) -> None:
    text = path.read_text(encoding="utf-8")
    found = text.count(old)
    assert found >= 1
    if count == -1:
        path.write_text(text.replace(old, new), encoding="utf-8")
        return
    assert found >= count
    path.write_text(text.replace(old, new, count), encoding="utf-8")


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


def _status(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


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


def _tree(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout
