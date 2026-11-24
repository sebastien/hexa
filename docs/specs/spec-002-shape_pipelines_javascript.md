# Shape-directed pipelines: JavaScript language binding

Binds [spec-001](spec-001-shape_pipelines.md),
[spec-004](spec-004-shape_pipelines_shapes.md) and
[spec-005](spec-005-shape_pipelines_stages.md) to JavaScript. It defines syntax,
native semantics and module layout only; concepts, rules and canonical names come
from those specs. Targets ES2022 modules in browsers, Bun and Node, with no
dependencies and no Node/Bun imports in library modules. The
[Python binding](spec-003-shape_pipelines_python.md) uses identical names.

## Modules

The modules form the package `hexa`, imported as `hexa/shapes.js`,
`hexa/combinators.js` and `hexa/runtime.js`. The package has no dependencies, so
libraries building on it may load it lazily with `import()` and stay usable
without it.

| Module | Exports |
| --- | --- |
| `shapes.js` | Types, shape constructors (including `tolerant` and `gather`), slots, `Location`, `Origin`, codecs, `transform`, `lossy` and `codes`, standard vocabulary, operations (including `evidence` and `parsed`), `encode`, `decode`, `profile`, `request`, `describe`, `SelectionRequest`, `Failure`, `Gap`, `Issue`, `Profile` |
| `combinators.js` | Combinators (including `validate` and `plan`), `Policy`, `backoff` |
| `runtime.js` | `run`, `report`, `ledger`, `join`, `race`, `fork`, `inert`, `p`, `Ledger`, `Coverage`, `Checks`, `Runtime` |
| `http.js` | `http` (the `RequestSend` operation over `fetch`), `Request` |

`shapes.js` is a façade over `shape` (algebra and matching), `template`, `codec`,
`selector`, `encoding`, `profile`, `selection_request` (the concrete selection
format) and `vocabulary`. `combinators` and `runtime` build on `stage` and
`instructions`; shared declaration identity and reason helpers live in `core`.
Programs import only the three public modules; hosts also import `hexa/http.js`
for HTTP APIs (spec-005, HTTP requests). Integrations that decode
configuration compiled elsewhere may also import `hexa/core.js` (`REGISTRY`),
`hexa/shape.js` (`Slot`, `compileShape`, `validateShape`, captures) and
`hexa/template.js` (`Transform`), as the Python integrations import `hexa.core`
and `hexa.shape`. They use these to keep slots and transforms out of the
process-wide registry: a `decode` registry layered over `REGISTRY`, and
unregistered `Slot` and `Transform` values.

Import canonical names unchanged; never rename on import.

## Shapes

| Notation | JavaScript |
| --- | --- |
| Types | `Any`, `Text`, `Num`, `Int`, `Float`, `Bool`, `Null`, `Struct`, `List`, `DateTime`, `Instant`, `Decimal` |
| Literal | `"Applicant"`, `12`, `true`, `null` |
| `{k: S, k?: S}` | `{ k: S, "k?": S }` |
| `{k: S, ...}` | `loose({ k: S })` |
| `[S]`, `Tuple(A, B)` | `[S]`, `Tuple(A, B)` |
| `A \| B` | `either(A, B)` |
| `~"..."` | `pattern("...")` |
| `S where C` | `refine(S, C)`; custom: `refine(S, where(isEven, "Even"))` |
| `$N.S via Codec(Arg)` | `via(N.S, DatePattern("YYYYMMDD"))` |
| `[$N.S]`, collection capture | `[N.S]`, `[via(N.S, Codec)]` |
| `tolerant(S)` | `tolerant(S)` |
| `gather($N.S)` | `gather(N.S)`, `gather(via(N.S, Codec))` |
| `parsed($N.S)` in a template | `parsed(N.S)` |
| `Name(Param)` | `const RootBound = Root => loose({ ... })`, used as `env => RootBound(env.root)` |

- `Text` matches primitive strings; `Num` finite numbers; `Int` values passing
  `Number.isSafeInteger`; `Float` finite numbers failing `Number.isInteger`; `Bool`
  primitive booleans. Boxed primitives never match. A captured `-0` becomes `0`
  ([spec-004](spec-004-shape_pipelines_shapes.md) number normalisation).
