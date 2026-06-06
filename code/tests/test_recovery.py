"""Unit tests for recovery.classify_failure + plan_recovery.

These tests pin the keyword-matching classifier against the actual
error strings the gateway emits today. If a future gateway upgrade
changes the prose (e.g. "Service Unavailable" → "Backend Unavailable"),
the test fails LOUDLY rather than silently routing a transient error
through an upstream-failure re-plan path that would burn tokens.

Review round-3 #2: the classifier is keyword-based; without these
tests a gateway-prose change is a silent regression.

The strings below were captured from real httpx / FastAPI error
output in the V8 gateway logs and from the providers.py error
branches.
"""

from __future__ import annotations

import sys
from pathlib import Path

# When running via pytest the cwd is the tests/ dir; the recovery module
# sits one level up.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from recovery import (
    MAX_RECOVERY_REPLANS,
    MAX_TRANSIENT_RETRIES,
    classify_failure,
    flatten_exception,
    handle_critic_verdict,
    plan_recovery,
)
from schemas import AgentResult


# Strings the gateway actually emits today. Each tuple is
# (error_text, expected_reason).
GATEWAY_TRANSIENT_STRINGS = [
    "exception: HTTPStatusError: Server error '503 Service Unavailable' for url 'http://localhost:8108/v1/chat'",
    "exception: HTTPStatusError: Server error '502 Bad Gateway' for url 'http://localhost:8108/v1/chat'",
    "exception: HTTPStatusError: Server error '504 Gateway Timeout' for url 'http://localhost:8108/v1/chat'",
    "Timeout occurred while waiting for provider reply",
    "Connection reset by peer",
    "httpx.ConnectError: All connection attempts failed",
]

GATEWAY_VALIDATION_STRINGS = [
    "planner: 2 malformed NodeSpec(s) emitted.\n  - successor={'foo': 'bar'} error=...",
    "1 validation error for NodeSpec\nskill\n  Field required",
    "researcher: malformed nodes emitted",
]

GENUINE_UPSTREAM_STRINGS = [
    "no code in upstream coder output",
    "file not found: /nonexistent/path.txt",
    "(not found)",
    "Tavily API returned no results for query 'xyz'",
    "",  # empty error text — treat as upstream by convention
]


@pytest.mark.parametrize("err", GATEWAY_TRANSIENT_STRINGS)
def test_classify_transient(err: str) -> None:
    assert classify_failure(err) == "transient", f"misclassified transient: {err!r}"


@pytest.mark.parametrize("err", GATEWAY_VALIDATION_STRINGS)
def test_classify_validation(err: str) -> None:
    assert classify_failure(err) == "validation_error", f"misclassified validation: {err!r}"


@pytest.mark.parametrize("err", GENUINE_UPSTREAM_STRINGS)
def test_classify_upstream(err: str) -> None:
    assert classify_failure(err) == "upstream_failure", f"misclassified upstream: {err!r}"


def test_plan_recovery_transient_retries_in_place() -> None:
    # A fresh transient failure re-runs the same node in place (not a replan,
    # not a skip) so its already-wired downstream dependents can unblock once
    # the retry succeeds.
    d = plan_recovery(failed_skill="researcher",
                      error_text=GATEWAY_TRANSIENT_STRINGS[0],
                      failed_node_id="n:42")
    assert d.action == "retry"
    assert d.reason == "transient"
    assert d.failure_report is None
    assert d.retries_exhausted is False


def test_plan_recovery_transient_retries_just_under_budget() -> None:
    # One retry short of the cap still retries.
    d = plan_recovery(failed_skill="researcher",
                      error_text=GATEWAY_TRANSIENT_STRINGS[0],
                      failed_node_id="n:42",
                      retries_used=MAX_TRANSIENT_RETRIES - 1)
    assert d.action == "retry"
    assert d.retries_exhausted is False


def test_plan_recovery_transient_retries_exhausted_skips() -> None:
    # At the cap, a transient failure stops retrying and skips, flagging the
    # exhaustion so the Executor can surface the stranded branch loudly.
    d = plan_recovery(failed_skill="researcher",
                      error_text=GATEWAY_TRANSIENT_STRINGS[0],
                      failed_node_id="n:42",
                      retries_used=MAX_TRANSIENT_RETRIES)
    assert d.action == "skip"
    assert d.reason == "transient"
    assert d.retries_exhausted is True


