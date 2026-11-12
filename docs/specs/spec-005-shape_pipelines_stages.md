# Shape-directed pipelines: stages and runtime

The program side of [spec-001](spec-001-shape_pipelines.md): stages and their
contracts, combinators that compose them, effects and structured concurrency, failures
and the policies that recover from them, tracing and the journal, the environment,
accumulators and testing. Shapes, slots and templates are in
[spec-004](spec-004-shape_pipelines_shapes.md).

## Stages

```text
/** Prepared request + capture → {Payload, Coverage}. No I/O. */
CaptureProject(env, Prepared, Capture) → ProjectionResult
```

- `env` is always first; every other input is an explicit parameter. Stages read
  nothing from module state except shapes, slots, templates and other stages.
- A stage mutates only an accumulator it was handed. It performs I/O, reads clocks,
  waits or draws randomness only by yielding effects.
- A stage does one projection step. If its body needs headings, split it.
- Pure helpers are not stages: they take no `env`, never yield, and use the
  language's ordinary naming.

### Stage namespaces

```text
People = stages {
  MortgageRetrieve(env) → Data: ...
  MortgageValidate = guard(RootBound(env.root), Mortgage.RootMismatch)
  PeopleExtract    = collect(pipe(MortgageRetrieve, MortgageValidate, ...))
}
```

A pipeline's stages are declared together in a stage namespace, and each stage is
named by the key it is declared under, so a name is written once.

- Every stage in the pipeline diagram is declared in a namespace: hand-written stages
  by definition, combinator results by assignment. A hand-written stage's key is its
  function name.
- Declarations reference siblings declared before them, so a namespace reads bottom
  up, ending with the entry point. A reference to a name the namespace does not
  declare is an error when the namespace is built, as for slots.
- Names are fixed when the namespace is built. Declaring a named stage again under a
  different name, in any namespace, is the `Runtime.StageRenamed` error, so a trace
  never shows a stage under two names. Reference it by its existing name instead.
- Stage names are registered process-wide: declaring two different stages under one
  name, in any namespace, is the `Runtime.NameRedeclared` error.
- The namespace groups; it does not qualify. Stage names stay unique by their own
  namespace prefix (`MortgageClientsSelect`), and traces and ledger targets use them
  unqualified.
- The namespace is frozen and behaves like slot namespaces: an unknown member is
  `Runtime.MemberUnknown`. Hosts run its members: `run(env, People.PeopleExtract)`.

### Contracts

`stage(Name?, Contract?, S)` declares a stage's contract
`{input: Tuple(...), output: Shape}`, and names stages built programmatically
(e.g. one per entry of a host table), where no declaration supplies a name. Inside a
namespace it takes no name: `MortgageValidate = stage(Contract, guard(...))`.
Boundary stages declare contracts; internal hand-written stages may use a one-line
`inputs → output` doc comment instead.

- The wrapper checks on every call, including delegated ones. `env.policy.contracts`
  selects `Checks.Boundary` (default), which checks every declared contract, or
  `Checks.Off`, which keeps only the entry stage's input check: boundary input checks
  are never off.
- An input mismatch is a `Contract` failure; an output mismatch is a `Defect`.
- Combinators derive contracts where they can: `project(S, T)` has input `S`,
  `guard(S)` output `S`, and `pipe(A, …, Z)` is `A.input → Z.output`. Tooling can
  generate stage tables and diagrams from them.

| Verb | Meaning |
| --- | --- |
| `Validate` | Check against a shape; return a checked or normalised copy |
| `Parse` | Text → value, then `Validate` |
| `Prepare` | Validate a request and resolve it into a plan-bearing value |
| `Compile` | Data shape + vocabulary → plan |
| `Bind` | Substitute captures or known keys into a request or template |
| `Retrieve` | Fetch data through an effect |
| `Select` | Enumerate occurrences |
| `Discover` | Establish scope or membership facts in an accumulator |
| `Admit` | Partition candidates into admitted and rejected-with-reason |
| `Project` | Turn admitted occurrences into proposed records or observations |
| `Normalize` | Convert values to canonical form through codecs |
| `Compose` | Build one value from several captured values |
| `Resolve` | Replace internal references with output locations |
| `Materialize` | Build the output structure from an accumulator |
| `Summarize` | Build public coverage from private results and the ledger |
| `Publish` | Hand a detached copy to the host through an effect |
| `Create` | Construct a fresh value, e.g. `EnvironmentCreate` |
| `Describe` | Describe host vocabulary for callers |

