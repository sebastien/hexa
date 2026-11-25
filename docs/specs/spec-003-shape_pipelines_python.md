# Shape-directed pipelines: Python language binding

Binds [spec-001](spec-001-shape_pipelines.md),
[spec-004](spec-004-shape_pipelines_shapes.md) and
[spec-005](spec-005-shape_pipelines_stages.md) to Python. It defines syntax, native
semantics and module layout only; concepts, rules and canonical names come from
those specs. Targets Python 3.14+ (template strings), standard library only. Names match
the [JavaScript binding](spec-002-shape_pipelines_javascript.md) exactly, including
PascalCase stages and slots and camelCase data keys, by deliberate exception to PEP 8.

## Modules

The modules form the package `hexa`: `hexa.shapes`, `hexa.combinators` and
`hexa.runtime`. The package has no dependencies, so libraries building on it, such
as domain languages, may load it lazily and stay usable without it.

| Module | Exports |
| --- | --- |
| `shapes.py` | Types, shape constructors (including `tolerant` and `gather`), slots, `Location`, `Origin`, codecs, `transform`, `lossy` and `codes`, standard vocabulary, operations (including `evidence` and `parsed`), `encode`, `decode`, `profile`, `request`, `describe`, `SelectionRequest`, `Failure`, `Gap`, `Issue`, `Profile` |
| `combinators.py` | Combinators (including `validate` and `plan`), `Policy`, `backoff` |
| `runtime.py` | `run`, `report`, `ledger`, `join`, `race`, `fork`, `inert`, `p`, `Ledger`, `Coverage`, `Checks`, `Runtime` |
| `http.py` | `http` (the `RequestSend` operation over `urllib`, or a host `transport`), `Request` |

`shapes.py` is a façade over the internal modules `shape` (algebra and matching),
`template`, `codec`, `selector`, `encoding`, `profile`, `selectionrequest` (the
concrete selection format) and `vocabulary`; `combinators.py` and `runtime.py`
build on `stage` and `instructions`. Shared declaration identity and reason
helpers live in `core`. Programs import only the three public modules; hosts
also import `hexa.http` for HTTP APIs (spec-005, HTTP requests). In Python the
base URL is positional and the rest keywords: `http(base=None, *, apis=None,
headers=None, transport=None, timeout=None)`. A `transport(method, url, headers,
body, timeout)` takes the body as bytes and `timeout` in seconds, returns
`(status, reason, headers, body bytes)`, and is plain (run in a thread) or `async`.

Import canonical names unchanged; never rename on import. Configure linters to
allow PascalCase functions and camelCase arguments in pipeline modules (e.g. ruff
`N802`, `N803`, `N806`).

## Shapes

| Notation | Python |
| --- | --- |
| Types | `Any`, `Text`, `Num`, `Int`, `Float`, `Bool`, `Null`, `Struct`, `List`, `DateTime`, `Instant`, `Decimal` |
| Literal | `"Applicant"`, `12`, `True`, `None` |
| `{k: S, k?: S}` | `{"k": S, "k?": S}` |
| `{k: S, ...}` | `loose({"k": S})` |
| `[S]`, `Tuple(A, B)` | `[S]`, `Tuple(A, B)` |
| One ignored position, arbitrary rest | `Any`, `__` (shorthand for `rest(Any)`) |
| Typed or captured tuple rest | `Tuple(Int, rest(Text), Text)`, `Tuple(Int, rest(Items.Values))` |
| `A \| B` | `either(A, B)` |
| `~"..."` | `pattern("...")` |
| `S where C` | `S & C`, or `refine(S, C)`; custom: `S & where(is_even, "Even")` |
| `$N.S via Codec` | `N.S @ Codec`, or `via(N.S, Codec)` |
| `$N.S via Codec(Arg)` | `N.S @ DatePattern("YYYYMMDD")` |
| `[$N.S]`, collection capture | `[N.S]`, `[N.S @ Codec]` |
| `tolerant(S)` | `tolerant(S)` |
| `gather($N.S)` | `gather(N.S)`, `gather(N.S @ Codec)` |
| `parsed($N.S)` in a template | `parsed(N.S)` |
| `$N.S?` in a template | `~N.S`, or `optional(N.S)` |
| `Name($A, "x", G($B))` in a template | `derive(Name, A, "x", derive(G, B))` |
| `Name(Param)` | `def RootBound(Root): return loose({...})`, used as `lambda env: RootBound(env.root)` |

