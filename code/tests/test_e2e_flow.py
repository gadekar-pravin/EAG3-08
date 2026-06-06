"""End-to-end tests for the five canonical Session 8 worked queries.

These drive ``flow.py`` as a subprocess and assert on the DAG shape read back
from ``graph.json`` plus the final answer text. The heavy lifting lives in
``e2e_runner``; each test here executes one query and asserts that every
*fatal* check passed (advisory checks are logged for diagnostics, not enforced —
node counts and critic auto-insertion vary because the Planner is an LLM).

Running them
------------
- Offline (gateway only, no internet, no token spend beyond the gateway)::

      cd code && uv run pytest tests/test_e2e_flow.py -m "not network" -v

  Runs ``test_01_hello`` and ``test_02_graceful_failure`` only.

- Full suite (needs internet + a live gateway; spends Gemini tokens)::

      cd code && S8_RUN_NETWORK=1 uv run pytest tests/test_e2e_flow.py -v

The three network tests carry both ``@pytest.mark.network`` and a skip guard on
``S8_RUN_NETWORK`` so a bare ``uv run pytest`` never reaches the internet.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# tests/ runs with cwd at code/; the runner + agent modules sit one level up.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import e2e_runner as e2e

RUN_NET = os.environ.get("S8_RUN_NETWORK") == "1"
netonly = pytest.mark.skipif(
    not RUN_NET, reason="set S8_RUN_NETWORK=1 to run live network e2e tests"
)


def _echo(r: e2e.RunResult) -> None:
    """Re-emit the captured flow.py board (session/node/FINAL) so a
    ``pytest -s`` run shows the same output a direct ``flow.py`` run prints.
    The runner captures the child's stdout to parse the sid + FINAL, so without
    this it is never shown. pytest still hides this output unless ``-s`` is
    passed (or the test fails)."""
    print("\n" + r.stdout.rstrip() + "\n")


def _assert(checks: list[e2e.Check]) -> None:
    """Fail on any fatal check; print advisory results for context."""
    for c in checks:
        if not c.fatal:
            status = "ok" if c.ok else "advisory-miss"
            print(f"  [{status}] {c.name}" + (f"  ({c.note})" if c.note else ""))
    failures = [c for c in checks if c.fatal and not c.ok]
    assert not failures, "fatal checks failed:\n" + "\n".join(
        f"  - {c.name}" + (f"  ({c.note})" if c.note else "") for c in failures
    )


def test_01_hello() -> None:
    """Minimal DAG: planner -> formatter, non-empty answer. Runs first so it
    pays the gateway cold-start for the rest of the suite."""
    query, _ = e2e.QUERIES["hello"]
    r = e2e.run_query_blocking(query, timeout=180.0)
    _echo(r)
    _assert(e2e.evaluate_hello(r, e2e.shape(r.sid) if r.sid else {}))


def test_02_graceful_failure() -> None:
    """Unanswerable file read: agent must finish cleanly and explain that the
    path is inaccessible rather than crash."""
    query, _ = e2e.QUERIES["J_graceful"]
    r = e2e.run_query_blocking(query, timeout=180.0)
    _echo(r)
    _assert(e2e.evaluate_graceful(r, e2e.shape(r.sid) if r.sid else {}))


@pytest.mark.network
@netonly
def test_03_shannon() -> None:
    """Sequential research path: planner -> researcher -> distiller -> (critic)
    -> formatter, extracting Shannon's dates from Wikipedia."""
    query, _ = e2e.QUERIES["A_shannon"]
    r = e2e.run_query_blocking(query, timeout=240.0)
    _echo(r)
    _assert(e2e.evaluate_shannon(r, e2e.shape(r.sid) if r.sid else {}))


@pytest.mark.network
@netonly
def test_04_populations() -> None:
    """Parallel fan-out: >=2 researchers concurrently, then a coder/sandbox to
    compare the figures."""
    query, _ = e2e.QUERIES["I_populations"]
    r = e2e.run_query_blocking(query, timeout=240.0)
    _echo(r)
    _assert(e2e.evaluate_populations(r, e2e.shape(r.sid) if r.sid else {}))


@pytest.mark.network
@netonly
def test_05_resumable() -> None:
    """Resumable execution: SIGKILL mid-flight, then --resume from disk. The
    load-bearing assertions are that we caught the run mid-flight and that the
    planner's completion survived the kill."""
    query, _ = e2e.QUERIES["K_resumable"]
    sid, pre, post, r = e2e.run_kill_resume(query)
    # Only the resumed run is captured in full; the pre-kill run is SIGKILLed
    # mid-flight (its survival is proven via the pre/post on-disk shapes).
    _echo(r)
    _assert(e2e.evaluate_resumable(sid, pre, post, r))
