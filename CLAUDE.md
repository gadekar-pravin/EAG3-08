# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Educational multi-agent "growing-graph" orchestrator (EAGV3 Session 8). A graph of typed
skills (Planner, Researcher, Distiller, Critic, Coder, SandboxExecutor, …) where nodes
execute in parallel via `asyncio.gather` and the DAG grows at runtime. The assignment is to
implement the **Coder** skill (`code/prompts/coder.md`). Read `code/flow.py` (~300 lines)
top-to-bottom before changing orchestration logic.

## Two separate projects — `uv` only, never `pip`

This is a monorepo with **two independent `uv` projects**, each with its own `pyproject.toml`
and `uv.lock`. Always `cd` into the right one first.

- `code/` — the agent orchestrator + CLI.
- `gateway/` — FastAPI LLM gateway (LLM Gateway V8), a service on `:8108`.

```bash
cd gateway && uv sync && cd ..      # install gateway deps
cd code    && uv sync && cd ..      # install agent deps
```

## Running — the gateway must be up first

The agent will fail with `[gateway] launching … failed to start within 45s` if the gateway
isn't listening on `:8108`. Start it in its own terminal before running the agent.

```bash
cd gateway && uv run main.py        # boots http://localhost:8108 ; /v1/routers should answer
cd code && uv run python flow.py "hello"   # run the agent (separate terminal)
cd code && uv run python replay.py <sid>   # inspect a session trace (prompt_sent = exact bytes sent)
```

Requires Python 3.11+, a `.env` (copy `.env.example`), at least one LLM provider key, and
Ollama for embeddings (`ollama pull nomic-embed-text`).

## Tests

`pytest` with `pytest-asyncio` (`asyncio_mode = "auto"`, so no `@pytest.mark.asyncio` needed).
Run from inside `code/` (or `gateway/`). Custom markers in `code/`: `network` (needs internet),
`embed` (needs the gateway embedding endpoint). Skip them with `-m "not network and not embed"`.
Single test: `uv run pytest tests/test_recovery.py::<name>`.

## Critical gotchas

- **Coder output is strict JSON** — emit exactly `{"code": "...", "rationale": "..."}` with no
  markdown fences. Fences cause `no code in upstream coder output` and a re-plan.
- **Do not touch S7 carryover files** — `memory.py`, `vector_index.py`, `artifacts.py`,
  `perception.py`, `decision.py`, `action.py` are byte-identical from Session 7 and out of scope.
- **The sandbox (`code/sandbox.py`) is a usability boundary, not a security one** — no chroot,
  container, or syscall filter. Don't treat executed code as isolated.
- **`code/state/sessions/` is git-ignored runtime state** — regenerated each run; don't commit it.
- No CI, no Makefile, no autoformatter is wired. Codebase is typed (Pydantic); match existing style.
