# Technical Specification: Cricket Auction Strategy Agent

## 1. Purpose

This document specifies the Session 8 assignment implementation for a
DAG-based IPL-style auction strategist. The system accepts a shortlist of
players and a fixed auction budget, researches each player independently,
extracts structured player profiles, verifies a value-for-money computation in
the sandbox, and returns a recommended two-player auction strategy.

The implementation must be player-agnostic. Users can provide any IPL-style
player shortlist and budget. The named four-player query below is the canonical
assignment fixture used for deterministic regression checks, not the only
supported player list.

The implementation must demonstrate the Session 8 growing-graph architecture:

- The Planner emits a DAG of typed skill nodes.
- The Executor runs independent ready nodes concurrently with `asyncio.gather`.
- Edges carry typed `AgentResult` payloads between nodes.
- Structured extraction is verified by the Critic when a skill has
  `critic: true`.
- The Coder emits Python that the SandboxExecutor runs for verification.
- A new `auction_strategist` skill is added through YAML and a prompt file,
  without modifying the Executor.

## 2. Current Architecture Contract

The agent runtime is a NetworkX `DiGraph` managed by `code/flow.py`. Each node
has a skill name, inputs, metadata, and status. Node outputs follow the
`AgentResult` schema from `code/schemas.py`; dynamically-added work is described
with `NodeSpec`.

Graph growth occurs through these mechanisms:

- Planner-created `NodeSpec.successors`.
- Skill-created dynamic successors.
- Static `internal_successors` from `code/agent_config.yaml`.
- Critic auto-insertion for outgoing edges from skills configured with
  `critic: true`.
- Recovery Planner nodes produced by failure handling in `code/recovery.py`.

The runtime limits are part of the assignment contract and should remain in
force:

- `MAX_NODES = 60` in `code/flow.py`.
- `MAX_TOOL_HOPS = 6` in `code/mcp_runner.py`.
- `MAX_RECOVERY_REPLANS = 3` in `code/recovery.py`.
- Sandbox execution uses the existing timeout and stdout/stderr caps in
  `code/sandbox.py`.

Session state is persisted under `code/state/sessions/<session_id>/`, including
graph state and per-node records. `code/replay.py` is the primary inspection
tool for validating prompts, node outputs, graph shape, and recovery behavior.

## 3. Main Auction Query And Generic Support

The canonical assignment fixture query is:

```text
Compare Jasprit Bumrah, Rashid Khan, Andre Russell, and Suryakumar Yadav for an IPL-style auction. For each player, research primary role, recent form, injury or fitness risk, match impact, role scarcity, and estimated auction value. With a budget of 19 crore, recommend the best two-player purchase strategy.
```

The same workflow must also support arbitrary player names and budgets, for
example:

```text
Compare Virat Kohli, Travis Head, and Axar Patel for an IPL-style auction. For each player, research primary role, recent form, injury or fitness risk, match impact, role scarcity, and estimated auction value. With a budget of 15 crore, recommend the best two-player purchase strategy.
```

The expected graph shape is:

```text
planner
  |- researcher: player 1
  |- researcher: player 2
  |- researcher: ...
  |- researcher: player N
       -> distiller
       -> critic
       -> coder
          |- sandbox_executor
          -> auction_strategist
             -> formatter
```

The exact internal node IDs may differ. The required behavioral properties are:

- The Planner emits one independent `researcher` branch per player.
- The four Researcher nodes are ready in the same execution wave and run in
  parallel.
- The Distiller produces structured player profile data.
- The Critic is auto-inserted because `distiller` has `critic: true`.
- The Coder produces executable Python for the controlled combination search.
- `sandbox_executor` verifies the Coder output as a static internal successor.
- The final answer path is `coder -> auction_strategist -> formatter`.
- `sandbox_executor` is a verification dead-end and must not be required for the
  final answer path.
