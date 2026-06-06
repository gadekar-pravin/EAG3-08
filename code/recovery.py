"""Failure classification and recovery decisions for the orchestrator.

Two surfaces:

  - `classify_failure(error_text)` buckets a failure into one of
    {transient, validation_error, upstream_failure} so the orchestrator
    can tell apart a gateway 503 from a malformed plan from a genuine
    upstream miss (NOTES_RUNS round-2 review P0 #3).

  - `plan_recovery(...)` is the predicate the Executor consults to
    decide WHAT to do with a failure: "skip", "replan", or "critic_fail".
    Concentrating the if/elif tree here keeps `flow.Executor.run`
    focused on graph mechanics and lets the recovery policy be unit-
    tested in isolation.

The orchestrator imports `plan_recovery` and acts on the returned
`RecoveryDecision` — it does not branch on classifier output itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RecoveryReason = Literal["transient", "validation_error", "upstream_failure"]
RecoveryAction = Literal["skip", "retry", "replan", "critic_fail"]

# Per-node budget on transient retries. A researcher that 503s is re-run IN
# PLACE (same node id, same edges) so its downstream dependents — which are
# wired to this exact node id — unblock once it succeeds. The gateway 503s
# observed are intermittent (sibling researchers in the same wave succeed), so
# a couple of re-runs almost always recovers the node; a persistently-failing
# one stops here and the final answer surfaces the missing branch. Bounded so a
# truly-down provider cannot re-run a node up to MAX_NODES.
MAX_TRANSIENT_RETRIES = 2

# Run-scoped budget on recovery re-plans. A node that fails with an
# upstream_failure normally queues a recovery Planner; without a ceiling a
# *systematically* failing branch (a tool that is down, a source that always
# returns empty) would keep spawning Planners until MAX_NODES. The observed
# failures are intermittent, so a small budget recovers them; a persistent one
# stops here and the final answer surfaces the missing branch. Tunable; kept
# well under flow.MAX_NODES. 3 covers a typical 3-way fan-out recovering once.
MAX_RECOVERY_REPLANS = 3


def flatten_exception(exc: BaseException) -> str:
    """Render an exception as classifier-friendly error text, recursively
    unwrapping ExceptionGroups to their leaf exceptions.

    The MCP tool loop runs inside anyio/asyncio task groups, so a researcher
    failure reaches the dispatcher as an ExceptionGroup whose str() shows only
    the wrapper ("unhandled errors in a TaskGroup (1 sub-exception)") and hides
    the real leaf. That wrapper text carries none of the gateway-prose keywords
    classify_failure matches on, so a transient 503 would misroute to an
    upstream_failure replan. Flattening to the leaves restores both the log
    detail and the correct classification.
    """
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(flatten_exception(sub) for sub in exc.exceptions)
    return f"{type(exc).__name__}: {exc}"


def classify_failure(error_text: str) -> RecoveryReason:
    e = (error_text or "").lower()
    if not e:
        return "upstream_failure"
    if "malformed" in e or "validationerror" in e or "validation error" in e:
        return "validation_error"
    transient_markers = (
        "503", "502", "504",
        "timeout", "timed out",
        "connection", "connectionerror", "httpstatuserror",
        "service unavailable", "bad gateway", "gateway timeout",
    )
    if any(m in e for m in transient_markers):
        return "transient"
    return "upstream_failure"


@dataclass(frozen=True)
class RecoveryDecision:
    action: RecoveryAction
    reason: RecoveryReason
    note: str
    failure_report: str | None = None  # populated when action == "replan"
    budget_exhausted: bool = False  # True when a would-be replan was capped
    retries_exhausted: bool = False  # True when transient retries ran out


def plan_recovery(
    *,
    failed_skill: str,
    error_text: str,
    failed_node_id: str,
    replans_used: int = 0,
    max_replans: int = MAX_RECOVERY_REPLANS,
    retries_used: int = 0,
    max_retries: int = MAX_TRANSIENT_RETRIES,
) -> RecoveryDecision:
    """Decide what to do with a node failure that is NOT a critic-verdict
    failure. The critic-fail path is handled separately in the Executor
    because it needs access to the critic node's metadata (target, child)
    and a per-target cap that is run-scoped state — this function is the
    purely-local predicate.

    `replans_used` is the count of recovery re-plans already queued this run;
    once it reaches `max_replans` a would-be replan is downgraded to a skip so
    a systematically-failing branch cannot grow the graph without bound.

    `retries_used` is the count of transient retries already spent on THIS node;
    a transient failure re-runs the same node in place until `max_retries`, then
    falls through to skip. In-place retry matters because downstream nodes are
    wired to this node's id — skipping a hard predecessor would deadlock them.

    Decision table (all coverage):
      reason=transient, retries available       → retry (re-run same node in place)
      reason=transient, retries exhausted       → skip (gateway retry exhausted)
      reason=validation_error                   → skip (prompt bug, not runtime)
      reason=upstream_failure, failed=planner   → skip (would loop on Planner errors)
      reason=upstream_failure, budget exhausted → skip (replan cap reached)
      reason=upstream_failure, failed=other     → replan
    """
    reason = classify_failure(error_text)
    if reason == "transient":
        if retries_used < max_retries:
            return RecoveryDecision(
                action="retry", reason=reason,
                note=f"transient gateway error; re-running node in place "
                     f"({retries_used + 1}/{max_retries})",
            )
        return RecoveryDecision(
            action="skip", reason=reason,
            note=f"transient gateway error; {max_retries} in-place retries "
                 f"exhausted, not re-planning",
            retries_exhausted=True,
        )
    if reason == "validation_error":
        return RecoveryDecision(
            action="skip", reason=reason,
            note="validation error (malformed NodeSpec); fix the prompt, not the run",
        )
    if failed_skill == "planner":
        return RecoveryDecision(
            action="skip", reason=reason,
            note="planner-itself failure; not re-planning a planner",
        )
    if replans_used >= max_replans:
        return RecoveryDecision(
            action="skip", reason=reason,
            note=f"recovery replan budget ({max_replans}) exhausted; "
                 f"not re-planning {failed_skill}",
            budget_exhausted=True,
        )
    fr = (f"node={failed_node_id} skill={failed_skill} reason={reason} "
          f"error={error_text}")
    return RecoveryDecision(
        action="replan", reason=reason,
        note="upstream failure; queueing planner recovery",
        failure_report=fr,
    )


def handle_critic_verdict(nid: str, result, graph, recovered_branches: dict,
                          cap_hit: list, *,
                          max_replans: int = MAX_RECOVERY_REPLANS) -> bool:
    """Critic-fail policy (P1 #5). Returns True when the caller should skip
    the normal `extend_from` (because the Critic emitted `fail` and we
    handled it by splicing a recovery Planner). False on `pass`.

    Two shapes of Critic appear in S8: auto-inserted Critics (Graph.extend_from
    inserts one whenever a `critic:true` skill has outgoing edges) which
    carry `target` + `child` in metadata, and Planner-emitted Critics
    which do not — for the latter we derive both from graph structure.

    Two caps bound critic-fail recovery so a structurally-unsatisfiable
    branch cannot grow the graph to MAX_NODES:
      - per-target: a given target is recovered at most once
        (`recovered_branches[target]`);
      - run-scoped: at most `max_replans` distinct targets are recovered
        across the whole run. Because each queued recovery adds exactly one
        key to `recovered_branches`, `len(recovered_branches)` IS the count
        of critic-fail replans spent this run.
    """
    if (result.output or {}).get("verdict", "pass") != "fail":
        return False
    md = graph.g.nodes[nid].get("metadata") or {}
    target_nid = md.get("target")
    child_nid = md.get("child")
    if not target_nid:
        for inp in graph.g.nodes[nid]["inputs"]:
            if inp.startswith("n:") and inp in graph.g.nodes:
                target_nid = inp; break
    if not child_nid:
        succs = list(graph.g.successors(nid))
        child_nid = succs[0] if succs else None
    if child_nid and child_nid in graph.g.nodes:
        graph.mark(child_nid, "skipped")
    if target_nid and not recovered_branches.get(target_nid) \
            and len(recovered_branches) >= max_replans:
        cap_hit.append(target_nid)
        print(f"  ↪ critic-fail recovery budget ({max_replans}) exhausted; "
              f"branch {target_nid} skipped, final will reflect missing data")
        return True
    if target_nid and not recovered_branches.get(target_nid):
        recovered_branches[target_nid] = True
        rationale = (result.output or {}).get("rationale", "(no rationale)")
        fr = f"critic failed target={target_nid} child={child_nid} rationale={rationale}"
        rec_nid = graph.add_node("planner", inputs=["USER_QUERY"],
                                 metadata={"failure_report": fr,
                                           "recovers": target_nid,
                                           "recovery_reason": "critic_fail"})
        print(f"  ↪ critic-fail recovery: planner node {rec_nid} for {target_nid}")
    elif target_nid:
        cap_hit.append(target_nid)
        print(f"  ↪ critic-fail on {target_nid} already recovered once; "
              f"CAP HIT — branch skipped, final will reflect missing data")
    return True