- `Text` matches `str`; `Bool` matches `bool` only. `Num` matches finite `int` or
  `float`, never `bool`. `Int` matches `int`, or a `float` with `is_integer()`, within
  ±2⁵³−1 and never `bool`, so `json.loads("1.0")` is an `Int` as in JavaScript.
  `Float` matches finite `float` values without `is_integer()`. A capture stores an
  integral `float` as `int` and `-0.0` as `0`, so outputs serialise as in JavaScript
  ([spec-004](spec-004-shape_pipelines_shapes.md) number normalisation).
- `DateTime`, `Instant` and `Decimal` are checked by the [spec-004](spec-004-shape_pipelines_shapes.md) grammar, never by
  `datetime.fromisoformat` or `decimal.Decimal`, which accept other forms.
- Structs match `dict` with `str` keys. `"k?"` treats an absent key as absent; a key
  holding `None` is a present null.
- `List`, `[S]` and `Tuple(...)` match `list` values only; Python tuples are not JSON
  values. A list literal of any other length than one is rejected as a shape.
- `Tuple` allows at most one `rest(S)`, consuming zero or more positions between
  its fixed prefix and suffix. `__` is exported by `hexa.shapes` and encodes as
  `{"$rest": "$Any"}`. A collection slot in `rest` captures the consumed items.
- A template list or tuple with missing positions expands to an index map, for
  example `{0: 10, 2: 30}` with a hole at `[1]`. JSON serialization writes the
  index keys as strings. Complete containers remain lists.
- `Any` and `List` are this library's shapes, not `typing.Any` / `typing.List`; do not
  import both into one module.
- `where(predicate, name)` wraps a pure predicate as a named constraint. `name` is a
  qualifier string (`"Even"`), not a code: constraints register under their own name,
  and a failed constraint reports `Issue.Constraint` with that name. Standard
  constraints (`DateOnly`, `Timed`, `Zoned`, `Positive`, `Nonempty`) are exported
  ready to use.

### Operators

`&`, `@` and `~` are syntax for `refine`, `via` and `optional`: they build the same
values, which encode and trace under the canonical names. Python examples use them.

- `S & C` requires a constraint on the right and works with any shape on the left,
  including literals, dicts and lists (through `__rand__`). `S & C1 & C2` is
  `refine(S, C1, C2)`.
- `@` requires a slot on the left and a codec on the right. `~` applies to slots in
  templates only; in a shape, where absence is written `"key?"`, a `~` slot is an
  error when the shape is built.
- There is no `|` for `either`: `{...} | {...}` on two dicts is a silent dict merge and
  `"a" | "b"` a `TypeError`, so unions are always written `either(...)`.

Compatibility aliases, accepted but never used in documentation: `str`, `int`,
`float`, `bool`, `dict` and `list` for `Text`, `Int`, `Num`, `Bool`, `Struct` and
`List`.

## Slots and codecs

```python
Client = slots("Client",
    Id=Int & Positive, GivenNames=Text, FamilyName=Text,
    BirthDate=either(DateTime & DateOnly, Null), Country=CountryCode)

CountryName = codec("CountryName", source=Text, canonical=CountryCode,
    map={"AU": "Australia", "NZ": ["New Zealand", "Aotearoa New Zealand"]},
    gaps={"Unknown": "NotStated"})

CrmClient = loose({"id": Client.Id, "countryName?": Client.Country @ CountryName})
```

- Library options are keyword arguments, never option dicts: `slots`, `codec`,
  `codes`, `stage` contracts, policy options, `retry` and `backoff`. Shapes, templates
  and effect arguments stay dicts, because they are data.
- `slots(Namespace, **Shapes)` returns an immutable namespace. Each member is a frozen,
  hashable slot interned by qualified name; captures are keyed by slot internally.
- Accessing an unknown member raises `AttributeError` with code `Runtime.SlotUnknown`. Names
  starting with `_` always raise `AttributeError`, so `copy`, `pickle` and debuggers
  never create slots.
