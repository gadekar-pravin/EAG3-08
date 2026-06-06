# Cricket Auction Strategy Agent

## Project Idea

Build a DAG-based IPL-style auction strategist. Given a shortlist of players and a fixed auction budget, the agent researches players independently, extracts structured profiles, computes value-for-money combinations, and recommends an auction strategy.

The project demonstrates the Session 8 architecture: Planner emits the DAG, Executor runs independent nodes concurrently, Critic verifies structured outputs, Coder emits Python for SandboxExecutor verification, and a new `auction_strategist` skill is added through YAML and a prompt file without Executor modification.

## Main Auction Query

```text
Compare Jasprit Bumrah, Rashid Khan, Andre Russell, and Suryakumar Yadav for an IPL-style auction. For each player, research primary role, recent form, injury or fitness risk, match impact, role scarcity, and estimated auction value. With a budget of 19 crore, recommend the best two-player purchase strategy.
```

## Expected DAG

```text
planner
 ├─ researcher: Jasprit Bumrah
 ├─ researcher: Rashid Khan
 ├─ researcher: Andre Russell
 └─ researcher: Suryakumar Yadav
      ↓
   distiller
      ↓
  [critic auto-inserted if distiller has critic: true]
      ↓
   coder
      ├─ sandbox_executor        # verification dead-end
      └─ auction_strategist
             ↓
          formatter
```

The `sandbox_executor` verifies the Coder’s Python output. It does not feed the final answer path. The answer path is `coder → auction_strategist → formatter`.

## Parallel Fan-Out Proof

The four player research tasks are independent. The Planner should emit one `researcher` node per player. The README will show actual logs proving that the research layer wall-clock is approximately the maximum branch time, not the sum.

Example format:

```text
Bumrah researcher:       actual log time
Rashid researcher:       actual log time
Russell researcher:      actual log time
Suryakumar researcher:   actual log time

Parallel layer wall-clock: max(branches)
Sequential equivalent: sum(branches)
```

## Coder Demo

Use controlled numeric inputs so the arithmetic is reproducible.

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

Formula:

```text
value_score =
  0.30 * recent_form
+ 0.30 * match_impact
+ 0.20 * role_scarcity
+ 0.10 * fitness
+ 0.10 * price_efficiency
```

Expected result:

```text
Rashid + Bumrah are the top two individual players but cost 20 crore, so they exceed the 19 crore budget.

Best valid pair:
Rashid + Russell
Combined score: 16.7
Total cost: 16 crore
```

The SandboxExecutor should run the Coder’s Python and verify the combination search.

## New Skill: auction_strategist

Add one new YAML skill:

```yaml
auction_strategist:
  prompt: prompts/auction_strategist.md
  tools_allowed: []
  temperature: 0.3
  max_tokens: 1200
```

The exact key should match the starter code’s `agent_config.yaml`.

The strategist does not redo arithmetic. It consumes Coder output and produces a structured auction decision object:

```json
{
  "recommended_buy": ["Rashid Khan", "Andre Russell"],
  "total_estimated_spend_crore": 16,
  "why_this_pair": "...",
  "backup_strategy": "...",
  "avoid_or_do_not_overpay": ["..."],
  "major_risks": ["..."],
  "confidence": "medium"
}
```

Formatter then renders this object into the final answer.

## Separate Critic Demo

Use a separate controlled query for Critic pass/fail.

Property checked:

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

Pass run:

* Source includes all fields.
* Distiller extracts all fields.
* Auto-inserted Critic returns pass.
* Formatter outputs the card.

Fail run:

* Source genuinely omits `fitness_score`.
* Distiller leaves `fitness_score` empty or missing.
* Auto-inserted Critic returns fail and names `fitness_score`.
* Recovery Planner emits targeted research for Bumrah fitness/injury status.
* Distiller merges the recovered evidence.
* Critic passes.
* Formatter outputs corrected card.

## README Proof Checklist

1. Base queries:

   * hello
   * A
   * I
   * J
   * K

2. Parallel fan-out:

   * auction query
   * four researcher nodes
   * real timing logs
   * max(branches), not sum(branches)

3. Critic:

   * pass run
   * fail run
   * recovery Planner node
   * corrected answer

4. Coder:

   * completed `prompts/coder.md`
   * Python emitted by Coder
   * SandboxExecutor output
   * best feasible pair under budget

5. New skill:

   * `auction_strategist` in `agent_config.yaml`
   * `prompts/auction_strategist.md`
   * graph contains `auction_strategist`
   * no Executor change

## What NOT to touch

- `agent7_s7_carryover.py` (if present) — the Session 7 single-loop agent kept for reference. Out of scope.
- `perception.py`, `decision.py`, `action.py`, `memory.py`,
  `vector_index.py`, `artifacts.py`, `mcp_server.py` — carry over
  byte-identical from Session 7. The tool-blindness contract on
  Perception depends on these staying as-is.