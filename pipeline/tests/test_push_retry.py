"""push_with_retry.sh against a local bare remote. sleep is stubbed."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "push_with_retry.sh"


def test_script_does_not_trace_commands():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "set -x" not in text
    assert "git fetch origin main" in text
    assert "git rebase origin/main" in text
    assert "sleep 5" in text
    assert "sleep 10" in text


def test_push_succeeds_on_the_first_try(tmp_path: Path):
    script_env, setup_env, log = _env(tmp_path, reject_until=0)
    remote, clone = _repo(tmp_path / "ok", setup_env)
    _commit(clone, "a.txt", "a", setup_env)
    _git(clone, "push", env=setup_env)
    _commit(clone, "b.txt", "b", setup_env)
    result = _run(clone, script_env)
    assert result.returncode == 0, result.stderr
    assert _sleeps(log) == []
    assert _show(remote, "b.txt") == "b"


def test_push_rebases_and_retries_twice_then_succeeds(tmp_path: Path):
    script_env, setup_env, log = _env(tmp_path, reject_until=3)
    remote, clone = _repo(tmp_path / "retry", setup_env)
    _commit(clone, "a.txt", "a", setup_env)
    _git(clone, "push", env=setup_env)
    other = _clone(remote, tmp_path / "other", setup_env)
    _commit(other, "c.txt", "c", setup_env)
    _git(other, "push", env=setup_env)
    _commit(clone, "b.txt", "b", setup_env)
    result = _run(clone, script_env)
    assert result.returncode == 0, result.stderr + result.stdout
    assert _sleeps(log) == ["5", "10"]
    assert _show(remote, "b.txt") == "b"
    assert _show(remote, "c.txt") == "c"


def test_push_exits_nonzero_after_three_failures(tmp_path: Path):
    script_env, setup_env, log = _env(tmp_path, reject_until=99)
    _remote, clone = _repo(tmp_path / "fail", setup_env)
    _commit(clone, "a.txt", "a", setup_env)
    _git(clone, "push", env=setup_env)
    _commit(clone, "b.txt", "b", setup_env)
    result = _run(clone, script_env)
    assert result.returncode != 0
    assert _sleeps(log) == ["5", "10"]


def _run(repo: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _repo(directory: Path, env: dict[str, str]) -> tuple[Path, Path]:
    directory.mkdir(parents=True)
    remote = directory / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, env=env)
    return remote, _clone(remote, directory / "work", env)


def _clone(remote: Path, dest: Path, env: dict[str, str]) -> Path:
    subprocess.run(
        ["git", "clone", str(remote), str(dest)],
        check=True,
        env=env,
        capture_output=True,
    )
    _git(dest, "config", "user.name", "test", env=env)
    _git(dest, "config", "user.email", "test@example.com", env=env)
    return dest


def _commit(repo: Path, name: str, text: str, env: dict[str, str]) -> None:
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", name, env=env)
    _git(repo, "commit", "-m", name, env=env)


def _git(repo: Path, *args: str, env: dict[str, str]) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )


def _show(remote: Path, name: str) -> str:
    result = subprocess.run(
        ["git", "--git-dir", str(remote), "show", f"main:{name}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _sleeps(log: Path) -> list[str]:
    if not log.exists():
        return []
    return [line for line in log.read_text(encoding="utf-8").splitlines() if line]


def _env(tmp_path: Path, reject_until: int) -> tuple[dict[str, str], dict[str, str], Path]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "sleeps.txt"
    real_git = subprocess.check_output(["bash", "-c", "command -v git"], text=True).strip()
    (bindir / "sleep").write_text(
        "#!/bin/bash\n" + f'printf "%s\\n" "$1" >> "{log}"\n' + "exit 0\n",
        encoding="utf-8",
    )
    (bindir / "sleep").chmod(0o755)
    (bindir / "git").write_text(
        "#!/bin/bash\n"
        f'REAL="{real_git}"\n'
        f'COUNT="{tmp_path / "pushes"}"\n'
        'if [ "$1" = "push" ]; then\n'
        '  n=0\n'
        '  if [ -f "$COUNT" ]; then n=$(cat "$COUNT"); fi\n'
        '  n=$((n + 1))\n'
        '  printf "%s" "$n" > "$COUNT"\n'
        f'  if [ "$n" -lt {reject_until} ]; then\n'
        '    echo "reject $n" >&2\n'
        "    exit 1\n"
        "  fi\n"
        "fi\n"
        'exec "$REAL" "$@"\n',
        encoding="utf-8",
    )
    (bindir / "git").chmod(0o755)
    config = tmp_path / "gitconfig"
    config.write_text("", encoding="utf-8")
    setup = os.environ.copy()
    setup["GIT_CONFIG_GLOBAL"] = str(config)
    setup["GIT_CONFIG_SYSTEM"] = "/dev/null"
    setup["GIT_AUTHOR_NAME"] = "test"
    setup["GIT_AUTHOR_EMAIL"] = "test@example.com"
    setup["GIT_COMMITTER_NAME"] = "test"
    setup["GIT_COMMITTER_EMAIL"] = "test@example.com"
    setup["GIT_TERMINAL_PROMPT"] = "0"
    script = setup.copy()
    script["PATH"] = f"{bindir}{os.pathsep}{setup.get('PATH', '')}"
    return script, setup, log
