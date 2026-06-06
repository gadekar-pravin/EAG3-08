from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import _prefer_provider


def test_agent_preference_moves_provider_to_front_without_dropping_failover() -> None:
    candidates = ["ollama", "gemini", "nvidia", "groq"]

    assert _prefer_provider(candidates, "gemini") == [
        "gemini",
        "ollama",
        "nvidia",
        "groq",
    ]


def test_unknown_agent_preference_leaves_candidates_unchanged() -> None:
    candidates = ["ollama", "nvidia", "groq"]

    assert _prefer_provider(candidates, "gemini") == candidates