def test_plan_recovery_validation_skips() -> None:
    d = plan_recovery(failed_skill="planner",
                      error_text=GATEWAY_VALIDATION_STRINGS[0],
                      failed_node_id="n:1")
    assert d.action == "skip"
    assert d.reason == "validation_error"


def test_plan_recovery_planner_failure_never_replans() -> None:
    # Even genuinely-upstream "planner failed" never triggers a re-plan;
    # that would loop forever on a stubborn planner.
    d = plan_recovery(failed_skill="planner",
                      error_text="no code in upstream coder output",
                      failed_node_id="n:1")
    assert d.action == "skip"
    assert d.reason == "upstream_failure"


def test_plan_recovery_upstream_failure_replans() -> None:
    d = plan_recovery(failed_skill="researcher",
                      error_text="Tavily returned no results",
                      failed_node_id="n:7")
    assert d.action == "replan"
    assert d.reason == "upstream_failure"
    assert d.failure_report and "n:7" in d.failure_report
    assert "researcher" in d.failure_report


def test_plan_recovery_replans_just_under_budget() -> None:
    # One replan short of the cap still re-plans.
    d = plan_recovery(failed_skill="researcher",
                      error_text="Tavily returned no results",
                      failed_node_id="n:7",
                      replans_used=MAX_RECOVERY_REPLANS - 1)
    assert d.action == "replan"
    assert d.budget_exhausted is False


def test_plan_recovery_budget_exhausted_skips() -> None:
    # At the cap, a would-be upstream replan is downgraded to a skip so a
    # systematically-failing branch cannot grow the graph up to MAX_NODES.
    d = plan_recovery(failed_skill="researcher",
                      error_text="Tavily returned no results",
                      failed_node_id="n:7",
                      replans_used=MAX_RECOVERY_REPLANS)
    assert d.action == "skip"
    assert d.reason == "upstream_failure"
    assert d.budget_exhausted is True
    assert str(MAX_RECOVERY_REPLANS) in d.note


# ── Critic-fail splice tests (review round-3 #3) ────────────────────────────
#
# The end-to-end haiku run did exercise the Critic node, but the Critic
# returned `pass`. The fail-splice path therefore wasn't visible at the
# orchestrator level. These tests drive `handle_critic_verdict` directly
# with a synthetic fail-verdict AgentResult so the splice mechanics are
# verified without depending on an LLM disagreeing with itself.

class _StubGraph:
    """Minimal stand-in for flow.Graph used by handle_critic_verdict."""

    def __init__(self):
        from networkx import DiGraph
        self.g = DiGraph()
        self._added: list[tuple[str, str, list[str], dict]] = []
        self._marks: list[tuple[str, str]] = []
        self._counter = 0

    def mark(self, nid: str, status: str) -> None:
        if nid in self.g.nodes:
            self.g.nodes[nid]["status"] = status
        self._marks.append((nid, status))

    def add_node(self, skill: str, inputs: list, metadata: dict | None = None) -> str:
        self._counter += 1
        nid = f"n:rec{self._counter}"
        self.g.add_node(nid, skill=skill, inputs=list(inputs),
                        metadata=dict(metadata or {}), status="pending")
        self._added.append((nid, skill, list(inputs), dict(metadata or {})))
        return nid


def _seed_critic_branch(graph: _StubGraph, *, auto_inserted: bool):
    """Build target → critic → child shape. When auto_inserted=True the
    critic carries target/child in metadata (as Graph.extend_from sets);
    when False the critic was emitted explicitly by the Planner and the
    handler must derive both from graph structure."""
    graph.g.add_node("n:t", skill="distiller", status="complete",
                     inputs=["USER_QUERY"], metadata={})
    graph.g.add_node("n:c", skill="critic", status="complete",
                     inputs=["n:t"],
                     metadata={"target": "n:t", "child": "n:f"} if auto_inserted else {})
    graph.g.add_node("n:f", skill="formatter", status="pending",
                     inputs=["n:c"], metadata={})
    graph.g.add_edge("n:t", "n:c")
    graph.g.add_edge("n:c", "n:f")


