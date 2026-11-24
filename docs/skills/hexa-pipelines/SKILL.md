---
name: hexa-pipelines
description: Write, review or refactor Hexa pipelines in JavaScript or Python. Stages are generators composed with combinators that yield inert effects, run by a runtime under policies, with a ledger and replayable journal. Use for extraction, normalisation and projection pipelines (bridges, CRM/API mappings), their stages, policies, retries, HTTP access, fixtures and tests. Define the data with hexa-shapes first.
---

# Hexa pipelines

A pipeline turns source data into the records a caller receives: stages retrieve,
select and project data described by shapes. This skill covers the stage half; load
**hexa-shapes** for shapes, slots, codecs, templates and codes. The specs in
`docs/specs/` decide disputes.

| Need | Read |
| --- | --- |
| Concepts, naming, data vs configuration | [spec-001](../../specs/spec-001-shape_pipelines.md) |
| Stages, combinators, effects, failures, policies, retry, ledger, replay, testing | [spec-005](../../specs/spec-005-shape_pipelines_stages.md) |
| JavaScript syntax | [spec-002](../../specs/spec-002-shape_pipelines_javascript.md) |
| Python 3.14+ syntax | [spec-003](../../specs/spec-003-shape_pipelines_python.md) |
| A complete program with fixtures | [examples/mortgage-people](../../../examples/README.md) |

Both bindings are implemented: `hexa/combinators.js`, `hexa/runtime.js`
(tests: `make test-js`) and `hexa.combinators`, `hexa.runtime` (tests:
`make test-py`). Report which binding and tests were actually exercised; do not
claim full draft-spec conformance from them.

## Workflow

1. **Shapes first.** Output template, slots, codecs, source shapes and codes, as in
   hexa-shapes.
2. **Compose.** Build stages from combinators: `guard`, `select`, `each(project(…))`,
   `choose`, `recover`, `retry`, `collect`, `always`. Hand-write a generator stage
   only to yield effects or fan in earlier outputs.
3. **Declare the pipeline.** Diagram plus a `Stage | Input → output | Policy` table,
   at the top of the module or in its README.
4. **Fixtures and tests.** One example per policy and failure path; a recorded
   journal for the pipeline.

Prefer a combinator to a hand-written stage.

## Rules

### Configuration vs data

- Kinds, policies and codes are symbols: `Failure.Gap`, `Policy.Drop`. Never a
  string literal, never compared against a data string (see hexa-shapes).
- Data (ledger, journal, fixtures) holds names: `"kind": "Gap"`,
  `"target": "MortgageClientsProject"`.

### Naming

- Stages are `Namespace? Subject Object Verb`, verb last, from the spec-005 verb table
  (`Validate`, `Retrieve`, `Select`, `Project`, `Normalize`, `Summarize`, `Publish`, …):
  `MortgageClientsSelect`, never `SelectMortgageClients`.
- Operations are `Subject Verb` (`RequestSend`, `DelayWait`).
- Pure helpers are not stages: no `env`, no `yield`, language-native naming
  (camelCase in JS and Python).

### Stages and effects

- `env` first, every other input explicit. Read nothing from module state except
  shapes, slots, templates and stages. No I/O, clocks, waits or randomness except by
  yielding effects.
- `yield env.Operation(...)` yields an inert description; calling it without `yield`
  does nothing. Delegate with `yield*` / `yield from`.
- Concurrency only through `yield join(...)`, `race(...)`, `fork(...)`. Never `await`,
  `Promise.all`, `asyncio.gather` or a native promise/coroutine in a stage.
- Request paths always through `p`: `` p`/mortgages/${Id}` `` (JS),
  `p(t"/mortgages/{Id}")` (Python). Never a plain template literal or f-string.
- Pass effect results through unchanged to keep `$Origin`.
- A stage does one projection step; if it needs headings, split it. `pipe` is for
  unary chains; fan-in is a hand-written stage.
- Declare every stage in the diagram in one stage namespace per pipeline, so each
  name is written once: `stages(S => ({ … }))` in JS, `class People(stages):` in
  Python. Never write `X = stage("X", …)`; `stage(name, …)` is only for stages built
  programmatically with computed names, and `stage(Contract, S)` adds a contract.
- Declare siblings before use (the entry point last). Hosts run
  `People.PeopleExtract`; aliasing a stage under another name is
  `Runtime.StageRenamed`.
- Nest unnamed combinators at most one level inside a named one.
- Accumulators are created per run by `fold` or one owner stage and passed
  explicitly; never in `env`, module scope, closures or forked strands.

### HTTP

- The host puts `http({base, ...})` (`hexa/http.js`, `hexa.http`) in the
  environment: `RequestSend` returns `{status, data}` and fails with `Request`
  codes (transient ones are retried); the details are in the journal.

### Failures and policies