- Slot names are interned process-wide: redeclaring `Client.Id` with a different
  shape raises `Runtime.SlotRedeclared`.
- `slots("Exchange.Instrument", Isin=Text)` declares a nested namespace: its
  members are `Exchange.Instrument.Isin`. A namespace name is the full dotted
  prefix; there is no parent object to declare first.
- `slot(QualifiedName, Shape)` creates a single slot; `Location` and `Origin` are
  exported.
- `derive(Transform, *Arguments)` applies a transform. Arguments are slots, `~`
  slots, literals or other `derive` values:
  `derive(PersonNameCompose, Client.GivenNames, Client.FamilyName)`.
- `transform(Name, function, inputs=[...], output=Shape, gaps=[...],
  examples=[...])` declares a transform; `function` takes the arguments
  positionally and raises `Gap(Transform.Reason)` on failure.
- `evidence(captures, slot)` returns `{"raw": ..., "location": ..., "codec": ...}`
  (`codec` omitted for a plain capture) or `None`.
- `codec(Name, source=, canonical=, parse=, format=, gaps=, losses=)` declares a code
  codec; `gaps` and `losses` are lists of reason names. `parse` and `format` are pure,
  raise `Gap(Codec.Reason)` on failure, and `parse` returns `lossy(value, Codec.Loss)`
  for a lossy result. `format(value, original=None)` receives the captured raw value
  as `original` when there is one.
- `codec(Name, source=, canonical=, map=, gaps=)` declares a table: `map` is a dict
  from canonical value to a source string or a list of them, the first formatting;
  `gaps` maps source strings to reason names, declaring the codes
  `CountryName.NotStated` etc.
- `codec(Name, params={"Pattern": Text}, ...)` declares a parameterised codec. It
  returns a codec family: calling it, `DatePattern("YYYYMMDD")`, checks the arguments
  and returns an interned instance. `parse` and `format` receive the arguments as
  leading positional parameters: `parse(pattern, value)`.
- `examples=[...]` on `codec` and `transform` takes the spec-004 data form as dicts:
  `{"input": "20260915", "output": "2026-09-15"}`,
  `{"input": "2026-02-30", "gap": "Invalid"}`. Examples of a parameterised codec add
  `"arguments": ["YYYYMMDD"]`.

## Profiles and requests

```python
Advisor = profile("AdvisorProfile",
    constructors=[loose, either, refine], types=[Text, Int], shapes={"ClientRow": ClientRow},
    slots=[Client], constraints=[Positive], codecs=[CountryName, DatePattern],
    requests={SelectionRequest: {"versions": [1], "maxSelections": 8, "maxRelations": 8,
                                 "entitySets": {"Clients": ["GivenNames", "BirthDate"]},
                                 "relations": ["HouseholdHasMemberPerson"]}},
    budgets={"maxDepth": 16, "maxNodes": 2000})

shape = decode(callerShape, Advisor)

class Extraction(stages):
    RequestValidate = validate(SelectionRequest)
    PlanCompile = plan(SelectionRequest)
    RequestPrepare = pipe(RequestValidate, PlanCompile)

env = {"policy": {"profile": Advisor, "allow": ["client_read"]},
       "vocabulary": {"SelectionRequest": catalog}}
```

- `profile(Name, *, constructors, types, shapes, slots, constraints, derivations,
  codecs, requests, budgets)` takes the objects themselves, never names. Constructors
  are the constructor functions (`loose`, `Tuple`, `rest`, `either`, `refine`,
  `pattern`, `tolerant`, `gather`, `parsed`, `derive`). `shapes` is a dict, because
  Python shapes have no names: it registers each shape under its key, so callers
  reference `"$ClientRow"`. `slots` takes slots or slot namespaces. `requests` maps
  request formats to their option dicts.
- A mistake in a `profile` or `request` call raises `TypeError` or `ValueError`.
  Redeclaring a name with different contents raises `Runtime.NameRedeclared`.
- `decode(value, profile=None, *, registry=None, maxDepth=…, maxNodes=…,
  maxBytes=…)`: with a profile, the profile's budgets replace the keyword limits.
