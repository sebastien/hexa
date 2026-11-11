---
name: shape-pipelines
description: Write, review or refactor programs in the shape-directed pipeline style, in JavaScript or Python. Inputs and outputs are declared as shapes and templates, slots capture canonical values, codecs convert source formats, and stages are generators composed with combinators that yield inert effects. Use for extraction, normalisation and projection pipelines (bridges, CRM/API mappings), and for designing their shapes, codecs, policies, fixtures and tests.
---

# Shape-directed pipelines

The style is specified in `docs/specs/` of this repository. This skill gives the working rules; the specs decide disputes.

| Need | Read |
| --- | --- |
| Concepts, naming, data vs configuration, enums and codes | [spec-001](../../spec-001-shape_pipelines.md) |
| Shapes, slots, codecs, selectors, templates, encoding | [spec-004](../../spec-004-shape_pipelines_shapes.md) |
| Stages, combinators, effects, failures, policies, retry, ledger, replay, testing | [spec-005](../../spec-005-shape_pipelines_stages.md) |
| JavaScript syntax | [spec-002](../../spec-002-shape_pipelines_javascript.md) |
| Python 3.14+ syntax | [spec-003](../../spec-003-shape_pipelines_python.md) |
| A complete program with fixtures | [examples/mortgage-people](../../examples/README.md) |

**Python has an experimental implementation** in `hexa.shapes`,
`hexa.combinators` and `hexa.runtime`, tested with `python3 -m unittest discover`.
JavaScript remains syntax-only (`node --check`) until its binding exists. Report
which binding and tests were actually exercised; do not claim full draft-spec
conformance from syntax checks or the initial fixture suite.

## Workflow

1. **Output template first.** Write the record the caller receives, with slot
   references as leaves: `PersonRecord = {GivenNames: $Client.GivenNames, …}`.
2. **Slots.** Declare one slot namespace per subject with each slot's *canonical*
   shape: `Client = slots {Id: Int where Positive, …}`.
3. **Codecs.** For each source format that differs from the canonical shape, pick a
   standard codec (`InstantOffset`, `DecimalText`, `DatePattern("…")`), a table codec
   (text only) or a code codec. Conversion happens nowhere else. Give code codecs and
   transforms examples.
4. **Source shapes.** Describe each source occurrence with slots at the positions
   they occupy, `via Codec` where the format differs. Loose (`...`) only where the
   source legitimately carries extra keys.
5. **Codes.** Declare program codes for every contract or gap reason you raise:
   `Mortgage = codes {RootMismatch}`.
6. **Compose.** Build stages from combinators: `guard`, `select`, `each(project(…))`,
   `choose`, `recover`, `retry`, `collect`, `always`. Hand-write a generator stage
   only to yield effects or fan in earlier outputs.
7. **Declare the pipeline.** Diagram plus a `Stage | Input → output | Policy` table,
   at the top of the module or in its README.
8. **Fixtures and tests.** One synthetic example per union branch, codec row, gap
   reason and policy, plus a strictness failure; a recorded journal for the pipeline.

Prefer, in order: a template or shape → a registered codec, derivation or transform →
a combinator → a hand-written stage. Never widen the shape language to avoid a codec.

## Rules to keep in mind

### Configuration vs data

- Program code references members and codes as symbols: `Failure.Gap`, `Policy.Drop`,
  `Mortgage.RootMismatch`. **Never** write a kind, policy or code as a string literal,
  and never compare a data string against one.
- Strings in program code are only declarations (`slots("Client", …)`,
  `codes("Mortgage", …)`), small languages (selectors, patterns) and
  effect arguments.
- Data (outputs, ledger, journal, fixtures, encodings) holds names: `"kind": "Gap"`,
  `"reason": "Issue.Missing"`, `"target": "MortgageClientsProject"`. Only the runtime
  and `encode` convert.

### Naming

- PascalCase for everything a program names: stages, shapes, slots, codecs, codes,
  operations. camelCase for every key the spec defines (`maxGaps`, `env.root`,
  ledger and failure fields). External keys keep their source spelling.
- Stages are `Namespace? Subject Object Verb`, verb last, from the spec-005 verb table
  (`Validate`, `Retrieve`, `Select`, `Project`, `Normalize`, `Summarize`, `Publish`, …):
  `MortgageClientsSelect`, never `SelectMortgageClients`.