## Combinators

| Combinator | Stage | Meaning |
| --- | --- | --- |
| `stages {Name: S, ...}` | — | A frozen namespace naming each stage by its key (above) |
| `stage(Name?, Contract?, S)` | as `S` | Contract, and a name for programmatic stages (above) |
| `pipe(S1, S2, ...)` | `a → z` | Each output is the next input |
| `lift(fn)` | `a → fn(a)` | A pure function as a stage |
| `project(Shape, Template, Options?)` | `Value \| Occurrence → Value` | Capture, join enclosing captures, expand, report holes. Options: `outer` (slots from enclosing capture blocks), `holes` (`Policy.Drop` or `Policy.Abort`) |
| `guard(Shape \| (env → Shape), Code)` | `a → a` | Precondition; mismatch is a `Failure.Contract` with `Code`. A function builds a parameterised shape when the stage runs |
| `select(Selector)` | `Value → [Occurrence]` | Enumerate; missing paths are gaps |
| `each(S, Policy?, Options?)` | `[a] → [b]` | Per item, in order; the policy applies per item and `Policy.Drop` leaves items out |
| `parallel(S, Policy?, Options?)` | `[a] → [b]` | As `each`, with concurrent items (see Effects) |
| `choose(when(Shape, S), ..., otherwise(S)?)` | `a → b` | The first branch whose shape matches runs `S`; `otherwise` runs when none does. No match is a `Failure.Shape` |
| `admit([Shape \| Predicate, Reason], ...)` | `[a] → {Admitted, Rejected}` | Every rule must pass; a rejection records the first failing reason |
| `fold(Create, S)` | `[a] → Acc` | `Create(env)` makes a run-local accumulator; `S(env, Acc, Item) → Acc` |
| `tap(S)` | `a → a` | Run `S` on a detached copy; return the input |
| `bound(Key, Measure)` | `a → a` | `Budget` failure if `Measure(a)` exceeds `env.policy[Key]` |
| `recover(S, Policy, Options?)` | `a → b` | Apply a failure policy to one value |
| `retry(S, Options?)` | `a → b` | Rerun `S` on transient effect failures |
| `always(S, T)` | `a → b` | Run `T` with `{value \| failure, ledger}` whether `S` succeeds or fails, except on `Cancel`; re-raise a failure afterwards |
| `collect(S)` | `a → {value, ledger}` | Run `S`; return the ledger entries it reported alongside its value |
| `validate(Format)` | `Value → Request` | Admit caller data as a request of `Format` under `env.policy.profile` (spec-004) |
| `plan(Format)` | `Request → Plan` | Compile a validated request into a plan, without effects (spec-004) |

- Combinators are pure constructors: building a stage performs no work and holds no
  run state.
- `when` only matches: its shape routes and never captures or converts, so slots
  and `via` are not allowed in it. The routing is visible at the call site; branch
  stages need no contract. `choose` derives input `either(Shapes...)`, or `Any` with
  `otherwise`.
- Fan-in stays a hand-written stage passing earlier outputs explicitly; `pipe` is for
  unary chains.
- Combinator results that appear in a diagram are declared in the pipeline's stage
  namespace. Inline, unnamed combinators nest at most one level inside a named one.
- No combinator hides an effect except `retry` delays (`DelayWait`).
- `always(S, T)`: when `S` and `T` both fail, `S`'s failure is raised with `T`'s
  attached privately as `cleanup`. `T` does not run on `Cancel` (see Cancellation).

### Pipeline declaration

```text
ExtractionRun:  always(RequestPrepare → PlanRetrieve → CaptureProject, CapturePublish)
CaptureProject: CaptureValidate → CaptureDiscover → ObservationsProject
                → RelationsProject → MetadataCreate → PayloadMaterialize
                → CoverageSummarize
```

Every pipeline module opens with, or its README contains, the diagram and a
`Stage | Input → output | Policy` table. The diagram is the primary documentation; a
reader should be able to predict the composition from it, and tooling may generate
both from declared contracts.

## Effects and concurrency

```text
Response ← yield env.RequestSend({method: GET, path: p"/mortgages/{root.mortgage}"})
{Mortgage, Deals} ← yield join({Mortgage: env.RequestSend(MortgageRequest), Deals: env.RequestSend(DealsRequest)})
```