- `request(Name, *, versions, options, validate, compile, gaps=())` declares a
  request format; `validate` and `compile` are plain functions, as for codecs.
- `env.policy["profile"]` holds the profile object and `env.policy["allow"]` a list
  of strings. The runtime rejects other values with `Failure.Contract`.

## Enums and codes

```python
Mortgage = codes("Mortgage", "RootMismatch")

guard(lambda env: RootBound(env.root), Mortgage.RootMismatch)
each(project(ClientRow, PersonRecord), Policy.Drop)
each(project(ClientRow, PersonRecord), Policy.Drop, on=[Failure.Shape, Failure.Gap, Failure.Effect], target=Client)
project(DividendRow, DividendRecord, outer=[Instrument.Isin], holes=Policy.Abort)
```

- Enum members and codes are interned, hashable sentinels whose `repr` and `str` are
  the qualified name (`Failure.Gap`), exposed on immutable namespaces. They are not
  `enum.Enum` members, so namespaces can hold constructors and program codes alike.
- Namespaces behave like slot namespaces: an unknown member raises `AttributeError`
  with code `Runtime.MemberUnknown`.
- `Failure` is both the exception class and the kind enum: `Failure.Gap` is a class
  attribute. `Policy.Substitute(value)` and `Policy.Fallback(S)` return frozen policy
  values; the other `Policy` members are sentinels.
- Members are not JSON. The runtime and `encode` write their data names; stages never
  compare data strings against string literals.

## Stages and effects

```python
def MortgageRetrieve(env):
    """Root → mortgage response data."""
    mortgage = env.root["mortgage"]
    Response = yield env.RequestSend({"method": "GET", "path": p(t"/mortgages/{mortgage}")})
    return Response["data"]

def MortgageDealsRetrieve(env, Id):
    """Mortgage id → {Mortgage, Deals}, retrieved concurrently."""
    return (yield join({
        "Mortgage": env.RequestSend({"method": "GET", "path": p(t"/mortgages/{Id}")}),
        "Deals": env.RequestSend({"method": "GET", "path": p(t"/mortgages/{Id}/deals")}),
    }))
```

| Concept | Python |
| --- | --- |
| Stage | `def Name(env, input, ...)` containing `yield` |
| Stage namespace | `class People(stages):` with `def X(env, a)` and `Y = S1` in the body |
| Contract | `Y = stage(S1, input=..., output=...)`, or `@stage(input=..., output=...)` on `def X(env, a)`, in a namespace |
| Programmatic stage | `stage(name, S)`, `stage(name, S, input=..., output=...)` with a computed `name` |
| Delegate | `out = yield from Stage(env, input)` |
| Effect | `result = yield env.Operation(*args)` |
| Path | `p(t"/mortgages/{Id}/deals")` |
| Concurrency | `yield join([...])`, `yield join({...})`, `yield race(...)`, `h = yield fork(S, input)` |
| Ledger | `yield report(Ledger.Gap, target=..., reason=...)`; `entries = yield ledger()` |
| Policy | `each(S, Policy.Drop)`; `recover(S, Policy.Substitute([]), on=[Failure.Gap])` |
| Retry | `retry(S, attempts=3, delay=backoff(250))` |

- A hand-written stage must be a generator. A step without effects or delegation is a
  pure function wrapped with `lift`, not a generator with a dummy `yield`.
- `p` takes a `string.templatelib.Template` and rejects a `str`, so an f-string cannot
  bypass segment checking. Interpolations are checked as `Text` or `Int` segments and
  encoded with `urllib.parse.quote(value, safe="")`.
- The runtime passes a frozen stand-in as `env`. Data attributes (`root`, `policy`,
  `vocabulary`, `scope`) are read-only mappings read by key
  (`env.policy["maxGaps"]`); operation attributes return effect descriptions.
  `env.strand` is the runtime's handle on the executing strand; stages never use it.
  Calling a stand-in operation outside a yield does nothing.
- Pure helpers use camelCase without a leading underscore; `__all__` declares each
  module's exports.
  Classes and static methods use PascalCase; instance methods and fields use
  camelCase. Canonical API and Python protocol names retain their exact spelling.
- Implementation modules use tabs, a `Module: name — description` docstring,
  future annotations, definitions before utilities and operations, high-level API
  functions last, and an explicit `# EOF` marker.