def _fail_result() -> AgentResult:
    return AgentResult(success=True, agent_name="critic",
                       output={"verdict": "fail", "rationale": "syllables off"})


def test_critic_fail_auto_inserted_splices_planner_and_skips_child() -> None:
    g = _StubGraph()
    _seed_critic_branch(g, auto_inserted=True)
    recovered: dict[str, bool] = {}
    cap: list[str] = []
    handled = handle_critic_verdict("n:c", _fail_result(), g, recovered, cap)
    assert handled is True
    assert ("n:f", "skipped") in g._marks, "child was not skipped"
    added_skills = [a[1] for a in g._added]
    assert added_skills == ["planner"], "expected exactly one planner recovery"
    assert g._added[0][3]["recovers"] == "n:t"
    assert g._added[0][3]["recovery_reason"] == "critic_fail"
    assert cap == []


def test_critic_fail_explicit_critic_derives_target_and_child_from_graph() -> None:
    g = _StubGraph()
    _seed_critic_branch(g, auto_inserted=False)
    recovered: dict[str, bool] = {}
    cap: list[str] = []
    handled = handle_critic_verdict("n:c", _fail_result(), g, recovered, cap)
    assert handled is True
    assert ("n:f", "skipped") in g._marks
    assert g._added[0][3]["recovers"] == "n:t"
    assert cap == []


def test_critic_fail_cap_fires_on_second_failure_for_same_target() -> None:
    g = _StubGraph()
    _seed_critic_branch(g, auto_inserted=True)
    recovered: dict[str, bool] = {"n:t": True}  # already recovered once
    cap: list[str] = []
    handled = handle_critic_verdict("n:c", _fail_result(), g, recovered, cap)
    assert handled is True
    assert cap == ["n:t"], "cap-hit should be surfaced for future logging"
    assert [a[1] for a in g._added] == [], "no second planner should be queued"


def test_critic_fail_run_scoped_budget_caps_distinct_targets() -> None:
    # Even with a brand-new target (never recovered before), once the run has
    # already spent MAX_RECOVERY_REPLANS critic-fail recoveries on distinct
    # targets, the next one is capped instead of queued — this is what stops a
    # structurally-unsatisfiable critic contract from growing to MAX_NODES.
    g = _StubGraph()
    _seed_critic_branch(g, auto_inserted=True)
    recovered = {f"n:prev{i}": True for i in range(MAX_RECOVERY_REPLANS)}
    cap: list[str] = []
    handled = handle_critic_verdict("n:c", _fail_result(), g, recovered, cap)
    assert handled is True
    assert cap == ["n:t"], "new target should hit the run-scoped budget cap"
    assert [a[1] for a in g._added] == [], "no planner queued once budget exhausted"


def test_critic_pass_returns_false_no_splice() -> None:
    g = _StubGraph()
    _seed_critic_branch(g, auto_inserted=True)
    recovered: dict[str, bool] = {}
    cap: list[str] = []
    pass_result = AgentResult(success=True, agent_name="critic",
                              output={"verdict": "pass", "rationale": "ok"})
    handled = handle_critic_verdict("n:c", pass_result, g, recovered, cap)
    assert handled is False
    assert g._added == []
    assert cap == []


# ── flatten_exception ─────────────────────────────────────────────────────────
# The researcher's MCP tool loop runs inside anyio/asyncio task groups, so a
# failure reaches the dispatcher as an ExceptionGroup whose str() shows only the
# wrapper text and hides the real leaf. flatten_exception unwraps it so the log
# and classify_failure both see the real error. These tests pin that contract.


class _Fake503(Exception):
    """An exception whose str() mirrors the real httpx 503 the gateway emits,
    so we can build an ExceptionGroup leaf without depending on httpx here."""

    def __str__(self) -> str:
        return ("Server error '503 Service Unavailable' for url "
                "'http://localhost:8108/v1/chat'")