- The runtime hands each stage a stand-in `env`. Data properties (`root`, `policy`,
  `vocabulary`) are values; calling an operation returns an inert effect description
  with `operation` and `arguments`. Nothing runs until it is yielded. Stage code reads as a
  call: `yield env.RequestSend(...)`.
- Effect arguments and results are JSON data, so effects can be journaled, replayed,
  compared in tests and serialised.
- `p` builds a request path from a template: each interpolated value becomes exactly
  one percent-encoded segment, so `p"/mortgages/{Id}"` cannot address another
  resource. A value other than `Text` or `Int`, or one that is empty, `.` or `..`, is
  the `Failure.Contract` `Runtime.PathSegmentInvalid`. The result is a plain string.
- The runtime performs a yielded effect against the host operation, passing a
  cancellation signal scoped to the effect's strand. It also enforces budgets,
  publishes progress, applies retry defaults and writes the journal.
- Concurrency is structured and explicit:

| Instruction | Result |
| --- | --- |
| `join(List \| Struct)` | All items concurrently; results in the same list or struct shape. The first unrecovered failure cancels the others and is raised at the yield |
| `race(List \| Struct)` | The first item to complete; the others are cancelled. A struct yields a struct holding only the winner's key; a list yields the winner's value |
| `fork(S, ...Inputs)` | Starts `S` as a concurrent strand and returns a handle; `join` and `race` accept handles |

- `race` keeps its input's structure where JSON can: `{Primary, Mirror}` resolves to
  `{Mirror: value}`, destructured like a `join`. Lists cannot hold holes and `null` is
  a value, so list items are interchangeable alternatives; race a struct when the
  winner matters.
- `race` selects the first **completion**, not the first success. If the selected
  participant fails or is cancelled, that interruption propagates; another
  participant's success is never a fallback. Selection is of a direct participant,
  which may itself be a nested instruction or fork handle.
- Items of `join` and `race` are effects, handles or nested instructions.
- Effect descriptions and instructions are typed records, not plain structs. They
  compare equal by value, so a yielded `join` compares equal to an expected one in
  tests, and a struct a stage yields is always a plain value, whatever its keys. An
  effect description encodes as `{operation, arguments}`.
- A stage cannot finish with unjoined strands; the runtime cancels them and raises
  the `Failure.Defect` `Runtime.StrandUnjoined`.
- Forked strands never receive accumulators. A stage that joins effects updates its
  accumulators after the yield, sequentially.
- Each strand buffers its ledger reports; they merge in declared order when joined,
  so coverage is deterministic.
- Hand-written stages obtain concurrency only through these instructions; they never
  await, `Promise.all` or `gather` themselves.
- `parallel` forks one strand per item, at most `env.policy.maxConcurrency`
  at a time, and joins them in input order. It may ship after `each`.

The runtime is a trampoline. It resumes the stage synchronously for plain values and
instructions, and awaits only when performing effects. A stage yielding no effects
runs to completion without suspension.

| Yield | Runtime action |
| --- | --- |
| Effect description | Perform it; resume with the result or raise the failure at the yield |
| Instruction (`join`, `race`, `fork`, `report`, `ledger`) | Interpret it |
| Plain value | Resume with it unchanged |
| Native promise or coroutine | `Defect`: effects must be descriptions |

## Failures and recovery

| `Failure` | Raised by | Recoverable |
| --- | --- | --- |
| `Contract` | `guard`, `check`, authority mismatches, invalid caller data | Never |
| `Budget` | `bound` and policy limits, including ledger size, attempts and concurrency | Never |
| `Cancel` | The run's cancellation signal | Never; never retried |
| `Defect` | Programming errors, failed output contracts, misbehaving codecs | Never |
| `Shape` | `capture` and `project` on source data | Yes |
| `Gap` | Selectors, codecs, derivations, holes and admission rules | Yes |
| `Effect` | A failed host operation | Yes; retryable when transient |

- A shape failure of source data is recoverable; a shape failure of caller or
  intermediate data is raised by `guard` or `check` and is a `Contract` failure.
- A `Shape` failure takes its code and path from its first issue, e.g. `Issue.Missing` at
  `[basicDetails, firstName]`; all issues stay available to the failure's handler.
- `env.FailureClassify(Error) → {code, transient}` classifies effect failures. The
  default classifies nothing as transient.
- A failure carries `{kind, code, path?, location?, trace, fatal?}` publicly, and its
  ledger, journal and `cleanup` failure privately (see Tracing). Public fields never
  hold source values.

