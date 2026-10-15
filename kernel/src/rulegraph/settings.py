"""Operational settings. Rule content is never configured here."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


@dataclass
class Settings:
    # "Today" for current-rules mode. Pin it in tests and replays.
    today: date = field(default_factory=date.today)
    # Which date selects the rules in force: the decision date (default) or the service date.
    rule_resolution: str = "decision_date"
    # Deadline for one delegated task, in seconds.
    agent_deadline_s: float = 4.0
    # Candidate rules recalled per investigator per search.
    recall_k: int = 12
    # Where episodic memory is appended. None keeps it in memory only.
    episodic_dir: Path | None = None