- `DateTime`, `Instant` and `Decimal` are checked by the [spec-004](spec-004-shape_pipelines_shapes.md) grammar, never by
  `Date.parse` or `Number`.
- Structs match plain objects only: prototype `Object.prototype` or `null`, own string
  keys. `__proto__`, `prototype` and `constructor` are rejected as data keys.
- `"k?"` treats an own property holding `undefined` as absent. `undefined` is not a
  JSON value: it never matches `Null` and is not a shape.
- `List`, `[S]` and `Tuple(...)` match arrays without holes. An array literal of any
  other length than one is rejected as a shape.
- `where(Predicate, Name)` wraps a pure predicate as a named constraint. `Name` is a
  qualifier string (`"Even"`), not a code: constraints register under their own name,
  and a failed constraint reports `Issue.Constraint` with that name. Standard
  constraints (`DateOnly`, `Timed`, `Zoned`, `Positive`, `Nonempty`) are exported
  ready to use.

Compatibility aliases, accepted but never used in documentation: the `String`,
`Number`, `Boolean`, `Object` and `Array` constructors for `Text`, `Num`, `Bool`,
`Struct` and `List`; `new Set([A, B])` for `either(A, B)`.

## Slots and codecs

```js
const Client = slots("Client", {
	Id: refine(Int, Positive), GivenNames: Text, FamilyName: Text,
	BirthDate: either(refine(DateTime, DateOnly), Null), Country: CountryCode,
});

const CountryName = codec("CountryName", { source: Text, canonical: CountryCode,
	map: { AU: "Australia", NZ: ["New Zealand", "Aotearoa New Zealand"] }, gaps: { Unknown: "NotStated" } });

const CrmClient = loose({ id: Client.Id, "countryName?": via(Client.Country, CountryName) });
```

- `slots(Namespace, Shapes)` returns a frozen namespace. Each member is a frozen slot
  object identified by `Symbol.for("slot:Client.Id")`; captures are keyed by that
  symbol internally.
- Accessing an unknown member throws `Runtime.SlotUnknown`. `then`, `toJSON`, `constructor`
  and symbol keys return `undefined`, so a namespace is never mistaken for a promise
  or serialised by accident.
- Slot names are interned process-wide: redeclaring `Client.Id` with a different
  shape throws `Runtime.SlotRedeclared`.
- `slots("Exchange.Instrument", { Isin: Text })` declares a nested namespace: its
  members are `Exchange.Instrument.Isin`. A namespace name is the full dotted
  prefix; there is no parent object to declare first.
- `slot(QualifiedName, Shape)` creates a single slot; `Location` and `Origin` are
  exported.
- In templates, `optional(Slot)` marks an expected absence and `via(Slot, Codec)`
  formats the canonical value. `derive(Transform, ...Arguments)` applies a
  transform; arguments are slots, `optional` slots, literals or other `derive`
  values: `derive(PersonNameCompose, Client.GivenNames, Client.FamilyName)`.
- `transform(Name, fn, { inputs, output, gaps?, examples? })` declares a
  transform; `fn` takes the arguments positionally and throws
  `new Gap(Transform.Reason)` on failure.
- `evidence(captures, slot)` returns `{ raw, location, codec? }` or `null`.
- `codec(Name, { source, canonical, parse, format, gaps?, losses? })` declares a code
  codec; `gaps` and `losses` are arrays of reason names. `parse` and `format` are pure,
  throw `new Gap(Codec.Reason)` on failure, and `parse` returns
  `lossy(value, Codec.Loss)` for a lossy result. `format(value, original)` receives
  the captured raw value as `original` when there is one, and `undefined` otherwise.
- `codec(Name, { source, canonical, map, gaps? })` declares a table: `map` is an object from canonical value
  to a source string or a list of them, the first formatting; `gaps` maps source
  strings to reason names, declaring the codes `CountryName.NotStated` etc.
