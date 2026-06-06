from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from router import load_limits


def test_default_limits_keep_free_tier_caps() -> None:
    limits = load_limits({})

    assert limits["gemini"]["rpm"] == 15
    assert limits["gemini"]["rpd"] == 1000
    assert limits["gemini"]["tpm"] == 250000
    assert limits["gemini"]["cooldown"] == pytest.approx(4)
    assert limits["openrouter"]["rpm"] == 20
    assert limits["openrouter"]["rpd"] == 50
    assert limits["openrouter"]["cooldown"] == pytest.approx(3)
    assert limits["ollama"]["rpm"] == 0
    assert limits["ollama"]["rpd"] == 0
    assert limits["ollama"]["cooldown"] == 0


def test_gemini_env_overrides_raise_caps_and_derive_cooldown() -> None:
    limits = load_limits({
        "GEMINI_RPM": "4000",
        "GEMINI_TPM": "4000000",
        "GEMINI_RPD": "150000",
    })

    assert limits["gemini"]["rpm"] == 4000
    assert limits["gemini"]["tpm"] == 4000000
    assert limits["gemini"]["rpd"] == 150000
    assert limits["gemini"]["cooldown"] == pytest.approx(0.015)


def test_paid_openrouter_non_free_model_defaults_to_uncapped_local_rpm_rpd() -> None:
    limits = load_limits(
        {"OPENROUTER_MODEL": "deepseek/deepseek-v4-flash"},
        openrouter_paid=True,
    )

    assert limits["openrouter"]["rpm"] == 0
    assert limits["openrouter"]["rpd"] == 0
    assert limits["openrouter"]["cooldown"] == 0
    assert limits["openrouter"]["limit_source"] == "paid"


def test_openrouter_free_model_keeps_free_caps_even_for_paid_key() -> None:
    limits = load_limits(
        {"OPENROUTER_MODEL": "nvidia/nemotron-3-super-120b-a12b:free"},
        openrouter_paid=True,
    )

    assert limits["openrouter"]["rpm"] == 20
    assert limits["openrouter"]["rpd"] == 50
    assert limits["openrouter"]["cooldown"] == pytest.approx(3)
    assert limits["openrouter"]["limit_source"] == "free-model"
