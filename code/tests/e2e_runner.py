"""End-to-end runner for the five canonical Session 8 "worked queries".

This module is the single source of truth for driving ``flow.py`` as a
subprocess and judging the outcome. It is deliberately free of any ``pytest``
import so it can be used two ways:

  - imported by ``tests/test_e2e_flow.py`` (one pytest test per query), and
  - run standalone to print a human-readable PASS/FAIL board::

        cd code && S8_RUN_NETWORK=1 uv run python tests/e2e_runner.py

Why subprocess rather than calling ``Executor().run()`` in-process?  Query K
(resumable execution) requires a hard ``SIGKILL`` mid-flight and a fresh
``--resume`` process — that durability path only exists across process
boundaries, so every query is driven the same way for consistency.

Design facts this harness depends on (all verified against the code):

  - ``flow.py`` mints its own session id ``s8-<8 hex>`` (``flow.py`` ``run()``)
    and prints it before any node runs:  ``session s8-xxxxxxxx  ─  query: ...``
    (the dash is U+2500; we anchor the regex on the ``s8-`` token, not the dash).
  - The final answer is printed as ``FINAL: <answer[:600]>`` between rows of
    ``═`` (U+2550) at ``flow.py:290``.
  - On-disk state lives under ``state/sessions/<sid>/``; we read it back through
    ``persistence.SessionStore``. Node attributes carry ``skill`` and ``status``
    (one of pending/running/complete/failed/skipped).
  - On ``--resume`` every node still marked ``running`` is reset to ``pending``;
    so the kill test snapshots the on-disk shape *before* resuming to prove the
    SIGKILL'd state survived.
"""

from __future__ import annotations

import atexit
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

# tests/ sits one level under code/; flow.py + persistence.py live in code/.
CODE_DIR = Path(__file__).resolve().parent.parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from persistence import SessionStore  # noqa: E402  (after sys.path setup)

# Anchor on the sid token itself — the surrounding line uses a U+2500 dash that
# is easy to mis-encode; the token is stable.
SID_RE = re.compile(r"session\s+(s8-[0-9a-f]{8})\b")
# The bare sid token, for validating a captured id.
SID_TOKEN_RE = re.compile(r"s8-[0-9a-f]{8}")
# Capture the FINAL block up to the first ═ rule that closes it.
FINAL_RE = re.compile(r"FINAL:\s*(.*?)\n═", re.S)

# ── Query catalogue ────────────────────────────────────────────────────────
# key -> (query text, needs_network)
QUERIES: dict[str, tuple[str, bool]] = {
    "hello": ("Say hello.", False),
    "A_shannon": (
        "Fetch https://en.wikipedia.org/wiki/Claude_Shannon and tell me his "
        "birth date, death date, and three key contributions to information "
        "theory.",
        True,
    ),
    "I_populations": (
        "Find the populations of London, Paris, Berlin and tell me which two "
        "are closest in size.",
        True,
    ),
    "J_graceful": ("Read /nonexistent/path.txt and tell me what's in it.", False),
    "K_resumable": (
        "For Lagos, Cairo, and Kinshasa, find current populations and growth "
        "rates and tell me which is growing fastest.",
        True,
    ),
}


@dataclass
class RunResult:
    """Outcome of one ``flow.py`` invocation."""

    sid: str | None
    final: str | None
    stdout: str
    returncode: int | None
    timed_out: bool


@dataclass
class Check:
    """One assertion within a query's evaluation.

    ``fatal=True`` checks fail the test; ``fatal=False`` ones are advisory
    (the documented behaviour allows variation, e.g. exact node counts) and
    are only logged.
    """

    name: str
    ok: bool
    fatal: bool = True
    note: str = ""


# ── Subprocess drivers ─────────────────────────────────────────────────────
def _as_text(x: object) -> str:
    """Coerce a possibly-bytes/None stream into str."""
    if isinstance(x, bytes):
        return x.decode("utf-8", errors="replace")
    return x if isinstance(x, str) else ""


def _flow_args(query: str, resume_sid: str | None) -> list[str]:
    if resume_sid is not None:
        # Empty query on resume tells flow.py to reuse the stored query.txt.
        return ["--resume", resume_sid, query]
    return [query]


def _fresh_state_dir() -> str:
    """A throwaway memory/FAISS state dir for one flow.py run, cleaned at exit.

    flow.py runs as a subprocess and its memory + FAISS index live at a global,
    persistent path that grows every run. Once content is indexed, the Planner
    sees MEMORY HITS and routes research through a `retriever` instead of a
    `researcher`, which makes the worked-query assertions non-deterministic.
    Pointing each run at an empty temp dir (via S8_STATE_DIR, honoured by
    memory.py and forwarded to the MCP server) keeps the suite reproducible and
    never touches the real on-disk index. Session graphs are unaffected —
    persistence.SessionStore keeps writing under code/state/sessions/.
    """
    d = tempfile.mkdtemp(prefix="s8-e2e-state-")
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return d