- `codec(Name, { params: { Pattern: Text }, ... })` declares a parameterised codec.
  It returns a codec family: calling it, `DatePattern("YYYYMMDD")`, checks the
  arguments and returns an interned instance. `parse` and `format` receive the
  arguments as leading parameters: `parse(pattern, value)`.
- `examples: [...]` on `codec` and `transform` takes the spec-004 data form:
  `{ input: "20260915", output: "2026-09-15" }`,
  `{ input: "2026-02-30", gap: "Invalid" }`; examples of a parameterised codec add
  `arguments: ["YYYYMMDD"]`.

## Enums and codes

```js
const Mortgage = codes("Mortgage", "RootMismatch");

guard(env => RootBound(env.root), Mortgage.RootMismatch);
each(project(ClientRow, PersonRecord), Policy.Drop);
each(project(ClientRow, PersonRecord), Policy.Drop, { on: [Failure.Shape, Failure.Gap, Failure.Effect], target: Client });
project(DividendRow, DividendRecord, { outer: [Instrument.Isin], holes: Policy.Abort });
```

- Enum members and codes are interned symbols, `Symbol.for("member:Failure.Gap")`,
  exposed on frozen namespaces. Their `description` includes the `member:` prefix;
  the runtime and `encode` strip that prefix to derive canonical data names.
- Namespaces behave like slot namespaces: an unknown member throws
  `Runtime.MemberUnknown`, and `then`, `toJSON` and `constructor` return `undefined`.
- `Failure` is both the error class and the kind enum: `Failure.Gap` is a static
  member. `Policy.Substitute(value)` and `Policy.Fallback(S)` return frozen policy
  values; the other `Policy` members are symbols.
- Symbols are not JSON. A stage that needs a member's data name strips the
  `member:` prefix from `member.description`; it never compares data strings
  against string literals.

## Stages and effects

```js
/** Root → mortgage response data. */
function* MortgageRetrieve(env) {
	const { data } = yield env.RequestSend({ method: "GET", path: p`/mortgages/${env.root.mortgage}` });
	return data;
}

/** Mortgage id → {Mortgage, Deals}, retrieved concurrently. */
function* MortgageDealsRetrieve(env, Id) {
	return yield join({
		Mortgage: env.RequestSend({ method: "GET", path: p`/mortgages/${Id}` }),
		Deals: env.RequestSend({ method: "GET", path: p`/mortgages/${Id}/deals` }),
	});
}
```

| Concept | JavaScript |
| --- | --- |
| Stage | `function* Name(env, input, ...)` |
| Stage namespace | `const People = stages(S => ({ *X(env, a) { … }, Y: S1, Z: pipe(S.X, S.Y) }))` |
| Contract | `Y: stage({ input, output }, S1)` inside a namespace |
| Programmatic stage | `stage(name, S)`, `stage(name, { input, output }, S)` with a computed `name` |
| Delegate | `const out = yield* Stage(env, input)` |
| Effect | `const result = yield env.Operation(...args)` |
| Path | `` p`/mortgages/${Id}/deals` `` |
| Concurrency | `yield join([...])`, `yield join({...})`, `yield race(...)`, `const h = yield fork(S, input)` |
| Ledger | `yield report(Ledger.Gap, { target, reason })`; `const entries = yield ledger()` |
| Policy | `each(S, Policy.Drop)`; `recover(S, Policy.Substitute([]), { on: [Failure.Gap] })` |
| Retry | `retry(S, { attempts: 3, delay: backoff(250) })` |

- Hand-written stages are `function*` declarations or generator methods; stages in a
  pipeline diagram are declared in a stage namespace (below).
- `p` is a template tag. Each interpolation is checked as a `Text` or `Int` segment
  and percent-encoded as RFC 3986 path segments (including `!'()*`), so `/` inside
  a value never splits it. Literal template text is unchanged.
- The runtime passes a frozen stand-in as `env`. Data properties (`root`, `policy`,
  `vocabulary`, `scope`) are frozen values; operation properties return effect
  descriptions. Calling a stand-in operation outside a yield does nothing.
