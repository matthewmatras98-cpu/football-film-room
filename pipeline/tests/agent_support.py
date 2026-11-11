"""Shared setup for the agent-database tests. Offline fixtures only."""

from __future__ import annotations

import shutil
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CARDS = FIXTURES / "cards"
AGENT = FIXTURES / "agent"
REPO = Path(__file__).resolve().parents[2]
GOLDEN_CARD = REPO / "tests" / "fixtures" / "generated-cards" / "2099_03_CHI_DET.json"
DUMP = "game_id,book,point\n2099_03_CHI_DET,BOOKSENTINEL,-3.5\n"


def prepare_agent_root(tmp_path: Path) -> tuple[Path, Path]:
    """Copy the card fixtures plus a dump the agent build must ignore."""
    root = tmp_path / "repo"
    (root / "data" / ".cache").mkdir(parents=True)
    (root / "src" / "data").mkdir(parents=True)
    games = root / "src" / "content" / "games"
    games.mkdir(parents=True)
    shutil.copy(AGENT / "graded.csv", root / "data" / "graded.csv")
    shutil.copy(AGENT / "summary.json", root / "src" / "data" / "summary.json")
    shutil.copy(AGENT / "picks_view.json", root / "src" / "data" / "picks_view.json")
    shutil.copy(GOLDEN_CARD, games / GOLDEN_CARD.name)
    (root / "data" / ".cache" / "week.d1dump.csv").write_text(DUMP, encoding="utf-8")
    (root / "extra.d1dump.csv").write_text(DUMP, encoding="utf-8")
    fixtures = tmp_path / "fixtures"
    shutil.copytree(CARDS, fixtures)
    (fixtures / "week.d1dump.csv").write_text(DUMP, encoding="utf-8")
    return root, fixtures
