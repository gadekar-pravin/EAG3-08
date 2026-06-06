"""Rich-rendered views of the Session 8 execution graph.

The orchestrator runs the graph in *waves*: every node whose predecessors are
all done becomes ready at once and is dispatched together via
``asyncio.gather``. This view makes that parallelism visible by laying each
wave out as a horizontal row of *boxed nodes*, with edge-aware arrows flowing
downward between waves (one ▼ per edge that crosses each wave boundary).

``format_dag(graph)`` returns a ready-to-print string, so existing callers —
``flow.py`` (live) and ``replay.py`` (offline post-mortem) — keep working
unchanged: they already do ``print(format_dag(graph))``. By default the string
carries ANSI styling (colour); pass ``plain=True`` for un-styled text suitable
for substring assertions in tests or for piping into a file.

Rendering uses ``rich``. This is still a near-leaf module (networkx + rich); it
deliberately pulls in nothing from the gateway/skills stack, so the live and
replay paths can both import it cheaply.
"""

from __future__ import annotations

from io import StringIO

import networkx as nx
from rich.align import Align
from rich.box import ROUNDED
from rich.columns import Columns
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

# status → (glyph, rich colour). Unknown/absent statuses fall back to _FALLBACK.
# Every colour used here is a standard rich colour name (no grey-NN look-ups), so
# this never raises a colour-parse error on an exotic terminal.
_STATUS: dict[str, tuple[str, str]] = {
    "complete": ("✓", "green"),
    "failed":   ("✗", "red"),
    "skipped":  ("⊘", "yellow"),
    "running":  ("…", "cyan"),
    "pending":  ("·", "bright_black"),
}
_FALLBACK = ("?", "white")

_FRAME = "bright_black"  # subtle colour for panel borders and connectors


def _node_sort_key(nid: str) -> tuple[int, str]:
    """Natural ordering for ``n:<i>`` ids so n:2 sorts before n:10."""
    if nid.startswith("n:"):
        try:
            return int(nid.split(":", 1)[1]), nid
        except ValueError:
            pass
    return 10**9, nid


def _elapsed_secs(graph: nx.DiGraph, nid: str) -> float | None:
    """Numeric wall-time for a node, read from the stored AgentResult, or None.

    Defensive on purpose: the result may be absent (pending/running node) or a
    plain dict on a replayed graph, so we never assume the attribute exists."""
    result = graph.nodes[nid].get("result")
    secs = getattr(result, "elapsed_s", None)
    if secs is None and isinstance(result, dict):
        secs = result.get("elapsed_s")
    if isinstance(secs, (int, float)) and secs > 0:
        return float(secs)
    return None


def _elapsed(graph: nx.DiGraph, nid: str) -> str | None:
    """Formatted wall-time for a node (e.g. ``5.7s``), or None when unavailable."""
    secs = _elapsed_secs(graph, nid)
    return f"{secs:.1f}s" if secs is not None else None


def _wave_total(graph: nx.DiGraph, wave: list[str]) -> str | None:
    """Wall-clock for a wave: the slowest node, since the wave ran in parallel.

    None when no node in the wave has usable timing (nothing to show)."""
    times = [s for nid in wave if (s := _elapsed_secs(graph, nid)) is not None]
    return f"{max(times):.1f}s" if times else None


def _node_panel(graph: nx.DiGraph, nid: str) -> Panel:
    """One boxed node: ``n:2 ✓`` titled, with skill / label / time stacked.

    The Planner often names a node via ``metadata.label`` (london/paris/…);
    surfacing it is what lets you tell parallel siblings apart at a glance."""
    data = graph.nodes[nid]
    skill = data.get("skill", "?")
    status = data.get("status", "")
    glyph, colour = _STATUS.get(status, _FALLBACK)
    label = (data.get("metadata") or {}).get("label")

    body = Text(skill, style=f"bold {colour}")
    if isinstance(label, str) and label:
        body.append(f"\n[{label}]", style="italic bright_black")
    secs = _elapsed(graph, nid)
    if secs:
        body.append(f"\n{secs}", style="bright_black")

    return Panel(
        body,
        title=Text(f"{nid} {glyph}", style=f"bold {colour}"),
        title_align="left",
        border_style=colour,
        box=ROUNDED,
        padding=(0, 1),
        expand=False,
    )


def _wave_row(graph: nx.DiGraph, wave: list[str]) -> Group:
    """A centred row of boxed nodes — one parallel wave — captioned with the
    wave's wall-clock total (the slowest node, since they ran concurrently)."""
    row = Align.center(Columns([_node_panel(graph, nid) for nid in wave],
                               padding=(0, 2), expand=False))
    total = _wave_total(graph, wave)
    if total is None:
        return Group(row)
    caption = Align.center(
        Text(f"wave total {total}  (parallel wall-clock)", style="bright_black"))
    return Group(row, caption)


def _connector(graph: nx.DiGraph, upper: list[str], lower: list[str]) -> Align:
    """Centred downward arrows for one wave boundary: one ▼ per edge that
    actually crosses from the upper wave into the lower wave, so a fan-out
    (1→N) and a fan-in (N→1) both read as N arrows and a 1→1 step as one."""
    lower_set = set(lower)
    crossing = sum(1 for u in upper for v in graph.successors(u) if v in lower_set)
    arrows = "   ".join(["▼"] * max(1, crossing))
    return Align.center(Text(arrows, style=f"bold {_FRAME}"))