- The Planner derives player names and the budget from `USER_QUERY`; it must
  not inject the canonical fixture player names into non-fixture queries.
- Distiller and Coder use the requested players' researched evidence and
  explicit query context for arbitrary-player runs. The fixture's controlled
  scores apply only to the canonical assignment fixture.

## 4. Parallel Fan-Out Requirement

The four player research tasks are independent and must be represented as
separate Researcher nodes. The README evidence must show that the research
layer takes approximately the maximum branch duration, not the sum of all
branch durations.

Required proof format:

```text
Bumrah researcher:       actual log time
Rashid researcher:       actual log time
Russell researcher:      actual log time
Suryakumar researcher:   actual log time

Parallel layer wall-clock: max(branches)
Sequential equivalent: sum(branches)
```

Acceptance criteria:

- Runtime logs show four Researcher nodes for the four named players.
- Those nodes appear in the same ready layer before downstream aggregation.
- The README records real timings from an actual run.
- The reported parallel wall-clock is based on the slowest branch, not the
  arithmetic sum.

## 5. Coder And SandboxExecutor Contract

The Coder must emit Python code that can be run by the existing
SandboxExecutor. The Coder must not rely on hidden state or non-deterministic
inputs for the assignment arithmetic demo.

For arbitrary IPL auction queries, the Coder reads `fields.player_cards` from
the Distiller, derives the budget from `USER_QUERY`, computes every two-player
pair with the assignment scoring formula, rejects pairs over budget, and
selects the highest-scoring feasible pair. It must use the actual player names,
scores, and prices from upstream player cards, not the canonical fixture data.

For the canonical fixture only, the controlled inputs are:

```text
Budget: 19 crore

Bumrah:
recent_form=9, match_impact=10, role_scarcity=9, fitness=8, price_efficiency=6, estimated_price_crore=11

Rashid:
recent_form=9, match_impact=9, role_scarcity=10, fitness=9, price_efficiency=7, estimated_price_crore=9

Russell:
recent_form=7, match_impact=9, role_scarcity=8, fitness=5, price_efficiency=8, estimated_price_crore=7

Suryakumar:
recent_form=8, match_impact=8, role_scarcity=6, fitness=8, price_efficiency=7, estimated_price_crore=8
```

The score formula is:

```text
value_score =
  0.30 * recent_form
+ 0.30 * match_impact
+ 0.20 * role_scarcity
+ 0.10 * fitness
+ 0.10 * price_efficiency
```

The expected result is:

```text
Rashid + Bumrah are the top two individual players but cost 20 crore, so they exceed the 19 crore budget.

Best valid pair:
Rashid + Russell
Combined score: 16.7
Total cost: 16 crore
```

Acceptance criteria:

- `code/prompts/coder.md` is completed so the Coder emits the expected JSON or
  text shape required by the existing SandboxExecutor.
- The Coder output contains executable Python for the pair search.
- The SandboxExecutor runs that Python and reports stdout, stderr, exit code,
  and any generated files using the existing sandbox contract.
- The verified best feasible pair is Rashid Khan and Andre Russell.
- The verified total spend is 16 crore.
- The verified combined score is 16.7.

Important graph contract:

- `coder` keeps `internal_successors: [sandbox_executor]` in
  `code/agent_config.yaml`.
- The Coder skill must not emit `sandbox_executor` in its own dynamic
  `successors`, because doing so would run the SandboxExecutor twice.

## 6. New Skill: `auction_strategist`

Add one new skill entry in `code/agent_config.yaml`:

```yaml
auction_strategist:
  prompt: prompts/auction_strategist.md
  tools_allowed: []
  temperature: 0.3
  max_tokens: 1200
```

Add the prompt file at `code/prompts/auction_strategist.md`.

The strategist consumes the Coder's verified computation and the distilled
player context. It does not redo the arithmetic. It converts the verified
combination result into a structured auction decision object. For
arbitrary-player queries, every recommended player name, spend value, score,
and over-budget warning comes from the Coder output for that query.