def test_flatten_exception_unwraps_to_leaf_not_wrapper() -> None:
    # Pins the actual bug: str(ExceptionGroup) would yield the wrapper
    # "unhandled errors in a TaskGroup ...", losing the leaf. flatten must not.
    eg = ExceptionGroup("unhandled errors in a TaskGroup", [ValueError("boom")])
    assert flatten_exception(eg) == "ValueError: boom"


def test_flatten_exception_surfaces_503_for_classification() -> None:
    eg = ExceptionGroup("unhandled errors in a TaskGroup (1 sub-exception)",
                        [_Fake503()])
    flat = flatten_exception(eg)
    assert "503" in flat and "_Fake503" in flat
    # The whole point: the flattened text now classifies as transient (→ skip),
    # not the upstream_failure default the wrapper text falls through to.
    assert classify_failure(flat) == "transient"
    assert classify_failure(str(eg)) == "upstream_failure"  # the bug, pinned


def test_flatten_exception_recurses_nested_groups() -> None:
    inner = ExceptionGroup("inner", [TimeoutError("timed out")])
    outer = ExceptionGroup("outer", [inner])
    flat = flatten_exception(outer)
    assert flat == "TimeoutError: timed out"
    assert classify_failure(flat) == "transient"


def test_flatten_exception_joins_multiple_leaves() -> None:
    eg = ExceptionGroup("two", [ValueError("a"), KeyError("b")])
    assert flatten_exception(eg) == "ValueError: a; KeyError: 'b'"


def test_flatten_exception_plain_exception_passthrough() -> None:
    assert flatten_exception(ValueError("nope")) == "ValueError: nope"


# ── in-place retry mechanism ──────────────────────────────────────────────────
# Reproduces the deadlock from session s8-c6b8a1e4: planner → 3 researchers →
# coder(inputs=all 3) → formatter. A failed researcher is a HARD predecessor of
# the coder, so leaving it "failed" strands the coder and formatter forever.
# The Executor's transient retry resets the node to "pending"; once it succeeds
# the coder unblocks. This pins the scheduling invariant the retry relies on.


def _fanout_to_coder_graph():
    from flow import Graph

    g = Graph()
    p = g.add_node("planner", inputs=["USER_QUERY"])
    r = [g.add_node("researcher", inputs=[p]) for _ in range(3)]
    coder = g.add_node("coder", inputs=list(r))
    fmt = g.add_node("formatter", inputs=[coder])
    g.mark(p, "complete")
    return g, p, r, coder, fmt


def test_failed_predecessor_strands_coder() -> None:
    # The bug being fixed: a researcher left "failed" blocks the coder.
    g, p, r, coder, fmt = _fanout_to_coder_graph()
    g.mark(r[0], "failed")
    g.mark(r[1], "complete")
    g.mark(r[2], "complete")
    assert coder not in g.ready_nodes()  # stranded — coder never runs


def test_in_place_retry_unblocks_downstream() -> None:
    g, p, r, coder, fmt = _fanout_to_coder_graph()
    g.mark(r[0], "failed")
    g.mark(r[1], "complete")
    g.mark(r[2], "complete")

    # Executor's retry action: reset the failed node to pending. It must become
    # runnable again (its only predecessor, the planner, is complete).
    g.mark(r[0], "pending")
    assert r[0] in g.ready_nodes()
    assert coder not in g.ready_nodes()  # still waiting on the retry

    # Retry succeeds → coder unblocks, then formatter after it.
    g.mark(r[0], "complete")
    assert coder in g.ready_nodes()
    g.mark(coder, "complete")
    assert fmt in g.ready_nodes()


def test_skipped_predecessor_unblocks_coder() -> None:
    # The general skip-deadlock fix: when recovery gives up on a failed node it
    # marks it "skipped" (not left "failed"), so the coder runs with the lost
    # branch rendered as empty rather than being stranded forever. This is the
    # path taken for validation_error / planner-failure / budget- or
    # retry-exhausted skips alike.
    g, p, r, coder, fmt = _fanout_to_coder_graph()
    g.mark(r[0], "failed")
    g.mark(r[1], "complete")
    g.mark(r[2], "complete")
    assert coder not in g.ready_nodes()   # "failed" predecessor strands it

    g.mark(r[0], "skipped")               # recovery's give-up decision
    assert coder in g.ready_nodes()       # now runnable with r[0] absent
