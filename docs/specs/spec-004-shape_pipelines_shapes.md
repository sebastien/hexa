# Shape-directed pipelines: shapes and projection

The data side of [spec-001](spec-001-shape_pipelines.md): shapes that match values,
slots that capture them, codecs that convert source formats, selectors that find
occurrences, templates that reproject captures, and the canonical encoding of all of
them.

## Shapes

```text
Name        = Shape
Name(Param) = Shape
Shape       = Type | Literal | Struct | List | Tuple | Union | Pattern | Constrained | Slot | Via | (Shape)
Type        = Any | Text | Num | Int | Float | Bool | Null | Struct | List | DateTime | Instant | Decimal
Literal     = "text" | 12 | true | null | Param.Path
Struct      = {key: Shape, key?: Shape}  |  {key: Shape, ...}
List        = [Shape]
Tuple       = Tuple(Shape, ...)
Union       = Shape | Shape
Pattern     = ~"{Name}-{Id:number}"
Constrained = Shape where Constraint
Slot        = $Namespace.Slot
Via         = $Namespace.Slot via Codec
```

| Form | Matches |
| --- | --- |
| `Any` | Every value |
| `Text` | Strings |
| `Num` | Finite numbers |
| `Int` | Integers within ±2⁵³−1 |
| `Float` | Finite numbers with a fractional part; `1.0` is an `Int` |
| `Bool` | `true` and `false`, never numbers |
| `Null` | `null` |
| `Struct` | Any plain object |
| `List` | Any array |
| `DateTime` | ISO 8601 calendar date strings with optional time and zone |
| `Instant` | Canonical UTC instants (below) |
| `Decimal` | Canonical decimal strings (below) |
| Literal | Equal values |
| `{key: S, key?: S}` | Plain objects with exactly these keys; `key?` may be absent |
| `{key: S, ...}` | Plain objects with at least these keys; others are ignored, never copied |
| `[S]` | Arrays whose every item matches `S`; `[]` matches as known empty |
| `Tuple(S1, S2, ...)` | Arrays of exactly that length, item-wise; `Tuple()` matches only `[]` |
| `S1 \| S2` | The first matching alternative, in order |
| `~"..."` | Whole strings; `{Name}` / `{Name:type}` segments capture text |
| `S where C` | `S`, then the named constraint; stops at the first failure |
| `$N.S` | The slot's canonical shape, then captures the value |
| `$N.S via Codec` | The codec's source shape, then captures the parsed canonical value |

- Precedence, tightest first: `where`, then `|`. Parentheses group otherwise.
- A list literal always has exactly one item shape; tuples are written only with
  `Tuple(...)`, so an item count never changes meaning.
- `Float` follows JSON, not the language: integral values are `Int` in every
  binding, even when a parser produces a native float. `Num` accepts both. Amounts
  that must keep their precision are `Decimal`, not `Float`.
- A parameterised shape `Name(Param)` is instantiated from the environment when its
  stage runs. Parameters supply literals only; they never add predicates or paths.
- Structs are strict unless written loose with `...`. Looseness is visible in the
  shape, never a call-site flag. Loose structs expose only the declared keys.
- A missing key and an explicit `null` remain distinct. `key?` admits absence;
  `S | Null` admits null; neither implies the other.
- Shapes never convert: `"12"` does not match `Num`. Conversion happens only through
  a codec attached with `via`.
- Matching continues past failures in structs, lists and tuples and collects all
  issues. Constraints run only on structurally valid values, left to right.
- An issue is `{code, path, constraint?}`: `code` is `Issue.Mismatch`, `Issue.Missing`,
  `Issue.Unexpected`, `Issue.Constraint` or `Issue.CaptureConflict`; `path` lists keys
  and indexes. Issues never contain the value that failed.

### Date, time and decimals

`DateTime` matches ISO 8601 strings at three precisions:

| Value | Date | Time | Zone |
| --- | --- | --- | --- |
| `2026-11-10` | ✓ | null | null |
| `2026-11-10T14:30`, `2026-11-10T14:30:05.123` | ✓ | ✓ | null |
| `2026-11-10T14:30:05Z`, `2026-11-10T14:30:05+13:00` | ✓ | ✓ | ✓ |

- The date must be a valid calendar date. Time is `HH:MM`, with optional seconds and
  fraction; hours are `00`–`23`. A zone (`Z` or `±HH:MM`) requires a time. Week
  dates, ordinal dates, `24:00` and leap seconds are rejected.
- Standard constraints restrict precision: `DateOnly` (no time), `Timed` (time
  required) and `Zoned` (time and zone required).
- The standard derivation `parts` returns `{date, time, zone}`, with `time` and
  `zone` null when absent.
- `Instant` is the canonical form of a zoned `DateTime`: UTC with `Z`, seconds always
  present, fraction without trailing zeros and omitted when zero, at most nine
  digits. `2026-11-10T01:00:00Z` and `2026-11-10T01:00:00.5Z` are canonical.
