"""Episodic memory: what happened before.

Keeps each explanation (for audit, replay and follow-up questions in the same
session) and each coverage gap (an outcome no rule explained), which becomes
the expert backlog. Appends JSONL locally; production writes the same records
to a warehouse or audit store.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class EpisodicMemory:
    def __init__(self, directory: Path | None = None):
        self.directory = directory
        self.episodes: list[dict[str, Any]] = []
        self.gaps: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        if directory:
            directory.mkdir(parents=True, exist_ok=True)

    def _append(self, name: str, record: dict[str, Any]) -> None:
        if not self.directory:
            return
        with (self.directory / name).open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def record_episode(self, episode: dict[str, Any]) -> None:
        episode = {"at": datetime.now(timezone.utc).isoformat(), **episode}
        with self._lock:
            self.episodes.append(episode)
            self._append("explanations.jsonl", episode)

    def record_gap(self, token: str, record_id: str | None, question: str, rule_sets: list[str]) -> None:
        gap = {
            "at": datetime.now(timezone.utc).isoformat(),
            "token": token,
            "record_id": record_id,
            "question": question,
            "rule_sets": rule_sets,
            "status": "open",
        }
        with self._lock:
            self.gaps.append(gap)
            self._append("gaps.jsonl", gap)

    def last_for(self, session_id: str | None, record_id: str | None, agent: str | None = None) -> dict[str, Any] | None:
        """Most recent episode for this session, record and agent, for follow-up questions."""
        if not session_id or not record_id:
            return None
        for ep in reversed(self.episodes):
            if ep.get("session_id") == session_id and ep.get("record_id") == record_id and ep.get("agent") == agent:
                return ep
        return None