### Cancellation

- On `Cancel`, the runtime closes each affected stage's generator instead of raising
  into it: only native `finally` blocks run. `recover`, `retry` and `always` never see
  a `Cancel`, and `T` in `always(S, T)` does not run.
- A generator that yields while closing is the `Failure.Defect` `Runtime.CloseYielded`.
  Cleanup that needs effects is the host's responsibility, outside the cancelled run.
- The rule is the same in every language binding, so cleanup behaves identically
  whether the language can resume a closing generator or not.

### Policies

```text
recover(S, Policy, {on?, target?, reason?})
each(S, Policy?, {on?, target?, reason?})

Policy  = Policy.Abort | Policy.Drop | Policy.Substitute(Value) | Policy.Fallback(Stage)
on      = [Failure member]           default [Failure.Shape, Failure.Gap]
target  = Stage | Namespace          default the enclosing named stage
reason  = Code | (Failure → Code)    default the failure's code
```

A policy applies through `recover`, or per item through `each` and `parallel`, which
default to `Policy.Abort`. On a failure from `S`:

1. If its kind is not in `on`, is never recoverable, or the failure is marked `fatal`
   or exhausted, it propagates unchanged.
2. A gap `{target, reason, source?, path?}` is reported; under `Policy.Abort` it is
   marked `fatal`.
3. The policy applies:

| Policy | Result |
| --- | --- |
| `Policy.Abort` | Re-raise the failure, keeping its kind, with `reason` as its code |
| `Policy.Drop` | Leave the item out; valid only in `each` and `parallel` |
| `Policy.Substitute(Value)` | Return `Value`, which must match the stage's output shape |
| `Policy.Fallback(Stage)` | Run `Stage` with the same input; its failures propagate |

- `target` names what was not produced. It is a program entity, never free text, so a
  ledger entry points at a stage in the pipeline diagram or at a slot namespace. The
  default is resolved when the failure is handled, from the logical stack (see
  Tracing): combinators are built before `stage` names them.
- A `fatal` failure has already been reported and refused recovery; enclosing
  handlers pass it through, as they pass exhausted retries.
- `Failure.Effect` is handled only when listed in `on`: transport failures are
  retried with `retry`, not treated as bad data.

Policies apply at three granularities, from finest to coarsest:

| Granularity | Unit lost | Declared by |
| --- | --- | --- |
| Field | One template key (a hole) | `project(..., {holes: Policy.Drop})`, the default |
| Item | One value of a list | `each` or `parallel` with `Policy.Drop` |
| Run | Everything | `Policy.Abort`, or no policy |

- Choose the finest unit whose loss leaves a correct output. Fields of independent
  facts (attributes of a record) are dropped one by one; fields that only make sense
  together belong to one derivation, so they succeed or fail as one.
- A field hole is reported, never silent, with the same ledger entry an item drop
  would make, at the field's path.
- `{holes: Policy.Abort}` turns field holes into item failures, so the enclosing
  `each` policy decides.

### Retry

`retry(S, {attempts?, delay?})` reruns `S` from the start, with the same input, when
it fails with a transient `Failure.Effect`, waiting `delay(attempt)` through
`DelayWait`.

- `env.policy.retry = {default, attempts, delay}` holds host defaults: `attempts` and
  `delay` fill missing options, and `default` decides whether effects outside any
  `retry` are retried.
- Retries never multiply: a failure that exhausted its attempts is marked exhausted,
  and enclosing `retry` stages and host defaults pass it through.
- `attempts` counts the first attempt and is capped by `env.policy.maxAttempts`.
  Retries count against call budgets and appear in the journal. Replay reproduces the
  recorded attempts, failures included, and answers `DelayWait` from the journal
  without waiting.
- A retried stage must not mutate an accumulator or publish before its last effect;
  wrap the smallest stage containing the effect.
- `delay(attempt) → Milliseconds` is pure, e.g. `backoff(250, {factor: 2, max: 5000})`.
  Jitter belongs to the host's `DelayWait`. Waiting honours cancellation.

### Ledger and coverage

- Stages report with `yield report(Ledger.Gap | Ledger.Loss, {target, reason,
  source?, path?})`. Ledger entries are data: `{kind, target, reason, source?, path?,
  fatal?}`. `source` is the occurrence's location and `path` the failing position
  within it. The runtime keeps one ledger per run, bounded by `env.policy.maxGaps`
  (exceeding it is a `Budget` failure). Entries equal on `kind`, `target`, `reason`,
  `source` and `path` are reported once, keeping the first position; `fatal` merges
  as or.