def _child_env(state_dir: str) -> dict[str, str]:
    """Parent environment plus the isolated state dir for the flow.py child."""
    return {**os.environ, "S8_STATE_DIR": state_dir}


def run_query_blocking(
    query: str, *, timeout: float, resume_sid: str | None = None,
    state_dir: str | None = None,
) -> RunResult:
    """Run ``flow.py`` to completion and capture its outcome.

    ``subprocess.run(timeout=...)`` guarantees the child is killed on
    wall-clock expiry, so this can never hang the test session — a wedged run
    surfaces as ``timed_out=True``.

    Each run gets an isolated, empty memory/FAISS state dir (``state_dir``);
    when omitted a fresh one is minted. On ``--resume`` the caller must pass the
    *same* ``state_dir`` as the killed run so the resumed process sees the same
    memory.
    """
    if state_dir is None:
        state_dir = _fresh_state_dir()
    cmd = ["uv", "run", "python", "flow.py", *_flow_args(query, resume_sid)]
    try:
        cp = subprocess.run(
            cmd,
            cwd=str(CODE_DIR),
            capture_output=True,
            text=True,
            timeout=timeout,
            start_new_session=True,
            env=_child_env(state_dir),
        )
        out = (cp.stdout or "") + (cp.stderr or "")
        rc: int | None = cp.returncode
        timed_out = False
    except subprocess.TimeoutExpired as e:
        # On timeout, stdout/stderr may be bytes, str, or None depending on
        # how much was buffered; coerce both to str so `out` is always text.
        out = _as_text(e.stdout) + _as_text(e.stderr)
        rc = None
        timed_out = True
    sid = (m.group(1) if (m := SID_RE.search(out)) else resume_sid)
    final = (m.group(1).strip() if (m := FINAL_RE.search(out)) else None)
    return RunResult(sid=sid, final=final, stdout=out, returncode=rc, timed_out=timed_out)


