"""Unit tests for spawn-dependency edges in ``Graph.extend_from`` (pure: no
network/gateway).

Regression guard for a DAG-view discrepancy: a "Hello" run spawned a formatter
whose only input was ``USER_QUERY``. Because ``USER_QUERY`` carries no in-graph
edge, the planner→formatter spawn dependency was never encoded, so the graph
had 0 edges and ``dag_view``'s topological waves collapsed both nodes into a
single "ran in parallel" row — even though the formatter could not run until
the planner finished spawning it. A spawned child must always carry an edge
from the node that spawned it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flow import Graph
from schemas import AgentResult, NodeSpec


class _StubSkill:
    """Minimal stand-in for a registry Skill — extend_from only reads these."""

    internal_successors: list[str] = []
    critic: bool = False


class _StubRegistry:
    def get(self, name: str) -> _StubSkill:
        return _StubSkill()


class _DistillerCriticRegistry:
    def get(self, name: str) -> _StubSkill:
        skill = _StubSkill()
        skill.critic = name == "distiller"
        return skill


def _result(successors: list[NodeSpec]) -> AgentResult:
    return AgentResult(success=True, agent_name="planner", successors=successors)


def test_user_query_child_gets_spawn_edge_from_parent():
    """A child fed only by USER_QUERY still depends on its spawning parent."""
    g = Graph()
    src = g.add_node("planner", inputs=["USER_QUERY"])  # n:1
    added = g.extend_from(
        src,
        _result([NodeSpec(skill="formatter", inputs=["USER_QUERY"])]),
        registry=_StubRegistry(),
    )

    (child,) = added
    assert g.g.has_edge(src, child)
    assert g.g.number_of_edges() == 1
    # Two topological generations → the DAG renders two waves, real order.
    waves = list(nx.topological_generations(g.g))
    assert waves == [[src], [child]]


def test_sibling_dependency_does_not_add_redundant_parent_edge():
    """A child fed by a sibling keeps only that edge — no spurious parent edge
    that would wrongly pull it into an earlier wave."""
    g = Graph()
    src = g.add_node("planner", inputs=["USER_QUERY"])  # n:1
    added = g.extend_from(
        src,
        _result([
            NodeSpec(skill="researcher", inputs=["USER_QUERY"],
                     metadata={"label": "r"}),
            NodeSpec(skill="formatter", inputs=["n:r"]),
        ]),
        registry=_StubRegistry(),
    )

    researcher, formatter = added
    assert g.g.has_edge(src, researcher)        # USER_QUERY child → parent edge
    assert g.g.has_edge(researcher, formatter)  # sibling dependency preserved
    assert not g.g.has_edge(src, formatter)     # no redundant parent edge
    waves = list(nx.topological_generations(g.g))
    assert waves == [[src], [researcher], [formatter]]


def test_planner_created_distiller_edge_gets_critic_gate():
    """A Planner-created distiller -> formatter edge must still be gated by
    Critic; the distiller does not spawn the formatter at runtime in this
    common worked-query shape."""
    g = Graph()
    src = g.add_node("planner", inputs=["USER_QUERY"])  # n:1
    added = g.extend_from(
        src,
        _result([
            NodeSpec(skill="researcher", inputs=["USER_QUERY"],
                     metadata={"label": "raw_page"}),
            NodeSpec(skill="distiller", inputs=["n:raw_page"],
                     metadata={"label": "facts"}),
            NodeSpec(skill="formatter", inputs=["n:facts"],
                     metadata={"label": "out"}),
        ]),
        registry=_DistillerCriticRegistry(),
    )

    researcher, distiller, formatter, critic = added
    assert g.g.nodes[critic]["skill"] == "critic"
    assert g.g.nodes[critic]["metadata"] == {
        "target": distiller,
        "child": formatter,
    }
    assert g.g.has_edge(src, researcher)
    assert g.g.has_edge(researcher, distiller)
    assert g.g.has_edge(distiller, critic)
    assert g.g.has_edge(critic, formatter)
    assert not g.g.has_edge(distiller, formatter)
    assert g.g.nodes[formatter]["inputs"] == [distiller]

    waves = list(nx.topological_generations(g.g))
    assert waves == [[src], [researcher], [distiller], [critic], [formatter]]
