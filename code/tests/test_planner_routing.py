"""Offline guards for the Planner routing contract and node-question threading.

Two regressions are covered, both observed in live worked-query runs:

  - The Planner mis-routed research through a `retriever` (or skipped straight
    to a `formatter`) whenever *any* MEMORY HITS appeared, even for an explicit
    URL fetch or a current-facts query. ``prompts/planner.md`` must keep the
    rules that send those cases to a `researcher`. This is a string check on the
    prompt contract — cheap insurance that the rules are not silently dropped.
  - ``render_prompt`` must surface a node's own ``metadata.question`` so a
    per-item fan-out node answers its slice instead of the whole USER_QUERY,
    and must surface explicit URLs so Researcher fetches named URLs directly.

Both run fully offline: building a Skill reads ``agent_config.yaml`` + the
prompt files; no gateway or network is touched.
"""

from __future__ import annotations

import sys
from pathlib import Path

# tests/ runs with cwd at code/; the agent modules sit one level up.
CODE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE_DIR))

from skills import SkillRegistry, render_prompt

PLANNER_MD = (CODE_DIR / "prompts" / "planner.md").read_text()
CODER_MD = (CODE_DIR / "prompts" / "coder.md").read_text()
RESEARCHER_MD = (CODE_DIR / "prompts" / "researcher.md").read_text()


def test_planner_routes_explicit_url_to_researcher() -> None:
    """An explicit URL fetch must route to a fresh researcher, not memory."""
    assert "MEMORY HITS are evidence" in PLANNER_MD
    assert "fetch it fresh" in PLANNER_MD


def test_planner_routes_url_field_extraction_through_distiller() -> None:
    """URL fetches that request fields should use Distiller before Formatter."""
    assert "fetch/read a URL and extract specific facts or\nfields" in PLANNER_MD
    assert "`researcher` -> `distiller` -> `formatter`" in PLANNER_MD
    assert "Do NOT emit a `critic` for this path" in PLANNER_MD
    assert '"skill":"distiller","inputs":["n:raw_page"]' in PLANNER_MD
    assert '"skill":"formatter","inputs":["n:facts"]' in PLANNER_MD
    assert "fetch https://example.com/path and collect source text" in PLANNER_MD


def test_planner_fail_fast_for_local_file_paths() -> None:
    """Local file reads are unsupported and must not route through tools."""
    assert "Local filesystem paths are not URLs" in PLANNER_MD
    assert "No Session 8 skill can read\narbitrary host files" in PLANNER_MD
    assert "malformed paths containing the replacement character `�`" in PLANNER_MD
    assert "For a\nrequest to read a local/non-URL file path" in PLANNER_MD
    assert "do NOT emit a `researcher`,\n`retriever`, `coder`, or `sandbox_executor`" in PLANNER_MD
    assert "Emit only a `formatter`\nwith `inputs:[\"USER_QUERY\"]`" in PLANNER_MD
    assert "Local file fail-fast example" in PLANNER_MD
    assert "/Users/name/Documents/bad�name.txt" in PLANNER_MD
    assert '"skill":"formatter","inputs":["USER_QUERY"]' in PLANNER_MD


def test_researcher_directly_fetches_explicit_urls() -> None:
    """Researcher must not search first when the user provided an exact URL."""
    assert "EXPLICIT_URLS" in RESEARCHER_MD
    assert "call `fetch_url` on those\n     exact URL(s) first" in RESEARCHER_MD
    assert "do NOT call\n     `web_search` unless the direct fetch returns empty or error content" in RESEARCHER_MD
    assert "`sources` must include\n    the requested URL(s)" in RESEARCHER_MD


def test_planner_routes_live_facts_to_researcher() -> None:
    """Current/live facts must go to web research, with per-item fan-out."""
    assert "current, live, or changing facts" in PLANNER_MD
    assert "one `researcher` per item" in PLANNER_MD


def test_planner_routes_numeric_comparison_through_coder() -> None:
    """Numeric comparison should aggregate through coder before formatter."""
    assert "numeric processing or arithmetic" in PLANNER_MD
    assert "populations, prices, growth rates, rankings" in PLANNER_MD
    assert "`coder` over the researcher outputs" in PLANNER_MD
    assert "Do NOT emit\n`sandbox_executor`" in PLANNER_MD


def test_planner_prefers_researcher_when_in_doubt() -> None:
    """The default must favour research over guessing from partial memory."""
    assert "prefer a `researcher`" in PLANNER_MD


def test_coder_prompt_requires_executable_pairwise_code() -> None:
    """Coder must emit Python that verifies numeric comparisons."""
    assert '"code": "<complete Python source>"' in CODER_MD
    assert "complete Python source" in CODER_MD
    assert "pairwise absolute" in CODER_MD
    assert "closest_pair" in CODER_MD
    assert "result_summary" in CODER_MD


def test_render_prompt_includes_node_question_when_present() -> None:
    skill = SkillRegistry().get("researcher")
    rendered = render_prompt(
        skill, "populations of London, Paris, Berlin", resolved=[],
        node_question="population of Berlin",
    )
    assert "NODE QUESTION: population of Berlin" in rendered


def test_render_prompt_includes_explicit_urls_from_query() -> None:
    skill = SkillRegistry().get("researcher")
    url = "https://en.wikipedia.org/wiki/Claude_Shannon"
    rendered = render_prompt(
        skill,
        f"Fetch {url} and tell me his birth date.",
        resolved=[],
    )
    assert "EXPLICIT_URLS:" in rendered
    assert f"  - {url}" in rendered


def test_render_prompt_includes_explicit_urls_from_node_question_and_literal() -> None:
    skill = SkillRegistry().get("researcher")
    wiki_url = "https://en.wikipedia.org/wiki/Claude_Shannon"
    nasa_url = "https://www.nasa.gov/"
    rendered = render_prompt(
        skill,
        "Fetch the named sources.",
        resolved=[{"id": "x", "kind": "literal", "value": f"also inspect {nasa_url}."}],
        node_question=f"fetch {wiki_url} and collect source text",
    )
    assert f"  - {wiki_url}" in rendered
    assert f"  - {nasa_url}" in rendered


def test_render_prompt_omits_node_question_when_absent() -> None:
    skill = SkillRegistry().get("researcher")
    rendered = render_prompt(skill, "some query", resolved=[])
    assert "NODE QUESTION:" not in rendered