- Pure helpers use camelCase and are not exported from pipeline modules unless tested
  independently.

### Stage namespaces

```js
export const People = stages(S => ({
	/** Root → mortgage response data. */
	*MortgageRetrieve(env) {
		const { data } = yield env.RequestSend({ method: "GET", path: p`/mortgages/${env.root.mortgage}` });
		return data;
	},
	MortgageClientsSelect: select("clients/*"),
	PeopleExtract: collect(pipe(S.MortgageRetrieve, S.MortgageClientsSelect)),
}));
```

JavaScript cannot name a call result from the `const` it is assigned to, and an
object literal cannot refer to its own keys, so `stages` takes a function from a
reference proxy `S` to the declarations.

- Each key names its value. A generator method (`*X(env) { … }`) is a hand-written
  stage, named by its method name; a module-level `function*` may be listed by
  shorthand (`{ MortgageRetrieve }`), and its function name must equal the key.
- `S.X` is a late reference, accepted wherever a combinator takes a stage. `stages`
  resolves every reference before returning; a reference to an undeclared key throws
  `Runtime.MemberUnknown` when the module loads. After that, `S` is the namespace, so
  hand-written stage bodies delegate with `yield* S.X(env, a)`.
- `stages` returns a frozen namespace with the slot namespace behaviour: unknown
  members throw `Runtime.MemberUnknown`, and `then`, `toJSON` and `constructor` return
  `undefined`. Destructuring it is therefore checked: `const { PeopleExtract } = People`.
- Methods never use `this`. The namespace is the module's export; hosts and tests use
  its members (`run(env, People.PeopleExtract)`, `People.MortgageRetrieve(env)`).

## Host environment

```js
const env = EnvironmentCreate({
	root: { mortgage: 42 }, scope: tenantScope, vocabulary: catalog,
	policy: { contracts: Checks.Boundary, maxGaps: 1000, maxConcurrency: 4,
		retry: { default: true, attempts: 3, delay: backoff(250) } },
	RequestSend: (request, { signal }) => transport(request, signal),
});
const { value, ledger } = await run(env, People.PeopleExtract);
```

- Operations receive the effect's arguments followed by a context
  `{ signal, attempt, strand }`, and return a value or a promise. They must honour
  `signal`.
- `EnvironmentCreate` in this example is a host-defined helper, not a library
  export. `run` and `inert` snapshot and freeze stage-facing authority; per-run
  options may be overlaid with `{ ...env, signal, record: true }`; `record` may
  also be a function receiving each journal entry (spec-005).

## Failures and cancellation

```js
export class Failure extends Error {
	static Contract = member("Failure.Contract");   // … Budget, Cancel, Defect, Shape, Gap, Effect
	constructor(kind, code, { path, location } = {}) {
		super(code.description.replace(/^member:/, ""));
		this.name = "Failure";
		Object.assign(this, { kind, code, ...(path ? { path } : {}), ...(location ? { location } : {}) });
	}
}
export class Gap extends Failure {
	constructor(code, details) { super(Failure.Gap, code, details); this.name = "Gap"; }
}
```

| `Failure` | JavaScript source |
| --- | --- |
| `Contract`, `Budget`, `Shape`, `Gap`, `Defect` | `Failure` with that kind (`Gap` for gaps) |
| `Effect` | A rejected host operation, classified by `FailureClassify` |
| `Cancel` | `AbortError` / `TimeoutError` `DOMException`, or any failure while `signal.aborted` |
| `Defect` | Any other thrown value, wrapped with its original as `cause` |

- The runtime adds `trace` and, on run failure, non-enumerable `ledger`, `journal`
  and `cleanup` properties, so `JSON.stringify` and default logging omit private
  evidence.
- A `Cancel` is never thrown into a generator. The runtime calls `it.return()`, which
  runs `finally` blocks only; if that resumes at a `yield`, the result is
  `Runtime.CloseYielded`.
- `kind` and `code` are symbols; `Failure.message` is the code's qualified name.

## Runtime