Required output shape:

```json
{
  "recommended_buy": ["<player from Coder output>", "<player from Coder output>"],
  "total_estimated_spend_crore": 0,
  "why_this_pair": "...",
  "backup_strategy": "...",
  "avoid_or_do_not_overpay": ["..."],
  "major_risks": ["..."],
  "confidence": "medium"
}
```

Acceptance criteria:

- `auction_strategist` is present in `code/agent_config.yaml`.
- `code/prompts/auction_strategist.md` exists.
- `tools_allowed` is an empty list.
- The graph for the main auction query contains an `auction_strategist` node.
- The Formatter receives the strategist output and renders the final answer.
- No Executor, scheduler, or graph-extension code changes are required solely
  to add this skill.

## 7. Critic Pass/Fail Demo

The assignment requires a separate controlled Critic demonstration. The checked
property is:

```text
All required player-card fields must be present and non-empty.
```

Required fields:

```text
player_name
primary_role
recent_form_score
match_impact_score
role_scarcity_score
fitness_score
price_efficiency_score
estimated_price_crore
auction_risk
```

Pass scenario:

- Source includes every required field.
- Distiller extracts every required field.
- The auto-inserted Critic returns pass.
- Formatter outputs the player card.

Fail and recovery scenario:

- Source genuinely omits `fitness_score`.
- Distiller leaves `fitness_score` empty or missing.
- The auto-inserted Critic returns fail and names `fitness_score`.
- Recovery Planner emits targeted research for Bumrah fitness or injury status.
- Distiller merges the recovered evidence.
- Critic returns pass after recovery.
- Formatter outputs the corrected player card.

Acceptance criteria:

- Replay evidence shows the failing Critic verdict and rationale.
- Replay evidence shows the recovery Planner node.
- The recovered branch includes targeted research for the missing field.
- The corrected output contains all required fields.
- Recovery remains within `MAX_RECOVERY_REPLANS`.

## 8. README Proof Checklist

The final README evidence must include the following proof points.

Base queries:

- `hello`
- `A`
- `I`
- `J`
- `K`

Parallel fan-out:

- The main auction query.
- Four Researcher nodes, one per player.
- Real timing logs.
- Explicit max-branch timing compared with sequential sum.

Critic:

- Pass run.
- Fail run.
- Recovery Planner node.
- Corrected final answer.

Coder:

- Completed `prompts/coder.md`.
- Python emitted by the Coder.
- SandboxExecutor output.
- Best feasible pair under the 19 crore budget.

New skill:

- `auction_strategist` in `agent_config.yaml`.
- `prompts/auction_strategist.md`.
- Graph evidence containing `auction_strategist`.
- Confirmation that no Executor change was needed.

For each evidence run, record:

- Exact query.
- Session ID.
- Graph nodes and node statuses.
- Final answer.
- Relevant timing or replay output.
- Log path under `code/state/sessions/<session_id>/` when applicable.

## 9. Out Of Scope

Do not modify these Session 7 carryover modules for this assignment:

- `agent7_s7_carryover.py`, if present.
- `perception.py`
- `decision.py`
- `action.py`
- `memory.py`
- `vector_index.py`
- `artifacts.py`
- `mcp_server.py`

The Perception layer must remain tool-blind. Planner nodes name skills, not
tools, and the downstream skill configuration controls tool access.

The gateway is treated as an external service for this assignment. Routine
assignment work should not require gateway changes.

## 10. Implementation Summary

The implementation is complete when:

- The main auction query produces the required graph shape.
- Independent player research branches run concurrently.
- The Coder/SandboxExecutor path verifies the deterministic pair search.
- The new `auction_strategist` skill produces a structured decision object.
- The Formatter renders the strategist decision as the final answer.
- Critic pass and fail recovery demos are captured.
- README proof covers the base queries and assignment-specific evidence.