- `Decimal` is a canonical decimal string: optional `-`, no exponent, no `+`, no
  leading zeros before the point except a single `0`, no trailing fractional zeros,
  no trailing point, and `0` rather than `-0`. `12`, `-0.5` and `1250.75` are
  canonical.
- Standard codecs: `InstantOffset` parses a zoned `DateTime` into an `Instant`,
  reporting the loss `InstantOffset.Discarded`; `DecimalText` parses a decimal string
  or a safe finite number, including exponents, into a `Decimal`. Both are exact
  string arithmetic, never native dates or floats.

## Slots and codecs

```text
Client = slots {Id: Int where Positive, GivenNames: Text, FamilyName: Text,
                BirthDate: DateTime where DateOnly | Null, Country: CountryCode}

CrmClient = {id: $Client.Id, countryName?: $Client.Country via CountryName, ...}
ApiClient = {clientId: $Client.Id, country?: $Client.Country via CountryIso3}
```

- A slot namespace declares each slot's canonical shape once. Slots are interned by
  qualified name: `Client.Id` is the same slot wherever it is written. An unknown
  name is an error when the shape is built.
- A slot written plainly in a shape expects the canonical shape at that position. A
  slot written `via Codec` expects the codec's source shape there; the captured value
  is the codec's canonical output.
- The source format belongs to the position, not the slot: one slot reads and writes
  any number of source formats.
- A repeated slot joins on canonical values with JSON equality; a mismatch is
  `CaptureConflict`. Codecs make joins across formats exact: `InstantOffset` turns
  `…14:00+13:00` and `…01:00Z` into one `Instant`.
- A capture privately retains the raw source value and its location as evidence. The
  canonical value is what flows on.
- A capture normalises numbers in every binding: an integral value is captured as an
  integer and `-0` as `0`. Both denote the same JSON value, so this is not conversion;
  it makes outputs and joins identical across bindings.
- `slot(Name, Shape)` is the underlying primitive for programmatically built slots.

### Codecs

A codec is a named pair: `parse` from source to canonical, `format` from canonical
to source. Either side yields a value or raises a gap. Codecs are pure, registered
in the vocabulary, and either code or tables.

```text
CountryName = codec {source: Text, canonical: CountryCode,
                     map: {AU: Australia, GB: United Kingdom, NZ: [New Zealand, Aotearoa New Zealand]},
                     gaps: {Unknown: NotStated}}
```

- A table maps each canonical value to its source value, or to a list whose first
  item formats and whose others only parse. Canonical values are on the left. Tables
  are data, so they serialise and can be reviewed.
- `gaps` maps source values to reasons the codec declares: parsing `Unknown` raises
  the gap `CountryName.NotStated`. A source value appears at most once across `map`
  and `gaps`; every `map` key must match the canonical shape.
- Table keys and source values are text; other canonical or source shapes need a
  code codec.
- An unlisted source value raises the gap `<Codec>.Unmapped`; a canonical value
  missing from `map` raises `<Codec>.Unformattable`. A codec never falls back to `Other`,
  `Unknown` or a default.
A code codec supplies the two functions and declares its reasons:

```text
InstantOffset = codec {source: DateTime where Zoned, canonical: Instant,
                       parse, format, gaps?: [Name, ...], losses: [Discarded]}

parse(Source)     → Canonical | lossy(Canonical, Reason) | raises Gap(Reason)
format(Canonical) → Source | raises Gap(Reason)
```

- `gaps` and `losses` list reason names; each declares the code `<Codec>.<Name>`, as
  table `gaps` do. `Unmapped` and `Unformattable` are always declared.
- `parse` and `format` are pure functions of one value: no environment, effects,
  clocks or randomness.
- `lossy(Value, Reason)` is data: the value plus a declared loss reason. The runtime
  unwraps it and reports the loss.
- Output must match the declared shape on its side, and every raised or returned reason
  must be declared; otherwise the codec is a `Defect`.
- A lossy parse reports a `Ledger.Loss` entry, with the slot's qualified name as
  `target`, the occurrence's location as `source` and the position within it as `path`.
- A failed union branch discards its captures and losses; only the matching branch
  reports.

## Selectors and occurrences

```text
clients/*/basicDetails
data/households/*/household/{id:$Household.Id}
```

- A selector enumerates occurrences `{value, location}`. The selector says where to
  look; a shape says what must be there. A selector is a `/`-separated list of
  property names and `*` for array elements, optionally ending in a capture block. It
  has no recursive descent, indexes or filters.
- A final capture block is a loose struct: `path/{id:$Household.Id}` matches as
  `loose({id: Household.Id})` at that path.
- Locations are JSON Pointers into the value given to the selector, not into the
  response it came from: in the spec-001 example, `/clients/0` points into the data
  `MortgageRetrieve` returns. Provenance back to an effect is not yet specified.
- Issue and ledger paths are key lists relative to the location, everywhere including
  results returned to callers.
- A missing property or wrong container type is a gap at that location, not an
  empty match. A known empty array supplies no occurrences and no gap.