- Shapes are `Subject Role` (`ClientRow`, `PersonRecord`); codecs `Canonical
  SourceFormat` (`CountryName`, `InstantOffset`); operations `Subject Verb`
  (`RequestSend`, `DelayWait`).
- Pure helpers are not stages: no `env`, no `yield`, language-native naming
  (camelCase in JS and Python).

### Shapes and slots

- Shapes never convert: `"12"` does not match `Num`. Integral numbers are `Int` in
  both bindings; amounts are `Decimal` via `DecimalText`, never `Float`.
- Structs are strict unless `loose`. `key?` admits absence; `S | Null` admits null;
  neither implies the other.
- Tuples only via `Tuple(...)`; a list shape has exactly one item shape.
- A repeated slot is a join on canonical values; a mismatch is `Issue.CaptureConflict`.
  Join plural selections on a shared slot, never by position.
- In templates, `$S?` (`optional`, Python `~`) marks an expected absence. A plain
  reference that is unbound is a hole and `project` reports it as a gap. A slot captured
  only in some union branches must be referenced as optional.
- A failed derivation or format is also a hole: the key is dropped, the rest of the
  value kept. Use `{holes: Policy.Abort}` when a record is wrong without every field.
- `tolerant(S)` only for loosely typed sources of independent fields: a value that
  does not match or parse becomes a hole for the template keys that use it. Never
  wrap a whole record in it.
- Collect a field from every list item with `gather($N.S)` into a collection slot;
  `[$N.S]` is the same for a list of scalars.
- Nested records read parent fields through a capture block in the selector
  (`items/*/{id?:$Order.Id}/lines/*`) and `project(…, {outer: [Order.Id]})`, never
  by passing the parent around.
- `$Location` binds the occurrence's JSON Pointer; issue and ledger `path`s are key
  lists relative to it. `$Origin` binds the effect it came from, when known; pass
  effect results through unchanged to keep it.

### Codecs

- Table codecs map canonical → source or `[source, ...alternatives]` (first formats):
  `codec("CountryName", {source: Text, canonical: CountryCode, map, gaps?})`.
- Unlisted source is `<Codec>.Unmapped`; unlisted canonical is
  `<Codec>.Unformattable`. Never map to `Other`, `Unknown` or a default; declare a gap
  reason instead (`gaps: {Unknown: "NotStated"}`).
- Code codecs: pure `parse`/`format` (plus literal parameters, for families such as
  `DatePattern`); raise `Gap(Codec.Reason)`; return `lossy(value, Codec.Reason)` for
  lossy parses; declare every reason in `gaps` or `losses`. `format` may return its
  `original` argument to keep the source spelling.

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

### Failures and policies

| Kind | Recoverable | Typical source |
| --- | --- | --- |
| `Contract`, `Budget`, `Cancel`, `Defect` | Never | `guard`, budgets, cancellation, bugs |
| `Shape`, `Gap` | Yes (default `on`) | Source data, codecs, holes, selectors |
| `Effect` | Only when listed in `on` | Host operations; use `retry` for transient ones |

- Use `guard` for caller and intermediate data (a `Contract` failure: authority
  mismatch aborts); use `project`/`capture` for source data (recoverable).
- `each(S, Policy.Drop)` drops bad rows; `recover(S, Policy.Substitute(V))` or
  `Policy.Fallback(T)` for one value. `Policy.Drop` is valid only in `each`/`parallel`.
- `target` defaults to the enclosing named stage; overrides must be program entities
  (a stage or slot namespace), never text.
- `choose(when(Shape, S), …, otherwise(S)?)` routes on match-only shapes (no slots,
  no `via`); first match wins.
- `retry(S, {attempts, delay: backoff(…)})` wraps the smallest stage containing the
  effect, before any accumulator mutation or publish. Exhausted and `fatal` failures
  pass through enclosing handlers.
- `always(S, T)` publishes partial results; `T` does not run on `Cancel`. Cleanup in
  a stage is a native `finally` that never yields.
- Report gaps and losses with `yield report(Ledger.Gap, {target, reason, …})`.
  `collect(S)` returns `{value, ledger}`; `run` returns the output only.

## Language binding cheat-sheet