def _build(graph: nx.DiGraph):
    """Assemble the full renderable. Kept separate from string/console output so
    ``format_dag`` (string) and ``render_dag`` (live console) share one layout."""
    n_nodes = graph.number_of_nodes()
    if n_nodes == 0:
        return Text("DAG  ·  empty", style="bright_black")
    n_edges = graph.number_of_edges()

    # Waves come from topological generations: each generation is one batch the
    # executor ran together. On a cyclic/degenerate graph we fall back to a
    # single flat wave rather than raising — this renders after the work is
    # done and must never crash the run.
    try:
        waves = [sorted(gen, key=_node_sort_key)
                 for gen in nx.topological_generations(graph)]
    except nx.NetworkXUnfeasible:
        waves = [sorted(graph.nodes, key=_node_sort_key)]

    header = Text()
    header.append("Execution DAG", style="bold")
    header.append(
        f"   {n_nodes} nodes · {n_edges} edges · {len(waves)} waves"
        "   (same row = ran in parallel)",
        style="bright_black",
    )

    blocks: list = [Align.center(header), Text()]
    for i, wave in enumerate(waves):
        blocks.append(_wave_row(graph, wave))
        if i < len(waves) - 1:
            blocks.append(_connector(graph, wave, waves[i + 1]))

    return Panel(
        Group(*blocks),
        title="DAG",
        title_align="left",
        border_style=_FRAME,
        box=ROUNDED,
        padding=(1, 2),
    )


def render_dag(graph: nx.DiGraph, *, console: Console | None = None) -> None:
    """Print the DAG straight to a live console (auto-sizes to the terminal)."""
    (console or Console()).print(_build(graph))


def format_dag(graph: nx.DiGraph, *, plain: bool = False) -> str:
    """Return the DAG as a printable string.

    By default the string carries ANSI styling, so the existing
    ``print(format_dag(graph))`` call sites render in colour with no change.
    Pass ``plain=True`` for un-styled text (stable for test assertions / files).
    Width tracks the real terminal when there is one, else rich's 80-col default.

    The recording console is given an in-memory ``file`` so ``rec.print`` only
    records — it must not write to the real stdout, or the caller's
    ``print(format_dag(...))`` would render the panel a second time."""
    width = Console().size.width or 100
    rec = Console(record=True, width=max(60, width), legacy_windows=False,
                  file=StringIO())
    rec.print(_build(graph))
    return rec.export_text(styles=not plain).rstrip("\n")


# ── Live per-node log line ───────────────────────────────────────────────────

_KEY_STYLE = "cyan"          # output-dict keys: the scannable anchors
_LABEL_STYLE = "bright_black"  # in/out labels and node id


def _preview_value(value) -> str:
    """One-line, plain-text preview of a single output value.

    Skill-agnostic on purpose: it handles str/list/dict/scalar generically so a new
    skill with new output keys renders correctly without touching this code. Width
    cropping is left to the caller's recording console."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return " ".join(value.split())  # collapse newlines/runs of whitespace
    if isinstance(value, list):
        if not value:
            return "[]"
        first = value[0]
        if isinstance(first, dict):
            rep = (first.get("title") or first.get("name") or first.get("url")
                   or next((str(v) for v in first.values()), ""))
            head = _preview_value(rep)
        else:
            head = _preview_value(first)
        n = len(value)
        noun = f"{n} item{'s' if n != 1 else ''}"
        return f"{noun} · {head}" if head else noun
    if isinstance(value, dict):
        keys = list(value)
        shown = ", ".join(keys[:6]) + ("…" if len(keys) > 6 else "")
        return "{" + shown + "}"
    return str(value)


def format_node_log(nid: str, skill: str, status: str, *,
                    elapsed_s: float | None = None, error: str | None = None,
                    inputs: list[str] | None = None, output: dict | None = None,
                    verbose: bool = False, plain: bool = False) -> str:
    """ANSI string for one node's live-log block (printed by flow.py per node).

    Always renders the compact header ``n:2  researcher  ✓ 15.4s`` — id dim, skill
    bold in the status colour, status glyph, elapsed dim, error in red. When
    ``verbose`` and an ``output``/``inputs`` are given, appends an ``in`` line and an
    ``out`` block with one aligned ``key  preview`` line per output key. Every line is
    no-wrap with ellipsis overflow, so the recording console crops to the terminal
    width instead of wrapping a long value across many rows."""
    glyph, colour = _STATUS.get(status, _FALLBACK)

    header = Text()
    header.append(f"{nid:<4} ", style=_LABEL_STYLE)
    header.append(f"{skill:<13} ", style=f"bold {colour}")
    header.append(glyph, style=colour)
    if elapsed_s and elapsed_s > 0:
        header.append(f" {elapsed_s:.1f}s", style=_LABEL_STYLE)
    if error:
        header.append(f"  err={error[:80]}", style="red")

    lines: list[Text] = [header]
    if verbose:
        in_line = Text()
        in_line.append("  in   ", style=_LABEL_STYLE)
        in_line.append(", ".join(inputs or []) or "(none)")
        lines.append(in_line)
        if output:
            keyw = min(max(len(k) for k in output), 14)
            for i, (k, v) in enumerate(output.items()):
                row = Text()
                row.append("  out  " if i == 0 else "       ", style=_LABEL_STYLE)
                row.append(f"{k:<{keyw}}  ", style=_KEY_STYLE)
                row.append(_preview_value(v))
                lines.append(row)

    width = Console().size.width or 100
    rec = Console(record=True, width=max(60, width), legacy_windows=False,
                  file=StringIO())
    # no_wrap + ellipsis must be passed to print() itself — the same attributes set
    # on the Text alone do not crop; rich wraps instead. crop trims to the width.
    for line in lines:
        rec.print(line, no_wrap=True, overflow="ellipsis", crop=True)
    return rec.export_text(styles=not plain).rstrip("\n")