### Stage namespaces

```python
class People(stages):
    def MortgageRetrieve(env):
        """Root → mortgage response data."""
        mortgage = env.root["mortgage"]
        Response = yield env.RequestSend({"method": "GET", "path": p(t"/mortgages/{mortgage}")})
        return Response["data"]

    MortgageClientsSelect = select("clients/*")
    PeopleExtract = collect(pipe(MortgageRetrieve, MortgageClientsSelect))
```

A stage namespace is a class body deriving from `stages`. Python calls
`__set_name__` on each value assigned in a class body, which is how a combinator
result learns its name; no frame inspection is involved.

- A generator `def` in the body is a hand-written stage, named by its function name.
  A stage value assigned in the body is named by its attribute name through
  `__set_name__`; one already named differently raises `Runtime.StageRenamed`.
- The body declares stages only: shapes, slots, codes and helpers stay at module
  level, and any other non-dunder value in the body is an error when the class is
  created.
- The body runs top to bottom, so declarations reference siblings declared above them
  by bare name. Class scope is not visible inside function bodies, lambdas or
  comprehensions: a hand-written stage delegates with
  `yield from People.MortgageRetrieve(env)`, and `guard`'s `lambda env: ...` may
  read only module names and `env`.
- `stages` is lowercase like `dict`, because canonical names are spelled identically
  in every binding. Its metaclass makes the class a frozen namespace: assignment
  raises, an unknown attribute raises `AttributeError` with code
  `Runtime.MemberUnknown`, and instantiation raises `TypeError`. Members are used on
  the class: `run(env, People.PeopleExtract)`, `People.MortgageRetrieve(env)`.

## Host environment

`run` and `inert` accept a host-provided mapping. `run` validates and snapshots
authority and configuration before exposing a frozen stand-in. `EnvironmentCreate`
is an optional host-owned helper, not a required hexa API.

```python
env = EnvironmentCreate(
    root={"mortgage": 42}, scope=tenant_scope, vocabulary=catalog,
    policy={"contracts": Checks.Boundary, "maxGaps": 1000, "maxConcurrency": 4,
            "retry": {"default": True, "attempts": 3, "delay": backoff(250)}},
    RequestSend=lambda request, context: transport(request, context["signal"]),
)
result = await run(env, People.PeopleExtract)   # {"value": [...], "ledger": [...]}
```

- Operations receive the effect's arguments followed by a context
  `{"signal", "attempt", "strand"}`, and may be plain or `async` functions. `signal`
  is an `asyncio.Event` set on cancellation; async operations are also cancelled as
  tasks.
- There is no synchronous entry point; synchronous callers use
  `asyncio.run(run(env, Stage, ...))`.

## Failures and cancellation

```python
class Failure(Exception):
    Contract = member("Failure.Contract")   # … Budget, Cancel, Defect, Shape, Gap, Effect

    def __init__(self, kind, code, path=None, location=None):
        super().__init__(str(code))
        self.kind, self.code, self.path, self.location = kind, code, path, location

class Gap(Failure):
    def __init__(self, code, path=None, location=None):
        super().__init__(Failure.Gap, code, path, location)
```

| `Failure` | Python source |
| --- | --- |
| `Contract`, `Budget`, `Shape`, `Gap`, `Defect` | `Failure` with that kind (`Gap` for gaps) |
| `Effect` | An `Exception` raised by a host operation, classified by `FailureClassify` |
| `Cancel` | `asyncio.CancelledError` or `TimeoutError` |
| `Defect` | Any other `Exception`, chained as `__cause__` |

- A `Cancel`, whether `asyncio.CancelledError` or a classified `TimeoutError`, is
  never thrown into a generator, so `recover` never sees it. The runtime closes the
  generator so `finally` blocks run, then propagates. A generator that yields while
  closing makes `close()` raise `RuntimeError`, which becomes `Runtime.CloseYielded`.
- The runtime adds `trace` and, on run failure, `ledger`, `journal` and `cleanup`
  attributes excluded from `str()` and `repr()`.

## Runtime