def _spawn(query: str, resume_sid: str | None = None,
           state_dir: str | None = None) -> subprocess.Popen:
    """Start ``flow.py`` with a live, unbuffered stdout pipe (for Query K).

    ``python -u`` is load-bearing. When flow.py's stdout is a pipe (not a TTY),
    CPython *block*-buffers it, so the ``session s8-…`` line and every
    node-progress print would sit in the child's buffer until the process
    exits. Query K reads stdout live (``for line in proc.stdout``) to capture
    the sid and then races to SIGKILL the run mid-flight; if the sid only
    surfaces at exit, the run is already complete and ``_midflight`` can never
    be observed — the poller then spins for the full ``midflight_timeout`` and
    raises TimeoutError. ``-u`` forces the child to flush each line as it is
    written. ``bufsize=1`` below only line-buffers *our* read side and is not
    sufficient on its own.
    """
    if state_dir is None:
        state_dir = _fresh_state_dir()
    cmd = ["uv", "run", "python", "-u", "flow.py", *_flow_args(query, resume_sid)]
    return subprocess.Popen(
        cmd,
        cwd=str(CODE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
        env=_child_env(state_dir),
    )


# ── On-disk graph inspection ───────────────────────────────────────────────
def shape(sid: str) -> dict[str, list[str]]:
    """Return ``{skill: [status, ...]}`` from the persisted graph.json.

    Returns ``{}`` if the graph hasn't been written yet (or mid atomic-write).
    """
    try:
        g = SessionStore(sid).read_graph()
    except Exception:
        return {}
    if g is None:
        return {}
    out: dict[str, list[str]] = {}
    for _, d in g.nodes(data=True):
        out.setdefault(d.get("skill", "?"), []).append(d.get("status", "?"))
    return out


def _midflight(s: dict[str, list[str]]) -> bool:
    """Planner finished but the run has not: some downstream node is still in
    flight (pending/running).

    Skill-agnostic on purpose. The Planner is non-deterministic about how it
    decomposes a research query — one run emits three ``researcher`` nodes,
    the next a single ``retriever`` (or a coder, etc.). All are valid, and the
    resume contract ("completed nodes stay complete, running nodes reset to
    pending") is identical regardless of which work skill it chose. Pinning the
    catch to one skill name turned a correct Planner decision into a 120s
    TimeoutError. We anchor on the run's *boundaries* instead: planner done,
    formatter (the terminal node) not yet done, and at least one non-planner
    node still pending/running.
    """
    if "complete" not in s.get("planner", []):
        return False
    if "complete" in s.get("formatter", []):
        return False  # formatter is terminal — the run already finished
    return any(
        st in ("running", "pending")
        for skill, statuses in s.items() if skill != "planner"
        for st in statuses
    )


def wait_until(sid, pred, *, timeout: float, poll: float = 0.5) -> dict[str, list[str]]:
    """Poll ``shape(sid)`` until ``pred`` holds; raise TimeoutError otherwise."""
    deadline = time.monotonic() + timeout
    last: dict[str, list[str]] = {}
    while time.monotonic() < deadline:
        last = shape(sid)
        if last and pred(last):
            return last
        time.sleep(poll)
    raise TimeoutError(f"{sid}: predicate not satisfied in {timeout}s; last shape={last}")


def kill_flow_only(proc: subprocess.Popen) -> None:
    """SIGKILL only the flow pid — never the process group.

    The gateway on :8108 is shared and, when flow auto-starts it, lives in the
    same session/group. Killing just the flow pid leaves the gateway listening
    so the ``--resume`` run can reuse it.
    """
    try:
        os.kill(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=10)
    except Exception:
        pass
    finally:
        if proc.stdout:
            proc.stdout.close()


def run_kill_resume(
    query: str, *, sid_timeout: float = 60.0,
    midflight_timeout: float = 120.0, resume_timeout: float = 300.0,
) -> tuple[str, dict[str, list[str]], dict[str, list[str]], RunResult]:
    """Drive the resumable-execution path for Query K.

    Returns ``(sid, pre_kill_shape, post_kill_shape, resume_result)``.
    """
    # The kill run and the --resume run must share one memory dir so the
    # resumed process sees the same (isolated) memory the killed run built.
    state_dir = _fresh_state_dir()
    proc = _spawn(query, state_dir=state_dir)
    sid: str | None = None
    deadline = time.monotonic() + sid_timeout
    assert proc.stdout is not None
    for line in proc.stdout:
        if (m := SID_RE.search(line)):
            sid = m.group(1)
            break
        if time.monotonic() > deadline:
            break
    if not sid:
        kill_flow_only(proc)
        raise AssertionError("never observed a session id on flow.py stdout")

    try:
        pre = wait_until(sid, _midflight, timeout=midflight_timeout)
    finally:
        kill_flow_only(proc)
    post = shape(sid)
    resumed = run_query_blocking("", resume_sid=sid, timeout=resume_timeout,
                                 state_dir=state_dir)
    return sid, pre, post, resumed


# ── Per-query evaluation (shared by pytest and the standalone board) ────────
def _total_nodes(s: dict[str, list[str]]) -> int:
    return sum(len(v) for v in s.values())


def _present_complete(s: dict[str, list[str]], skill: str) -> bool:
    return "complete" in s.get(skill, [])


def evaluate_hello(r: RunResult, s: dict[str, list[str]]) -> list[Check]:
    return [
        Check("did not time out", not r.timed_out),
        Check("session id captured", bool(r.sid and SID_TOKEN_RE.fullmatch(r.sid)),
              note=f"sid={r.sid}"),
        Check("non-empty final answer", bool(r.final), note=repr((r.final or "")[:80])),
        Check("planner completed", _present_complete(s, "planner")),
        Check("formatter completed", _present_complete(s, "formatter")),
        Check("minimal DAG (<= 4 nodes)", _total_nodes(s) <= 4, fatal=False,
              note=f"nodes={_total_nodes(s)} shape={s}"),
    ]


def evaluate_shannon(r: RunResult, s: dict[str, list[str]]) -> list[Check]:
    final = r.final or ""
    return [
        Check("did not time out", not r.timed_out),
        Check("non-empty final answer", bool(r.final)),
        Check("planner completed", _present_complete(s, "planner")),
        Check("formatter completed", _present_complete(s, "formatter")),
        Check("researcher present", "researcher" in s, note=f"shape={s}"),
        Check("node count in 3..9", 3 <= _total_nodes(s) <= 9, fatal=False,
              note=f"nodes={_total_nodes(s)}"),
        Check("mentions Shannon", bool(re.search(r"shannon", final, re.I))),
        Check("mentions a key date (1916 or 2001)",
              ("1916" in final) or ("2001" in final), note="content is truncated to 600 chars"),
        Check("distiller present", "distiller" in s, note=f"shape={s}"),
        Check("critic auto-inserted", "critic" in s, note=f"shape={s}"),
    ]


def evaluate_populations(r: RunResult, s: dict[str, list[str]]) -> list[Check]:
    final = (r.final or "").lower()
    cities_hit = sum(c in final for c in ("london", "paris", "berlin"))
    return [
        Check("did not time out", not r.timed_out),
        Check("non-empty final answer", bool(r.final)),
        Check("formatter completed", _present_complete(s, "formatter")),
        Check(">= 2 researchers (parallel fan-out)",
              len(s.get("researcher", [])) >= 2, note=f"researchers={s.get('researcher')}"),
        Check(">= 2 of the 3 cities named", cities_hit >= 2, note=f"hit={cities_hit}"),
        Check("coder present", "coder" in s, fatal=False),
        Check("sandbox_executor present", "sandbox_executor" in s, fatal=False),
        Check("node count near 7", 5 <= _total_nodes(s) <= 12, fatal=False,
              note=f"nodes={_total_nodes(s)} shape={s}"),
    ]


def evaluate_graceful(r: RunResult, s: dict[str, list[str]]) -> list[Check]:
    final = r.final or ""
    inaccessible = re.search(
        r"(inaccess|not\s*exist|no such|cannot|unable|nonexistent|not found|/nonexistent)",
        final, re.I,
    )
    read_skills = {k: v for k, v in s.items() if k in ("researcher", "retriever")}
    return [
        Check("did not time out", not r.timed_out),
        Check("exited cleanly (rc == 0)", r.returncode == 0, note=f"rc={r.returncode}"),
        Check("non-empty final answer", bool(r.final)),
        Check("planner completed", _present_complete(s, "planner")),
        Check("formatter present", "formatter" in s, note=f"shape={s}"),
        Check("answer explains inaccessibility", bool(inaccessible),
              note=repr(final[:120])),
        Check("no read/retrieval node dispatched", not read_skills,
              note=f"read_skills={read_skills}"),
    ]


def evaluate_resumable(
    sid: str, pre: dict[str, list[str]], post: dict[str, list[str]], r: RunResult
) -> list[Check]:
    final = r.final or ""
    return [
        Check("session id captured", bool(sid)),
        Check("caught mid-flight before kill", _midflight(pre), note=f"pre={pre}"),
        Check("planner persisted across SIGKILL", _present_complete(post, "planner"),
              note=f"post={post}"),
        Check("resume did not time out", not r.timed_out),
        Check("resumed run produced an answer", bool(r.final)),
        # Advisory, not fatal: query K's architectural claim is "survives a
        # SIGKILL and resumes", not "computes the right city". Which city grows
        # fastest depends on live, changing growth-rate data plus an LLM
        # comparison, so a fatal check here would make the resume test flaky for
        # a reason unrelated to resumability.
        Check("answer names Lagos (fastest-growing)",
              bool(re.search(r"lagos", final, re.I)), fatal=False,
              note="LLM + live-data dependent; see plan caveats"),
    ]


# ── Standalone board ───────────────────────────────────────────────────────
@dataclass
class QueryReport:
    key: str
    checks: list[Check] = field(default_factory=list)
    skipped: bool = False
    error: str = ""

    @property
    def passed(self) -> bool:
        return not self.error and all(c.ok for c in self.checks if c.fatal)


def run_and_check(key: str, *, run_network: bool) -> QueryReport:
    """Execute one query and evaluate it; used by the standalone board."""
    query, needs_net = QUERIES[key]
    if needs_net and not run_network:
        return QueryReport(key, skipped=True)
    try:
        if key == "K_resumable":
            sid, pre, post, r = run_kill_resume(query)
            checks = evaluate_resumable(sid, pre, post, r)
        else:
            timeout = 240.0 if needs_net else 180.0
            r = run_query_blocking(query, timeout=timeout)
            s = shape(r.sid) if r.sid else {}
            checks = {
                "hello": evaluate_hello,
                "A_shannon": evaluate_shannon,
                "I_populations": evaluate_populations,
                "J_graceful": evaluate_graceful,
            }[key](r, s)
        return QueryReport(key, checks=checks)
    except Exception as e:  # noqa: BLE001 — board must report, not crash
        return QueryReport(key, error=f"{type(e).__name__}: {e}")


def _print_report(rep: QueryReport) -> None:
    if rep.skipped:
        print(f"  SKIP  {rep.key}  (set S8_RUN_NETWORK=1 to run)")
        return
    if rep.error:
        print(f"  ERROR {rep.key}: {rep.error}")
        return
    verdict = "PASS" if rep.passed else "FAIL"
    print(f"  {verdict}  {rep.key}")
    for c in rep.checks:
        mark = "ok " if c.ok else ("XX " if c.fatal else "~~ ")
        tail = f"  ({c.note})" if c.note else ""
        print(f"        {mark}{c.name}{tail}")


def main() -> int:
    run_network = os.environ.get("S8_RUN_NETWORK") == "1"
    print("=" * 78)
    print(f"S8 worked-query board   (network queries {'ON' if run_network else 'OFF'})")
    print("=" * 78)
    reports = [run_and_check(k, run_network=run_network) for k in QUERIES]
    for rep in reports:
        _print_report(rep)
    ran = [r for r in reports if not r.skipped]
    failed = [r for r in ran if not r.passed]
    print("-" * 78)
    print(f"ran {len(ran)}  passed {len(ran) - len(failed)}  failed {len(failed)}  "
          f"skipped {len(reports) - len(ran)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