| Kind | Recoverable | Typical source |
| --- | --- | --- |
| `Contract`, `Budget`, `Cancel`, `Defect` | Never | `guard`, budgets, cancellation, bugs |
| `Shape`, `Gap` | Yes (default `on`) | Source data, codecs, holes, selectors |
| `Effect` | Only when listed in `on` | Host operations; use `retry` for transient ones |

- Use `guard` for caller and intermediate data (a `Contract` failure: authority
  mismatch aborts); use `project`/`capture` for source data (recoverable).
- Caller requests are validated and planned before the run, with the profile in
  `env.policy.profile`; hosts report plan gaps to the ledger themselves.
- `each(S, Policy.Drop)` drops bad rows; `recover(S, Policy.Substitute(V))` or
  `Policy.Fallback(T)` for one value. `Policy.Drop` is valid only in `each`/`parallel`.
- `target` defaults to the enclosing named stage; overrides must be program entities
  (a stage or slot namespace), never text.
- `choose(when(Shape, S), …, otherwise(S)?)` routes on match-only shapes; first
  match wins.
- `retry(S, {attempts, delay: backoff(…)})` wraps the smallest stage containing the
  effect, before any accumulator mutation or publish. Exhausted and `fatal` failures
  pass through enclosing handlers.
- `always(S, T)` publishes partial results; `T` does not run on `Cancel`. Cleanup in
  a stage is a native `finally` that never yields.
- Report gaps and losses with `yield report(Ledger.Gap, {target, reason, …})`.
  `collect(S)` returns `{value, ledger}`; `run` returns the output only.

## Binding cheat-sheet

| Notation | JavaScript | Python |
| --- | --- | --- |
| Options | option object `{ on, target }` | keyword arguments `on=…, target=…` |
| Stage namespace | `stages(S => ({ *X(env) {…}, Y: S1, Z: pipe(S.X, S.Y) }))` | `class N(stages):` with `def X(env):`, `Y = S1`, `Z = pipe(X, Y)` |
| Sibling inside a stage body | `yield* S.X(env, a)` | `yield from N.X(env, a)` |
| Stage | `function* X(env, a)` | `def X(env, a):` with `yield` |
| Delegate | `yield* S(env, a)` | `yield from S(env, a)` |
| Path | `` p`/x/${id}` `` | `p(t"/x/{id}")` |

- Python: a step without effects is `lift(fn)`, not a generator with a dummy `yield`.
- Python: class scope is invisible inside function bodies and lambdas, so a stage body
  delegates through the class (`People.X`), and the body declares stages only.
- JS: generator methods in the namespace never use `this`.

With `ClientRow` and `PersonRecord` from hexa-shapes:

```js
import { stages, pipe, select, each, project, collect, Policy } from "hexa/combinators.js";
import { p } from "hexa/runtime.js";

export const People = stages(S => ({
	/** Root → client rows. */
	*ClientsRetrieve(env) {
		const { data } = yield env.RequestSend({ method: "GET", path: p`/mortgages/${env.root.mortgage}/clients` });
		return data;
	},
	ClientsProject: each(project(ClientRow, PersonRecord), Policy.Drop),
	PeopleExtract: collect(pipe(S.ClientsRetrieve, select("*"), S.ClientsProject)),
}));
```

```python
from hexa.combinators import stages, pipe, select, each, project, collect, Policy
from hexa.runtime import p

class People(stages):
    def ClientsRetrieve(env):
        """Root → client rows."""
        mortgage = env.root["mortgage"]
        Response = yield env.RequestSend({"method": "GET", "path": p(t"/mortgages/{mortgage}/clients")})
        return Response["data"]

    ClientsProject = each(project(ClientRow, PersonRecord), Policy.Drop)
    PeopleExtract = collect(pipe(ClientsRetrieve, select("*"), ClientsProject))
```

## Testing

- Drive a hand-written stage with `inert(env)` and assert on the yielded effect or
  instruction descriptions; they compare equal by value.
- Replay a recorded journal (`env.replay`) for pipelines: effects match by
  `(strand, seq)`, so one journal is valid in both bindings.
- Keep fixtures as data: `responses.json` (transport fake), `expected.json`
  (`{value, ledger}`), `journal.json`.
- Assert on `kind`, `code` and `path` as members, never on message text.

## Review checklist

- [ ] Diagram and stage table present and consistent with the code.
- [ ] Diagram stages declared in one stage namespace; no stage name written twice.
- [ ] No kind, policy or code written as a string.
- [ ] Shapes pass the hexa-shapes checklist; no conversion inside stages.
- [ ] Stages yield effects for all I/O, use `p` for paths, and never await natively.
- [ ] Authority checks (`guard` against `env.root`) abort; source-data problems are
      handled by an explicit policy with a program-entity `target`.
- [ ] `retry` wraps only the effectful stage; no retries around publishes or
      accumulator updates.
- [ ] Journals stay host-private.
- [ ] Stage names put the verb last.

## Known open points

Do not invent answers for these; flag them:

- Logical traces across native delegation need explicit instrumentation.
- Encodings cover registered names only, never closures, handles or signals.