```python
async def drive(it, strand):
    """Drive one strand: effects and instructions go to `strand`, plain values return."""
    send, error = None, None
    try:
        while True:
            try:
                yielded = it.throw(error) if error else it.send(send)
            except StopIteration as stop:
                return stop.value
            send, error = None, None
            try:
                if inspect.isawaitable(yielded):
                    raise Failure(Failure.Defect, Runtime.NativeAwaitable)
                result = strand.perform(yielded)   # effect and instruction records act; other values pass through
                send = (await result) if inspect.isawaitable(result) else result
            except Exception as caught:
                error = strand.classify(caught)
                if error.kind is Failure.Cancel:
                    raise error   # closed below, never thrown in
    finally:
        try:
            it.close()
        except RuntimeError:
            raise Failure(Failure.Defect, Runtime.CloseYielded)

async def run(env, stage, *inputs):
    strand = Strand(Run(env))   # Run: authority, budgets, journal, replay; Strand: stack, ledger, signal
    try:
        return await drive(stage(strand.env, *inputs), strand)
    except Failure as failure:
        raise strand.attach(failure)
```

Instructions and plain values resolve synchronously; only performed effects await.
`join` and `race` run child strands in an `asyncio.TaskGroup`. Live joins cancel
siblings on the first unrecovered failure; live races cancel remaining participants
after their first completion. Replay drains recorded participant work, including
nested joins, and uses explicit per-strand race-ordinal decisions rather than task
completion order. These decisions do not consume effect `seq`. `Run`, `Strand` and
the instruction records (`Effect`,
`Report`, `LedgerRead`, `Join`, `Race`, `Fork`, `Parallel`) are internal, absent from
every `__all__`.

Origins (spec-005 provenance) are kept in the run as a dict from `id(container)`
to `(container, origin)`. Holding the container keeps its `id` from being reused
during the run; the dict is dropped with the run. Marking walks a result once,
when it is resumed into the stage.

## Example

```python
from .shapes import Any, Text, Int, DateTime, DateOnly, Positive, Null, loose, either, slots, codes, Location
from .combinators import stages, pipe, guard, select, each, project, collect, Policy
from .runtime import join, p
from .vocabulary import CountryCode, CountryName   # host vocabulary: type and codec table

Client = slots("Client",
    Id=Int & Positive, GivenNames=Text, FamilyName=Text,
    BirthDate=either(DateTime & DateOnly, Null), Country=CountryCode)
Mortgage = codes("Mortgage", "RootMismatch")

def RootBound(Root):
    return loose({"application": loose({"id": Root["mortgage"]}), "clients": [Any]})

ClientRow = loose({"id": Client.Id,
    "basicDetails": loose({"firstName": Client.GivenNames, "lastName": Client.FamilyName,
        "dateOfBirth?": Client.BirthDate, "countryOfBirth?": Client.Country @ CountryName})})
PersonRecord = {"GivenNames": Client.GivenNames, "FamilyName": Client.FamilyName,
    "BirthDate": ~Client.BirthDate, "Country": ~Client.Country, "Source": Location}

class People(stages):
    def MortgageRetrieve(env):
        """Root → {application, clients}, retrieved concurrently."""
        mortgage = env.root["mortgage"]
        Responses = yield join({
            "Application": env.RequestSend({"method": "GET", "path": p(t"/mortgages/{mortgage}")}),
            "Clients": env.RequestSend({"method": "GET", "path": p(t"/mortgages/{mortgage}/clients")}),
        })
        return {"application": Responses["Application"]["data"], "clients": Responses["Clients"]["data"]}

    MortgageValidate = guard(lambda env: RootBound(env.root), Mortgage.RootMismatch)
    MortgageClientsSelect = select("clients/*")
    MortgageClientsProject = each(project(ClientRow, PersonRecord), Policy.Drop)
    PeopleExtract = collect(pipe(MortgageRetrieve, MortgageValidate,
        MortgageClientsSelect, MortgageClientsProject))
```

[`examples/mortgage-people`](../../examples/README.md) has this example with fixtures.

Tests use `python -m unittest` (or pytest, which runs them unchanged). Drive stages
by hand with `inert(env)` to assert on yielded effect descriptions, replay journals
for pipelines, and assert on `kind`, `code` and `path`, comparing with members.