- `run(env, Stage, ...inputs)` returns the stage's output. A host that needs the
  ledger of a successful run wraps the entry stage in `collect`; a failed run
  attaches it to the failure.
- `collect(S)` returns the entries `S` reported; they also stay in the run ledger.
  `Summarize` stages read the run ledger with `yield ledger()`.
- The ledger is the only runtime-owned accumulator; it never outlives the run.
- Coverage distinguishes the `Coverage` members `Supplied`, `SourceNull`,
  `NotSupplied`, `Unmapped` and `Conflicting`. Explicit `null`, `false`, `0` and
  empty values are values, not gaps.
- Conflicting values for one target produce an issue and no value; no projection
  picks a winner silently.

## Tracing and journal

- The runtime tracks the logical stack of named stages and combinators. A failure's
  `trace` lists it from the outermost stage, e.g.
  `[PeopleExtract, MortgageClientsProject, each, project]`, plus the journal index of
  the last effect. Traces are value-free and map onto the pipeline diagram.
- When `env.record` is set, the runtime journals each performed effect:
  `{index, strand, seq, operation, arguments, result | failure | cancelled, attempt}`.
  It also journals explicit `race` decisions (below). `index` is the order of
  journal emission: effect completion or race selection/cancellation. It is
  informational only, never a replay matching or winner-selection key. A journal
  is a JSON array and valid `env.replay` input; replay never calls host operations.
- A failed run raises its failure with the ledger and, when recording, the journal
  attached privately. Journals contain source data: they are host-private evidence,
  never disclosed to callers by default.
- `always(S, T)` is the declarative way to publish partial results, such as a
  capture, when `S` fails.

### Provenance

Outputs can name the effect their data came from, without carrying source data.

- The runtime marks every list and struct within a value returned at a yield (an
  effect result, or a `join` or `race` result) with its origin: the effect's
  `(strand, seq)` and the JSON Pointer of that container within the result. Marks are
  kept by identity for the duration of the run and never change the value.
- When a selector walks a value, the nearest marked container on the path gives the
  occurrence's `origin`, extending its pointer by the remaining segments. In the
  spec-001 example, `MortgageRetrieve` returns `{application: Application.data,
  clients: Clients.data}`; the client list is the response's own list, so
  `clients/0` has origin `{strand: "/Clients", seq: 0, pointer: "/data/0"}`.
- Copies lose marks: a stage that rebuilds a container, and any scalar, has no
  origin. Stages that must preserve provenance pass containers through unchanged.
- Origins are recorded whether or not the run records a journal; they name journal
  positions so that, when it does, an origin resolves to the response that held the
  data. Replay reproduces the same origins.

### Strands and replay matching

Concurrent strands complete in any order, so replay matches effects by their position
in the program, not by completion order.

- A strand id is a JSON Pointer. The run's root strand is `""`. A `join` or `race`
  item runs in a child strand whose id appends its key or index (`/Application`,
  `/0`); the n-th `fork` of a strand appends `/@n`. Ids are therefore stable across
  runs. Struct keys of `join` and `race` must not begin with `@`. A handle joined or
  raced keeps its fork id; `parallel` forks one strand per item.
- `seq` counts performed effects from 0, including every retry attempt and
  `DelayWait`. `attempt` is metadata, not part of the replay lookup key.
  Delegation (`yield*`, `yield from`) and combinators stay in the same strand.
  Counters are run-local and continuous per strand path: successive `join` or
  `race` groups reusing a key continue the child's sequence. Overlapping reuse of
  an active child path is the defect `Runtime.StrandConflict`.
- Replay answers each yielded effect with the entry for its `(strand, seq)`, after
  checking that `operation` and `arguments` are equal as JSON. A missing entry or a
  difference is the `Failure.Defect` `Runtime.ReplayMismatch`.
- A recorded `failure` is raised again with its kind and code; a `cancelled` entry
  marks a strand that lost a `race` or was cancelled by a sibling, and replay cancels
  that strand at the same point. Race decisions, not effect completion order or
  the presence of cancelled effects, determine the selected participant.
- A journal fixture is valid for every language binding: strand ids, `seq` and
  arguments never depend on scheduling.

### Race decisions

Effects record what happened; a race decision records which direct participant
the runtime selected. Both alternatives may finish successfully before cancellation
reaches the loser, so effect results alone do not identify the selected one.

