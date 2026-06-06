The five base queries are **sanity/regression tests** for the Session 8 DAG orchestrator. They are not the “creative” part of the assignment; they prove your implementation still passes the known behaviors from the session before you add your own fan-out, Critic, Coder, and new skill demos. 

## The five base queries

| Label   | Verbatim query                                                                                                                                  | What it tests                              |
| ------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------ |
| `hello` | `Say hello.`                                                                                                                                    | Minimum possible DAG                       |
| `A`     | `Fetch https://en.wikipedia.org/wiki/Claude_Shannon and tell me his birth date, death date, and three key contributions to information theory.` | Research → distill → critic → format       |
| `I`     | `Find the populations of London, Paris, Berlin and tell me which two are closest in size.`                                                      | Parallel fan-out + Coder + SandboxExecutor |
| `J`     | `Read /nonexistent/path.txt and tell me what's in it.`                                                                                          | Graceful failure / fail-fast planning      |
| `K`     | `For Lagos, Cairo, and Kinshasa, find current populations and growth rates and tell me which is growing fastest.`                               | Persistence + resume after kill            |

---

## `hello`: minimum DAG

```text
Say hello.
```

Expected shape:

```text
planner → formatter
```

This proves the orchestrator can run the simplest possible DAG. The Planner should realize no research, retrieval, distillation, coding, or critic is needed. It should emit only a terminal Formatter node.

What to show in logs:

```text
n:1 planner complete
n:2 formatter complete
final answer: hello-style response
```

This is mostly a smoke test: “Can the Planner emit a valid graph, and can the Executor run it?”

---

## `A`: Claude Shannon Wikipedia query

```text
Fetch https://en.wikipedia.org/wiki/Claude_Shannon and tell me his birth date, death date, and three key contributions to information theory.
```

Expected shape:

```text
planner → researcher → distiller → critic → formatter
```

This tests the normal sequential research pipeline.

The Researcher should fetch the URL. The Distiller should extract structured facts: birth date, death date, and three contributions. Because `distiller` is marked with `critic: true`, the orchestrator should auto-insert a Critic between the Distiller and Formatter.

What this proves:

```text
Researcher can fetch source content.
Distiller can extract fields.
Critic insertion works.
Formatter can render final answer.
```

This query is not expected to show much parallelism because the steps depend on each other.

---

## `I`: London, Paris, Berlin populations

```text
Find the populations of London, Paris, Berlin and tell me which two are closest in size.
```

Expected shape:

```text
planner
 ├─ researcher: London
 ├─ researcher: Paris
 └─ researcher: Berlin
      ↓
    coder
      ↓
 ├─ formatter
 └─ sandbox_executor
```

This is the key Session 8 fan-out example.

The Planner should emit **three independent Researcher nodes**, one per city. The Executor should run those concurrently. After all three finish, the Coder computes the pairwise differences, and the SandboxExecutor verifies the Python computation.

What this proves:

```text
The Planner decomposes concrete items into separate nodes.
The Executor runs independent nodes concurrently.
The Coder emits executable Python.
The SandboxExecutor can verify arithmetic.
```

In the session notes, this query is used to show that the parallel layer costs the **maximum branch time**, not the sum of branch times.

So your README should include something like:

```text
London researcher: 40.50s
Paris researcher: 36.89s
Berlin researcher: 32.43s

Parallel layer wall-clock ≈ 40.50s
Serial sum would be ≈ 109.82s
```

---

## `J`: nonexistent file path

```text
Read /nonexistent/path.txt and tell me what's in it.
```

Expected shape:

```text
planner → formatter
```

or possibly:

```text
planner → file/action attempt → formatter
```

The session says the Planner may recognize that the request is impossible and fail fast. In the documented run, the Planner emitted a Formatter directly with a failure note, and no tool was dispatched.

What this proves:

```text
The system handles unanswerable requests gracefully.
The Planner can create a degenerate DAG.
The Executor does not crash on failure-style answers.
```

The important thing is not that it reads the file. It should not be able to. The important thing is that it gives a clear failure answer instead of looping or crashing.

---

## `K`: resumable execution

```text
For Lagos, Cairo, and Kinshasa, find current populations and growth rates and tell me which is growing fastest.
```

Expected initial shape:

```text
planner
 ├─ researcher: Lagos
 ├─ researcher: Cairo
 └─ researcher: Kinshasa
      ↓
    coder
      ↓
 ├─ formatter
 └─ sandbox_executor
```

This is similar to query `I`, but it is used to prove persistence and resume.

The intended demo is:

1. Start the run.
2. Kill the process while one or more Researcher nodes are running.
3. Resume using the session ID.
4. Show that completed nodes are not rerun.
5. Show that `running` nodes are reset to `pending`.
6. Show that the graph completes after resume.

What this proves:

```text
Graph state is persisted.
Node state is persisted.
Resume can recover from a killed process.
Completed nodes remain complete.
Running nodes are safely retried.
```

The final answer in the session identifies Lagos as the fastest-growing city, but for your assignment the architectural proof matters more than the exact fact.

---

## How to think about them together

The five base queries cover different architectural paths:

```text
hello = minimum graph
A     = sequential research + distillation + critic
I     = parallel fan-out + computation
J     = graceful failure
K     = persistence and resume
```

So in your README, do not just paste final answers. For each query, show:

```text
- exact query
- session id
- graph nodes
- node statuses
- wall-clock time
- final answer
- log path
- whether it stayed within the expected bounds
```

The assignment asks you to pass these five **verbatim** because they are the regression suite for the Session 8 architecture. Your own custom queries come after this.
