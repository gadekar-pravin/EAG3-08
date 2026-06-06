"""Offline contract guards for the IPL auction assignment prompt surface."""

from __future__ import annotations

import sys
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = CODE_DIR.parent
sys.path.insert(0, str(CODE_DIR))

from skills import SkillRegistry  # noqa: E402

PROMPTS = CODE_DIR / "prompts"
PLANNER_MD = (PROMPTS / "planner.md").read_text()
CODER_MD = (PROMPTS / "coder.md").read_text()
DISTILLER_MD = (PROMPTS / "distiller.md").read_text()
CRITIC_MD = (PROMPTS / "critic.md").read_text()
STRATEGIST_MD = (PROMPTS / "auction_strategist.md").read_text()
TECH_SPEC_MD = (REPO_DIR / "docs" / "technical-specification.md").read_text()

REQUIRED_PLAYER_FIELDS = [
    "player_name",
    "primary_role",
    "recent_form_score",
    "match_impact_score",
    "role_scarcity_score",
    "fitness_score",
    "price_efficiency_score",
    "estimated_price_crore",
    "auction_risk",
]

STRATEGIST_KEYS = [
    "recommended_buy",
    "total_estimated_spend_crore",
    "why_this_pair",
    "backup_strategy",
    "avoid_or_do_not_overpay",
    "major_risks",
    "confidence",
]

LIVE_PROMPTS = {
    "planner": PLANNER_MD,
    "coder": CODER_MD,
    "distiller": DISTILLER_MD,
    "critic": CRITIC_MD,
    "auction_strategist": STRATEGIST_MD,
}

CANONICAL_FIXTURE_LITERALS = [
    "Jasprit Bumrah",
    "Rashid Khan",
    "Andre Russell",
    "Suryakumar Yadav",
    "Bumrah",
    "Rashid Khan plus Andre",
    "Rashid Khan + Andre Russell",
    "combined score 16.7",
    "estimated_price_crore=11",
    "assignment_controlled",
]


def test_auction_strategist_registered_and_tool_free() -> None:
    skill = SkillRegistry().get("auction_strategist")
    assert skill.prompt_path.name == "auction_strategist.md"
    assert skill.tools_allowed == []
    assert skill.temperature == 0.3
    assert skill.max_tokens == 1200


def test_auction_strategist_prompt_output_contract() -> None:
    for key in STRATEGIST_KEYS:
        assert key in STRATEGIST_MD
    assert "Do not redo or change the Coder arithmetic" in STRATEGIST_MD
    assert "must come from the Coder output for the actual\n    players in USER_QUERY" in STRATEGIST_MD
    assert "Do not use fixed player names, fixed pairings" in STRATEGIST_MD


def test_planner_prompt_contains_generic_auction_dag_contract() -> None:
    assert "IPL auction strategy route" in PLANNER_MD
    assert "N named cricket players" in PLANNER_MD
    assert "Derive the player names and budget from USER_QUERY" in PLANNER_MD
    assert "do not use a fixed player list" in PLANNER_MD
    assert "N independent `researcher` nodes, one per player" in PLANNER_MD
    assert "one `distiller` node with inputs from all player researcher labels" in PLANNER_MD
    assert 'one `auction_strategist` node with input from the coder label' in PLANNER_MD
    assert '`coder` -> `auction_strategist` -> `formatter`' in PLANNER_MD
    assert "Do NOT emit `sandbox_executor`; Coder has a static internal successor" in PLANNER_MD


def test_coder_prompt_contains_generic_auction_math_contract() -> None:
    for text in [
        "Generic IPL auction strategy contract",
        "distilled `fields.player_cards`",
        "derive the auction\nbudget from USER_QUERY",
        "use the player names,\nscores, and `estimated_price_crore` values from upstream `player_cards`",
        "do not substitute the original assignment players or scores",
        "compute every two-player pair from `player_cards`",
        "reject pairs whose total estimated price is greater\nthan the USER_QUERY budget",
        "best_valid_pair_score",
    ]:
        assert text in CODER_MD


def test_canonical_fixture_is_not_baked_into_live_prompts() -> None:
    for prompt_name, prompt_text in LIVE_PROMPTS.items():
        for literal in CANONICAL_FIXTURE_LITERALS:
            assert literal not in prompt_text, f"{literal!r} leaked into {prompt_name}"


def test_canonical_fixture_remains_documented_outside_live_prompts() -> None:
    for text in [
        "Compare Jasprit Bumrah, Rashid Khan, Andre Russell, and Suryakumar Yadav",
        "Best valid pair:",
        "Rashid + Russell",
        "Combined score: 16.7",
    ]:
        assert text in TECH_SPEC_MD


def test_player_card_fields_are_enforced_by_distiller_and_critic() -> None:
    for field in REQUIRED_PLAYER_FIELDS:
        assert field in DISTILLER_MD
        assert field in CRITIC_MD
    # The distiller must DERIVE the judgment scores (not omit them) — an
    # empty required score is what deadlocked the Distiller<->Critic loop,
    # because these scores are not published anywhere for researchers to find.
    assert "always derive and include them rather than omitting" in DISTILLER_MD
    assert "the five 0-10 judgment scores" in DISTILLER_MD
    assert "Derive the\n    requested player names from USER_QUERY" in DISTILLER_MD
    assert "For IPL auction queries" in DISTILLER_MD
    assert "Never reuse scores or prices from\n    an example or prior run" in DISTILLER_MD
    assert "Do not emit a `comparison`, `winner`, or final purchase\n    recommendation" in DISTILLER_MD
    assert "fitness_score" in CRITIC_MD
    assert "missing or empty" in CRITIC_MD
    # The critic must accept a derived score backed by a rationale, and fail
    # only on absent/empty/contradicted fields — not merely "uncited".
    assert "do NOT\n     fail it merely for lacking a direct citation" in CRITIC_MD
    assert "absent, empty, or directly contradicted" in CRITIC_MD
    assert "budget feasibility\n     is the Coder's responsibility" in CRITIC_MD


def test_offline_contract_supports_arbitrary_players() -> None:
    assert "Generic auction strategy example" in PLANNER_MD
    assert "Virat Kohli" in PLANNER_MD
    assert "Travis Head" in PLANNER_MD
    assert "Axar Patel" in PLANNER_MD
    assert "inputs\":[\"n:kohli\",\"n:head\",\"n:patel\"]" in PLANNER_MD
    assert "verify best two-player purchase under the USER_QUERY budget" in PLANNER_MD
    assert "`recommended_buy`, `total_estimated_spend_crore`, pair score" in STRATEGIST_MD
    assert "actual player names from\n    that output" in STRATEGIST_MD
    assert "fixed player names, fixed pairings,\n    fixed spend values, or fixed scores" in STRATEGIST_MD


def test_planner_prompt_contains_targeted_fitness_recovery_rule() -> None:
    assert "Critic rejected a player card" in PLANNER_MD
    assert "`fitness_score`" in PLANNER_MD
    assert "targeted recovery work" in PLANNER_MD
    assert "fitness or injury status" in PLANNER_MD


def test_planner_prompt_contains_controlled_player_card_demo_route() -> None:
    assert "Controlled IPL player-card Critic demo" in PLANNER_MD
    assert "`distiller` -> `formatter`" in PLANNER_MD
    assert "Do not emit `critic`; the orchestrator auto-inserts it" in PLANNER_MD
    assert "leave missing fields empty" in PLANNER_MD