```js
/** Drive one strand: effects and instructions go to `strand`, plain values return. */
async function drive(it, strand) {
	let step = it.next();
	try {
		while (!step.done) {
			const yielded = step.value;
			let value, failed = false;
			try {
				if (isThenable(yielded)) throw new Failure(Failure.Defect, Runtime.NativeAwaitable);
				const result = strand.perform(yielded);   // synchronous unless an effect or concurrent instruction awaits
				value = isThenable(result) ? await result : result;
			} catch (error) { value = strand.classify(error); failed = true; }
			if (failed && value.kind === Failure.Cancel) throw value;   // closed below, never thrown in
			step = failed ? it.throw(value) : it.next(value);
		}
		return step.value;
	} finally {
		if (!it.return().done) throw new Failure(Failure.Defect, Runtime.CloseYielded);
	}
}

export async function run(env, stage, ...inputs) {
	const strand = new Strand(new Run(env));   // Run: authority, budgets, journal, replay; Strand: stack, ledger, signal
	try { return await drive(stage(strand.env, ...inputs), strand); }
	catch (failure) { throw strand.attach(failure); }
}
```

Immediate instructions and plain values resolve synchronously; performed effects
and concurrent instructions await.
`join` and `race` use `Promise.allSettled` / `Promise.race` over child strands, each
with an `AbortController` derived from the strand's signal. Effect descriptions and
instructions are instances of record classes (`Effect`, `Report`, `LedgerRead`,
`Join`, `Race`, `Fork`, `Parallel`), so a plain object a stage yields is always a
value. `Run`, `Strand` and the record classes are not exported from `runtime.js`.

Origins (spec-005 provenance) are kept in a `WeakMap` from container to origin,
owned by the run. Marking walks a result once, when it is resumed into the
stage.

## Example

```js
import { Any, Text, Int, DateTime, DateOnly, Positive, Null, loose, either, refine, slots, codes, optional, via, Location } from "./shapes.js";
import { stages, pipe, guard, select, each, project, collect, Policy } from "./combinators.js";
import { join, p } from "./runtime.js";
import { CountryCode, CountryName } from "./vocabulary.js";   // host vocabulary: type and codec table

const Client = slots("Client", {
	Id: refine(Int, Positive), GivenNames: Text, FamilyName: Text,
	BirthDate: either(refine(DateTime, DateOnly), Null), Country: CountryCode,
});
const Mortgage = codes("Mortgage", "RootMismatch");

const RootBound = Root => loose({ application: loose({ id: Root.mortgage }), clients: [Any] });
const ClientRow = loose({ id: Client.Id,
	basicDetails: loose({ firstName: Client.GivenNames, lastName: Client.FamilyName,
		"dateOfBirth?": Client.BirthDate, "countryOfBirth?": via(Client.Country, CountryName) }) });
const PersonRecord = { GivenNames: Client.GivenNames, FamilyName: Client.FamilyName,
	BirthDate: optional(Client.BirthDate), Country: optional(Client.Country), Source: Location };

export const People = stages(S => ({
	/** Root → {application, clients}, retrieved concurrently. */
	*MortgageRetrieve(env) {
		const { mortgage } = env.root;
		const { Application, Clients } = yield join({
			Application: env.RequestSend({ method: "GET", path: p`/mortgages/${mortgage}` }),
			Clients: env.RequestSend({ method: "GET", path: p`/mortgages/${mortgage}/clients` }),
		});
		return { application: Application.data, clients: Clients.data };
	},
	MortgageValidate: guard(env => RootBound(env.root), Mortgage.RootMismatch),
	MortgageClientsSelect: select("clients/*"),
	MortgageClientsProject: each(project(ClientRow, PersonRecord), Policy.Drop),
	PeopleExtract: collect(pipe(S.MortgageRetrieve, S.MortgageValidate,
		S.MortgageClientsSelect, S.MortgageClientsProject)),
}));
```

[`examples/mortgage-people`](../../examples/README.md) has this example with fixtures.

## Testing

Tests run with `bun test`. Drive stages by hand with `inert(env)` to assert on
yielded effect descriptions, replay journals for pipelines, and assert on `kind`,
`code` and `path`, comparing symbols with members.