| Notation | JavaScript | Python |
| --- | --- | --- |
| `{k: S, k?: S}` | `{ k: S, "k?": S }` | `{"k": S, "k?": S}` |
| `{k: S, ...}` | `loose({ k: S })` | `loose({"k": S})` |
| `A \| B` | `either(A, B)` | `either(A, B)` (never `\|`) |
| `S where C` | `refine(S, C)` | `S & C` |
| `$N.S via Codec` | `via(N.S, Codec)` | `N.S @ Codec` |
| `Name($A, "x")` (template) | `derive(Name, A, "x")` | `derive(Name, A, "x")` |
| `$N.S?` (template) | `optional(N.S)` | `~N.S` |
| Options | option object `{ on, target }` | keyword arguments `on=…, target=…` |
| Stage namespace | `stages(S => ({ *X(env) {…}, Y: S1, Z: pipe(S.X, S.Y) }))` | `class N(stages):` with `def X(env):`, `Y = S1`, `Z = pipe(X, Y)` |
| Sibling inside a stage body | `yield* S.X(env, a)` | `yield from N.X(env, a)` |
| Stage | `function* X(env, a)` | `def X(env, a):` with `yield` |
| Delegate | `yield* S(env, a)` | `yield from S(env, a)` |
| Path | `` p`/x/${id}` `` | `p(t"/x/{id}")` |
| Null | `null` | `None` |

- Import canonical names unchanged. Python: do not import `typing.Any`/`List` next to
  the library's `Any`/`List`; allow PascalCase in linters (ruff `N802`, `N803`, `N806`).
- Python: a step without effects is `lift(fn)`, not a generator with a dummy `yield`.
- Python: class scope is invisible inside function bodies and lambdas, so a stage body
  delegates through the class (`People.X`), and the body declares stages only.
- JS: generator methods in the namespace never use `this`.

```js
const Client = slots("Client", { Id: refine(Int, Positive), GivenNames: Text, Country: CountryCode });
const ClientRow = loose({ id: Client.Id, firstName: Client.GivenNames,
	"country?": via(Client.Country, CountryName) });
const PersonRecord = { GivenNames: Client.GivenNames, Country: optional(Client.Country), Source: Location };

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
Client = slots("Client", Id=Int & Positive, GivenNames=Text, Country=CountryCode)
ClientRow = loose({"id": Client.Id, "firstName": Client.GivenNames,
    "country?": Client.Country @ CountryName})
PersonRecord = {"GivenNames": Client.GivenNames, "Country": ~Client.Country, "Source": Location}

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
  instruction descriptions; they compare equal as data.
- Replay a recorded journal (`env.replay`) for pipelines: effects match by
  `(strand, seq)`, so one journal is valid in both bindings.
- Keep fixtures as data: `responses.json` (transport fake), `expected.json`
  (`{value, ledger}`), `journal.json`, and canonical encodings of shapes.
- Assert on `kind`, `code` and `path` as members, never on message text.

## Review checklist

- [ ] Diagram and stage table present and consistent with the code.
- [ ] Diagram stages declared in one stage namespace; no stage name written twice.
- [ ] No kind, policy or code written as a string; no data string compared to a literal.
- [ ] Every conversion goes through a codec; no `Date.parse`, `Number(…)`,
      `fromisoformat` or `Decimal(…)` in stages.
- [ ] Struct strictness is deliberate; `key?` and `| Null` used for what they mean.
- [ ] Optional template references only where absence is expected; other holes report.
- [ ] `tolerant` only around single fields, and `holes` policy chosen deliberately.
- [ ] Table codecs have no default/`Other` mapping; reasons are declared.
- [ ] Stages yield effects for all I/O, use `p` for paths, and never await natively.
- [ ] Authority checks (`guard` against `env.root`) abort; source-data problems are
      handled by an explicit policy with a program-entity `target`.
- [ ] `retry` wraps only the effectful stage; no retries around publishes or
      accumulator updates.
- [ ] Public failure and ledger fields hold locations and codes, never source values;
      journals stay host-private.
- [ ] Names follow the patterns: verb last for stages, `Subject Role` for shapes.

## Known open points

Do not invent answers for these; flag them:

- Profile format and compiling selections/relations requests (spec-004, deferred).
- Index access (`a[0]`) in selectors or shapes (spec-004, open).
- Logical traces across native delegation need explicit instrumentation.
- Encodings cover registered names only, never closures, handles or signals.
- `SlotRedeclared` needs stable shape equality, not object identity.
