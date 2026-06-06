"""Unit tests for the empty-result failure contract in ``run_skill``.

Regression guard for a silent failure observed in a live Shannon run
(``test_03_shannon``): the researcher node returned ``success=True`` with
``output={}`` after its tool-use loop produced no usable final text. Because
the node "succeeded", ``plan_recovery`` never ran and the empty result flowed
to the formatter, which then answered "the upstream researcher node did not
return any data". A node that produced *neither* parseable output *nor* any
successors did no useful work and must fail instead, with an
``upstream_failure``-classified error so the orchestrator re-plans.

These tests monkeypatch the gateway/tool layer so they run offline (no network,
no token spend) — they exercise ``run_skill``'s success/failure decision, not
the model.
"""

from __future__ import annotations

import sys
from pathlib import Path

# tests/ runs with cwd at code/; the agent modules sit one level up.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from recovery import classify_failure, plan_recovery
from skills import SkillRegistry, run_skill


def _nodes(skill: str) -> dict:
    return {"n:1": {"inputs": ["USER_QUERY"], "skill": skill, "status": "running"}}


async def test_researcher_empty_output_is_failure(monkeypatch):
    """Empty final text -> parsed={} and no successors -> success=False, and the
    error must route to a *replan* (not a skip)."""
    async def fake_run_with_tools(**_kwargs):
        return {"text": "", "provider": "test-prov"}

    monkeypatch.setattr("mcp_runner.run_with_tools", fake_run_with_tools)
    skill = SkillRegistry().get("researcher")
    result, _ = await run_skill(
        skill, "n:1", _nodes("researcher"), "sid", "Find Shannon's birth date", None
    )

    assert result.success is False
    assert result.output == {}
    # Must classify as upstream_failure so plan_recovery replans rather than skips.
    assert classify_failure(result.error or "") == "upstream_failure"
    decision = plan_recovery(
        failed_skill="researcher", error_text=result.error or "", failed_node_id="n:1"
    )
    assert decision.action == "replan"


async def test_researcher_nonempty_output_succeeds(monkeypatch):
    """A node that returns real JSON output must still succeed (no over-failing)."""
    async def fake_run_with_tools(**_kwargs):
        return {"text": '{"summary": "Shannon born 1916, died 2001"}',
                "provider": "test-prov"}

    monkeypatch.setattr("mcp_runner.run_with_tools", fake_run_with_tools)
    skill = SkillRegistry().get("researcher")
    result, _ = await run_skill(skill, "n:1", _nodes("researcher"), "sid", "q", None)

    assert result.success is True
    assert result.output.get("summary")


async def test_empty_output_but_successors_succeeds(monkeypatch):
    """The planner-safety boundary: a node whose payload is *successors* (so the
    ``successors`` pop empties ``parsed``) still did useful work and must NOT be
    failed. This is exactly the shape a Planner emits."""
    async def fake_run_with_tools(**_kwargs):
        return {"text": '{"successors": [{"skill": "formatter", "inputs": ["USER_QUERY"]}]}',
                "provider": "test-prov"}

    monkeypatch.setattr("mcp_runner.run_with_tools", fake_run_with_tools)
    skill = SkillRegistry().get("researcher")
    result, _ = await run_skill(skill, "n:1", _nodes("researcher"), "sid", "q", None)

    assert result.output == {}          # parsed emptied by the successors pop
    assert len(result.successors) == 1  # but it DID produce work
    assert result.success is True       # so it is NOT a failure
