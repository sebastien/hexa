# Shape-directed pipelines: Python binding

Binds [spec-001](spec-001-shape_pipelines.md),
[spec-004](spec-004-shape_pipelines_shapes.md) and
[spec-005](spec-005-shape_pipelines_stages.md) to Python. It defines syntax, native
semantics and module layout only; concepts, rules and canonical names come from
those specs. Targets Python 3.14+ (template strings), standard library only. Names match
the [JavaScript binding](spec-002-shape_pipelines_javascript.md) exactly, including
PascalCase stages and slots and camelCase data keys, by deliberate exception to PEP 8.

## Modules

| Module | Exports |
| --- | --- |
| `shapes.py` | Types, shape constructors, slots, codecs, `lossy` and `codes`, standard vocabulary, operations, `encode`, `decode`, `Failure`, `Gap`, `Issue` |
| `combinators.py` | Combinators, `Policy`, `backoff` |
| `runtime.py` | `run`, `report`, `ledger`, `join`, `race`, `fork`, `inert`, `p`, `Ledger`, `Coverage`, `Checks`, `Runtime` |

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
| `A \| B` | `either(A, B)` |
| `~"..."` | `pattern("...")` |
| `S where C` | `S & C`, or `refine(S, C)`; custom: `S & where(is_even, "Even")` |
| `$N.S via Codec` | `N.S @ Codec`, or `via(N.S, Codec)` |
| `$N.S?` in a template | `~N.S`, or `optional(N.S)` |
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
- `Any` and `List` are this library's shapes, not `typing.Any` / `typing.List`; do not
  import both into one module.
- `where(predicate, code)` wraps a pure predicate as a named constraint. Standard
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
- `slot(QualifiedName, Shape)` creates a single slot; `Location` is exported.
- `derive(Transform, *Slots)` applies a registered pure function:
  `derive(PersonNameCompose, Client.GivenNames, Client.FamilyName)`.
- `codec(Name, source=, canonical=, parse=, format=, gaps=, losses=)` declares a code
  codec; `gaps` and `losses` are lists of reason names. `parse` and `format` are pure,
  raise `Gap(Codec.Reason)` on failure, and `parse` returns `lossy(value, Codec.Loss)`
  for a lossy result.
  `codec(Name, source=, canonical=, map=, gaps=)` declares a table: `map` is a dict
  from canonical value to a source string or a list of them, the first formatting;
  `gaps` maps source strings to reason names, declaring the codes
  `CountryName.NotStated` etc.

## Enums and codes

```python
Mortgage = codes("Mortgage", "RootMismatch")

guard(lambda env: RootBound(env.root), Mortgage.RootMismatch)
each(project(ClientRow, PersonRecord), Policy.Drop)
each(project(ClientRow, PersonRecord), Policy.Drop, on=[Failure.Shape, Failure.Gap, Failure.Effect], target=Client)
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
  Calling a stand-in operation outside a yield does nothing.
- Pure helpers use snake_case, with a leading underscore when module-private.

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
async def _drive(it, scope):
    """Drive one strand: effects and instructions go to `scope`, plain values return."""
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
                result = scope.perform(yielded) if _is_effect(yielded) or _is_instruction(yielded) else yielded
                send = (await result) if inspect.isawaitable(result) else result
            except Exception as caught:
                error = scope.classify(caught)
                if error.kind is Failure.Cancel:
                    raise error   # closed below, never thrown in
    finally:
        try:
            it.close()
        except RuntimeError:
            raise Failure(Failure.Defect, Runtime.CloseYielded)

async def run(env, Stage, *inputs):
    scope = _Scope(env)   # stand-in env, ledger, journal, budgets, trace, strands
    try:
        return await _drive(Stage(scope.env, *inputs), scope)
    except Failure as failure:
        raise scope.attach(failure)
```

Instructions and plain values resolve synchronously; only performed effects await.
`join` and `race` run child scopes in an `asyncio.TaskGroup`, cancelling siblings on
the first unrecovered failure. `_Scope`, `_is_effect` and `_is_instruction` are
internal to `runtime.py`.

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

[`examples/mortgage-people`](examples/README.md) has this example with fixtures.

Tests use `python -m unittest` (or pytest, which runs them unchanged). Drive stages
by hand with `inert(env)` to assert on yielded effect descriptions, replay journals
for pipelines, and assert on `kind`, `code` and `path`, comparing with members.