```json
{"instruction": "race", "strand": "", "ordinal": 0,
 "keys": ["Primary", "Mirror"], "winner": "Mirror"}
```

- Each valid `race` invocation consumes one run-local, per-strand race `ordinal`,
  starting at 0. Repeated invocations and retries advance it; a reused strand path
  continues its counter. Race decisions never consume or reset effect `seq`.
- Decisions match by `(strand, ordinal)` in a separate identity domain from effect
  positions `(strand, seq)`. A decision and an effect may both have position 0.
- `keys` lists participant keys in declaration order. Struct keys are strings;
  list positions are integers `0` through `n - 1`, never booleans or numeric
  strings. A fork handle's key is its position in this race, not its fork id.
- `winner` names the selected participant even when it fails or is cancelled. The
  runtime records the decision immediately on selection, before cancelling and
  draining the others. Completed losing effects retain their actual results.
- If the whole race is cancelled before selection, the decision has
  `"cancelled": true` instead of `winner`. Exactly one of these fields is present.
- Replay validates the participant keys, then drives every participant through its
  recorded work to its recorded completion or cancellation. It drains nested joins
  too: immediately cancelling nonwinners could discard effects and ledger reports
  that happened before the original selection. Ledger entries still merge in
  declaration order.
- Replay returns the recorded selected participant's value, or propagates its
  failure/cancellation, regardless of replay scheduling. A race-level cancellation
  propagates even if all participants' recorded effects succeeded. A replay mismatch
  in any participant, including a nonwinner, remains a defect.
- Missing decisions or changed keys are `Failure.Defect` `Runtime.ReplayMismatch`;
  successful replay also rejects unused journal entries. Malformed journals, including duplicate
  decision identities or invalid winners, fail boundary validation. Older journals
  without races remain valid; a legacy race journal without a decision is rejected,
  never inferred from effect `index` or cancellation.

## Environment

The environment carries `root`, `scope`, `vocabulary`, `policy`, operations, and
per-run options `signal`, `replay` and `record`.

- The runtime accepts a host-provided mapping, validates and snapshots authority,
  vocabulary and policy, and exposes a frozen stand-in. A host factory
  (`EnvironmentCreate`) is optional, not a required library API. Caller input is
  never spread or merged into the environment.
- Per-run options may be overlaid; authority or policy changes require a fresh
  validated snapshot. The environment is read-only during a run.
- `env.policy` is host configuration: budgets and allow-lists, `profile` (the
  profile applied to caller input, spec-004), `allow` (the operations or capabilities
  a plan may need; absent permits all), `contracts` (a `Checks` member),
  `retry`, `maxAttempts`, `maxGaps` and `maxConcurrency`.
- Operations are named Subject + Verb and may return values or promises. Each has a
  default, either a no-op or a failure. Stages see them only through the stand-in.
- The vocabulary registers types, constraints, derivations, transforms and codecs by
  name, each pure and with a declared signature and, for codecs and derivations,
  examples (spec-004). It is what `CatalogDescribe` presents to callers.

## Accumulators

Discovery builds membership sets, projection deduplicates observations,
materialization assigns locations before resolving references. These use
accumulators, created by `fold` or by a hand-written owner.

- One stage creates an accumulator per run, passes it explicitly to sub-stages, and
  returns or discards it. Sub-stages receiving it return it.
- Accumulators never live in the environment, module scope or a combinator's closure,
  and never outlive the run. Forked strands never receive them.
- Declare the accumulator's shape in its owner's documentation, and split by purpose
  rather than growing one bag.

## Boundaries and testing

| Boundary | Required |
| --- | --- |
| Caller → pipeline | Data-only check (JSON, acyclic, bounded, no prototype keys), decoding under a profile, then `guard` |
| Effect → pipeline | Response shape, budget and authority checks: is this the data that was requested? |
| Pipeline → host | `tap` with a detached copy |
| Pipeline → caller | Whitelisted projection; ledger details, journal and evidence only on explicit host disclosure |

- Test each projection with synthetic data: one example per union branch, codec row
  and gap reason, plus a strictness failure.
- Test each policy and `retry`: retry exhaustion, every `Policy` member, and
  unrecoverable kinds passing through.
- Test stages by driving them by hand with `inert(env)`, the stand-in environment,
  and asserting on the effect descriptions they yield; test pipelines by replaying
  journals.
- Assert on kinds, codes and paths as members, never message text.
