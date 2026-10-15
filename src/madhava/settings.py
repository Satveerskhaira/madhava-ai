"""Runtime settings. Everything here is operational; rule content never is."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


@dataclass
class Settings:
    knowledge_dir: Path = field(default_factory=lambda: _env_path("MADHAVA_KNOWLEDGE", REPO_ROOT / "knowledge"))
    fixtures_dir: Path = field(default_factory=lambda: _env_path("MADHAVA_FIXTURES", REPO_ROOT / "fixtures"))
    goldens_file: Path = field(default_factory=lambda: _env_path("MADHAVA_GOLDENS", REPO_ROOT / "goldens" / "goldens.yaml"))
    # Where episodic memory (explanations, gaps) is appended. None keeps it in memory only.
    episodic_dir: Path | None = field(default_factory=lambda: (
        Path(os.environ["MADHAVA_EPISODIC_DIR"]) if os.environ.get("MADHAVA_EPISODIC_DIR") else None))
    # "Today" for current-rules mode. Pinned in tests and replays for reproducibility.
    today: date = field(default_factory=date.today)
    # Rules in force are resolved by adjudication date (design default) or by DOS.
    rule_resolution: str = "adjudication_date"
    # Per-agent deadline for one A2A task, in seconds.
    agent_deadline_s: float = 4.0
    # Candidate rules recalled per agent per search.
    recall_k: int = 12