- Joining independent plural selections requires a shared slot; never a positional
  zip or Cartesian product.

## Templates and projection

```text
PersonRecord = {GivenNames: $Client.GivenNames, BirthDate: $Client.BirthDate?,
                DisplayName: PersonNameCompose($Client.GivenNames, $Client.FamilyName),
                Country: $Client.Country via CountryIso3, Source: $Location}
```

Template leaves are slot references, optional references (`$S?`), formatted
references (`$S via Codec`), derivations (`Name($A, $B)`, with declared slot inputs)
and literals. Structs, lists and tuples expand recursively. `$Location` is the
standard slot bound to an occurrence's pointer.

| Operation | Signature | Meaning |
| --- | --- | --- |
| `errors` | `(Value, Shape) → Issues` | Diagnostics only |
| `check` | `(Value, Shape, Code) → Value` | The original value, or a `Contract` failure with `Code` |
| `capture` | `(Value, Shape) → Captures` | Match and parse; a `Shape` failure otherwise |
| `expand` | `(Template, Captures) → [Value, Holes]` | A fresh value and the paths of unbound references |
| `reshape` | `(Shape, Template, Value, Captures?) → Value` | `capture`, then `expand`; extra captures follow the join rule |
| `matches` | `(Value, Selector) → [Occurrence]` | Enumerate occurrences |

- Expansion runs only after a successful match and copies: outputs share no mutable
  structure with inputs or each other. Output is a whitelist of template keys.
- An unbound reference is a **hole**. `expand` leaves its key out and returns its
  path; `project` reports each hole as a gap at that path. `$S?` marks a reference
  whose absence is expected and reports nothing.
- `project` checks when it is built that every template reference, including
  derivation inputs, is captured by its shape. A slot captured only in some union
  branches must be referenced as optional.
- Derivations are registered, pure functions of their declared slots. A failed
  derivation raises a gap naming its reason.
- **Two-way shapes.** A shape whose leaves are only slots, `via` slots and literals is
  also a template; used as a template, an optional key `key?` is an optional
  reference. `reshape(CrmClient, ApiClient, row)` parses the CRM format and formats
  the API format, with no conversion code. A pair of such shapes is a data mapping.
- A projection maps one value to one value. Mapping rows is `each`.

## Encoding and profiles

Shapes, slot namespaces, templates, codec tables, policies and effects have a
canonical JSON encoding, so programs and runs are storable, comparable and
inspectable.

```json
{"$loose": {"id": "$Client.Id",
            "countryName?": {"$": "Client.Country", "via": "CountryName"},
            "tags": ["$Text"],
            "range": {"$tuple": ["$Int", "$Int"]},
            "status": {"$either": ["Active", "Closed", "$Null"]},
            "$$ref": "literal key"}}
```

- A string beginning with `$` is a reference: a type (`$Text`), a shape (`$ClientRow`)
  or a slot (`$Client.Id`). Slot references are qualified; `$Location` is the only
  unqualified standard slot.
- An object with a `$`-key is an operator: exactly one `$`-key plus camelCase
  options. `{"$": Ref, ...}` is a reference with options (`via`, `optional`);
  constructors are `$loose`, `$tuple`, `$either`, `$refine`, `$pattern` and
  `$derive`.
- `$$` prefixes a verbatim literal: `"$$Text"` is the string `$Text`, and the key
  `"$$ref"` is the literal key `$ref`. A verbatim key is never optional.
- Other strings, numbers, booleans and `null` are literals; a key ending in `?` is
  optional; a one-item array is a list shape.
- Constraints, derivations, transforms and codecs appear only as registered names.
  Encoded values never contain code.
- A codec declaration encodes as `{"$codec": {"source", "canonical", ...}}`. A table
  codec includes `map` and `gaps` and decodes into a working codec. A code codec
  encodes its signature only (`gaps` and `losses` as name lists), which is what
  `CatalogDescribe` publishes; decoding it requires a registered codec of that name
  with an identical signature.
- Enum members and codes are references: `"$Policy.Drop"`, `"$Mortgage.RootMismatch"`.
  A policy with options encodes as `{"$": "Policy.Drop", "on": ["$Failure.Shape"]}`.

A **profile** restricts the encoding for callers: permitted types, shapes, slots,
constraints, derivations and codecs, plus node, depth and size budgets. A data shape
is an encoded shape conforming to the host's profile; it is decoded, checked and
compiled into a plan. It cannot introduce a term, path, predicate or effect the host
did not register.

Domain profiles offer compact request formats that compile into the general form or
directly into a plan. The selections/relations request is the first:

```json
{"version": 1,
 "selections": {"People": {"from": "Clients", "attributes": ["GivenNames", "BirthDate"]}},
 "relations": [{"type": "HouseholdHasMemberPerson", "participants": {"source": "Household", "target": "People"}}]}
```

Caller aliases are PascalCase. General caller-sent templates are deferred until a
pipeline needs them.

## Still to draft

1. Profile format and the decoding of selections/relations into plans. Deferred until
   a pipeline consumes caller-sent requests.
