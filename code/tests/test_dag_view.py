"""Unit tests for the rich-rendered DAG view (pure: no network/gateway).

Assertions run against ``plain=True`` output: the default styled string wraps
each coloured run in ANSI escapes, which would split literal substrings like
``n:1 ──▶ n:2`` across escape codes. ``plain=True`` is the stable contract the
``format_dag`` docstring recommends for tests."""

from __future__ import annotations

import sys
from pathlib import Path

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dag_view import format_dag, format_node_log


def _fanout_graph() -> nx.DiGraph:
    """planner → 3 parallel researchers (labelled) → formatter."""
    g = nx.DiGraph()
    g.add_node("n:1", skill="planner", status="complete", metadata={})
    for i, city in ((2, "london"), (3, "paris"), (4, "berlin")):
        g.add_node(f"n:{i}", skill="researcher", status="complete",
                   metadata={"label": city})
        g.add_edge("n:1", f"n:{i}")
    g.add_node("n:5", skill="formatter", status="complete", metadata={})
    for i in (2, 3, 4):
        g.add_edge(f"n:{i}", "n:5")
    return g


def test_header_reports_counts_and_waves():
    out = format_dag(_fanout_graph(), plain=True)
    assert "Execution DAG" in out
    assert "5 nodes · 6 edges · 3 waves" in out
    assert "(one wave = ran in parallel)" in out


def test_header_includes_status_legend():
    out = format_dag(_fanout_graph(), plain=True)
    assert "status" in out
    for glyph in ("✓", "✗", "⊘", "…", "·"):
        assert glyph in out


def test_parallel_wave_renders_three_boxes_and_fanout_arrows():
    out = format_dag(_fanout_graph(), plain=True)
    assert "Wave 2" in out
    assert "3 nodes" in out
    # all three researchers are boxed on the parallel wave
    assert out.count("researcher") == 3
    # one ▼ per fan-out target → three arrows under the planner
    assert "▼   ▼   ▼" in out


def test_planner_label_is_shown():
    out = format_dag(_fanout_graph(), plain=True)
    assert "[london]" in out
    assert "[paris]" in out
    assert "[berlin]" in out


def test_node_time_is_shown_when_available():
    g = _fanout_graph()
    g.nodes["n:1"]["result"] = {"elapsed_s": 12.5}
    assert "12.5s" in format_dag(g, plain=True)


def test_wave_total_is_parallel_wall_clock_not_sum():
    """A wave's total is the slowest node (max), since the wave ran in parallel —
    never the sum, which would overstate the real elapsed time."""
    g = _fanout_graph()
    for nid, secs in (("n:2", 19.1), ("n:3", 17.3), ("n:4", 13.5)):
        g.nodes[nid]["result"] = {"elapsed_s": secs}
    out = format_dag(g, plain=True)
    assert "19.1s wall" in out            # max(19.1, 17.3, 13.5)
    assert "wave total" not in out        # totals live in the left gutter now
    assert "49.9s" not in out             # the (wrong) sum must not appear


def test_wave_gutter_marks_missing_timing():
    # no node carries a result → no wall-clock total can be computed
    out = format_dag(_fanout_graph(), plain=True)
    assert "— wall" in out
    assert "wave total" not in out


def test_edge_panel_is_gone():
    out = format_dag(_fanout_graph(), plain=True)
    # the separate bottom "edges" box (the only ──▶ producer) is removed; the
    # wiring now lives inline as the ▼ connectors between waves
    assert "──▶" not in out


def test_fanin_renders_arrows_before_join():
    """A pure fan-in (3 sources → 1 sink) shows one ▼ per incoming edge."""
    g = nx.DiGraph()
    g.add_node("n:4", skill="formatter", status="complete")
    for i in (1, 2, 3):
        g.add_node(f"n:{i}", skill="researcher", status="complete")
        g.add_edge(f"n:{i}", "n:4")
    out = format_dag(g, plain=True)
    assert "▼   ▼   ▼" in out


def test_natural_sort_beyond_nine():
    g = nx.DiGraph()
    for i in (1, 2, 10):
        g.add_node(f"n:{i}", skill="researcher", status="complete")
        if i != 1:
            g.add_edge("n:1", f"n:{i}")
    out = format_dag(g, plain=True)
    # box order proves numeric (not lexicographic) sort: n:2 before n:10
    assert out.index("n:2 ") < out.index("n:10 ")


def test_failed_status_glyph():
    g = nx.DiGraph()
    g.add_node("n:1", skill="researcher", status="failed")
    assert "✗" in format_dag(g, plain=True)


def test_all_known_status_glyphs_render_on_nodes():
    g = nx.DiGraph()
    statuses = ["complete", "failed", "skipped", "running", "pending"]
    for i, status in enumerate(statuses, start=1):
        g.add_node(f"n:{i}", skill="researcher", status=status)
    out = format_dag(g, plain=True)
    for glyph in ("✓", "✗", "⊘", "…", "·"):
        assert glyph in out


def test_empty_graph_does_not_crash():
    assert format_dag(nx.DiGraph(), plain=True) == "DAG  ·  empty"


def test_cycle_falls_back_without_raising():
    g = nx.DiGraph()
    g.add_node("n:1", skill="a", status="complete")
    g.add_node("n:2", skill="b", status="complete")
    g.add_edge("n:1", "n:2")
    g.add_edge("n:2", "n:1")  # cycle: topological_generations would raise
    out = format_dag(g, plain=True)  # must not raise
    assert "n:1" in out and "n:2" in out


def test_node_log_header_is_compact_with_glyph():
    out = format_node_log("n:2", "researcher", "complete", elapsed_s=15.4, plain=True)
    assert "n:2" in out and "researcher" in out
    assert "✓" in out and "15.4s" in out


def test_node_log_failed_shows_cross_and_error():
    out = format_node_log("n:5", "coder", "failed", elapsed_s=2.1,
                          error="NameError: x", plain=True)
    assert "✗" in out
    assert "err=NameError: x" in out


def test_node_log_verbose_one_line_per_output_key():
    output = {"question": "Find populations", "sources": [{"title": "Wiki"}],
              "findings": "line one\nline two\nline three"}
    out = format_node_log("n:2", "researcher", "complete", elapsed_s=1.0,
                          inputs=["USER_QUERY"], output=output,
                          verbose=True, plain=True)
    lines = out.splitlines()
    # one body line carries each output key as its scannable anchor
    assert sum("question" in ln for ln in lines) == 1
    assert any(ln.strip().startswith("sources") for ln in lines)
    assert any("findings" in ln for ln in lines)
    # the multi-line string value is collapsed onto a single row (no raw newline
    # inside the value, and the words end up on one line)
    findings_line = next(ln for ln in lines if "findings" in ln)
    assert "line one line two line three" in findings_line
    # a list value previews its count rather than dumping the list
    sources_line = next(ln for ln in lines if "sources" in ln)
    assert "1 item" in sources_line


def test_node_log_non_verbose_is_single_line():
    out = format_node_log("n:1", "planner", "complete", elapsed_s=1.0,
                          inputs=["USER_QUERY"], output={"a": 1},
                          verbose=False, plain=True)
    assert out.count("\n") == 0
    assert "in" not in out.split("planner")[1]  # no in/out block leaked


def test_format_dag_does_not_write_to_stdout(capsys):
    """format_dag must be pure — it returns a string and prints nothing.

    Regression guard: the rich rewrite once rendered the panel to stdout as a
    side effect of the recording console, so ``print(format_dag(...))`` drew it
    twice."""
    format_dag(_fanout_graph())
    assert capsys.readouterr().out == ""
