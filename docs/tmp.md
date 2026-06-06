Because `flow.py` **auto-starts the gateway** if nothing is listening on `:8108`, the simplest correct answer is a single command — but I'll give you both the one-liner and the explicit two-process version.

## Option A — one command (recommended)

`flow.py` brings up the gateway itself, so this is all you need:

```bash
cd /Users/pravingadekar/Documents/EAG3/EAG3-08/EAG3-S8/code && uv run python flow.py "your query here"
```

## Option B — launch the gateway explicitly, then the agent

Use this if you want the gateway as its own visible process (e.g. to watch its logs separately):

```bash
# 1. Start the gateway in the background (logs to gateway.log)
cd /Users/pravingadekar/Documents/EAG3/EAG3-08/EAG3-S8/gateway && uv run main.py > gateway.log 2>&1 &

# 2. Wait until it's actually accepting requests, then run the agent
until curl -sf -m 2 http://localhost:8108/v1/status >/dev/null; do sleep 0.5; done && \
cd /Users/pravingadekar/Documents/EAG3/EAG3-08/EAG3-S8/code && uv run python flow.py "your query here"
```

`★ Insight ─────────────────────────────────────`
- **The `until curl ... /v1/status` loop matters.** The gateway needs a moment to bind the port and load providers. Polling a real route (`/v1/status` returns 200; note `/health` does *not* exist and returns 404) avoids the classic race where the agent fires its first `/v1/chat` before the server is ready. A blind `sleep 5` is fragile by comparison.
- **`> gateway.log 2>&1 &` keeps the gateway detached** so your shell is free for the agent, while still capturing its output. The auto-start path in Option A instead manages the gateway as a child of `flow.py`.
`─────────────────────────────────────────────────`

## Important caveats for your machine right now

1. **The gateway is already running** (PID 27813 on `:8108`). If you run Option B, the second `uv run main.py` will fail with an "address already in use" error. Either use **Option A** (it detects the running gateway and reuses it), or stop the existing one first with `kill 27813`.
2. **Resume an existing session** instead of a fresh run:
   ```bash
   cd /Users/pravingadekar/Documents/EAG3/EAG3-08/EAG3-S8/code && uv run python flow.py --resume <session_id> "follow-up query"
   ```

Want me to actually run one of these with a specific query?

------------

Here's the command. Run it from the repo so `uv` picks up the `code/` project:

```bash
cd /Users/pravingadekar/Documents/EAG3/EAG3-08/EAG3-S8/code && S8_RUN_NETWORK=1 uv run pytest tests/test_e2e_flow.py -v -s
```

What each piece does:

- **`S8_RUN_NETWORK=1`** — satisfies the second skip-guard. The 3 live tests need *both* this env var **and** their `@pytest.mark.network` marker, so a bare `pytest` never spends tokens.
- **no `-m` filter** — runs **all 5** tests: the 2 offline (`hello`, graceful-failure) + the 3 network (Shannon, populations, resumable).
- **`-v`** — verbose: one line per test with PASS/FAIL.
- **`-s`** — disables pytest output capture so you see the live `print()` stream in your terminal: captured session ids, node counts, DAG shapes, and final-answer snippets as each query runs.

Two notes:
- It needs internet + spends Gemini tokens, and `flow.py` auto-starts the gateway on `:8108` if it isn't already up.
- If you want the standalone PASS/FAIL board instead of pytest, the equivalent is:
  ```bash
  cd /Users/pravingadekar/Documents/EAG3/EAG3-08/EAG3-S8/code && S8_RUN_NETWORK=1 uv run python tests/e2e_runner.py
  ```

By the way — I still have that same suite running in the background from the previous step (currently on `test_03_shannon`). If you're going to run it yourself in your terminal, I can kill the background run so the two don't compete for the shared gateway on `:8108`. Want me to stop it?


---

uv run python flow.py "Compare Jasprit Bumrah, Rashid Khan, Andre Russell, and Suryakumar Yadav for an IPL-style auction. For each player, research primary role, recent form, injury or fitness risk, match impact, role scarcity, and estimated auction value. With a budget of 19 crore, recommend the best two-player purchase strategy."

uv run python flow.py -v "Hello"
 
uv run python flow.py -v "Fetch https://en.wikipedia.org/wiki/Claude_Shannon and tell me his birth date, death date, and three key contributions to information theory."
 
uv run python flow.py -v "Find the populations of London, Paris, Berlin and tell me which two are closest in size."

uv run python flow.py -v "Read /nonexistent/path.txt and tell me what's in it."
uv run python flow.py -v "Read /Users/pravin/Documents/bad�name.txt and tell me what's in it."


uv run python flow.py -v "For Lagos, Cairo, and Kinshasa, find current populations and growth rates and tell me which is growing fastest."
uv run python flow.py -v --resume s8-e54bae3c

----
uv run python flow.py "Controlled critic fail demo: First extract an IPL auction player card only from this inline source text and render the card. Source text: player_name=Jasprit Bumrah; primary_role=fast bowler and death-overs specialist; recent_form_score=9; match_impact_score=10; role_scarcity_score=9; price_efficiency_score=6; estimated_price_crore=11; auction_risk=fitness status not provided. The source intentionally omits fitness_score; do not infer or invent fitness_score from auction_risk or any outside knowledge."

