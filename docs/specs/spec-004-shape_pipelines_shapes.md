# Shape-directed pipelines: shapes and projection

The data side of [spec-001](spec-001-shape_pipelines.md): shapes that match values,
slots that capture them, codecs that convert source formats, selectors that find
occurrences, templates that reproject captures, and the canonical encoding of all of
them.

## Shapes

```text
Name        = Shape
Name(Param) = Shape
Shape       = Type | Literal | Struct | List | Tuple | Union | Pattern | Constrained | Slot | Via | Tolerant | Gather | (Shape)
Type        = Any | Text | Num | Int | Float | Bool | Null | Struct | List | DateTime | Instant | Decimal
Literal     = "text" | 12 | true | null | Param.Path
Struct      = {key: Shape, key?: Shape}  |  {key: Shape, ...}
List        = [Shape]
Tuple       = Tuple(Shape | Rest, ...)
Rest        = rest(Shape)
Union       = Shape | Shape
Pattern     = ~"{Name}-{Client.Id:IntegerText}"
Constrained = Shape where Constraint
Slot        = $Namespace.Slot | $Namespace.Path.Slot
Via         = Slot via Codec | Slot via Codec(Argument, ...)
Tolerant    = tolerant(Shape)
Gather      = gather(Slot) | gather(Via)
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
| `[S]` | Arrays whose every item matches `S`; `[]` matches as known empty. `[$N.S]` captures the whole list (see Collection captures) |
| `Tuple(S1, S2, ...)` | Arrays of exactly that length, item-wise; `Tuple()` matches only `[]` |
| `S1 \| S2` | The first matching alternative, in order |
| `~"..."` | Whole strings; `{Name}` / `{Namespace.Slot}` capture text, with optional `:Codec` |
| `S where C` | `S`, then the named constraint; stops at the first failure |
| `$N.S` | The slot's canonical shape, then captures the value |
| `$N.S via Codec` | The codec's source shape, then captures the parsed canonical value |
| `gather($N.S)` | Inside list items: `S`, contributing one element per item to the collection slot |
| `tolerant(S)` | Every value; captures what `S` captures when it matches, and otherwise records the failure against `S`'s slots (see Templates) |

- Precedence, tightest first: `where`, then `|`. Parentheses group otherwise.
- A list literal always has exactly one item shape; tuples are written only with
  `Tuple(...)`, so an item count never changes meaning.
- `Any` ignores one tuple position. A tuple may contain at most one `rest(S)`;
  the fixed prefix and suffix anchor the match, and every intervening value must
  match `S`. The rest may be empty. `rest` is invalid outside `Tuple`. A collection
  slot in `rest` captures the consumed items, as in `[CollectionSlot]`.
- Pattern placeholders may name local captures (`{Name}`) or declared slots
  (`{Client.Name}`). Local captures use string keys in the returned captures and
  never enter the process-wide slot registry. Declared captures use slot keys.
  `:FORMAT` resolves a registered codec, not a native formatter or implicit cast;
  its canonical shape must be compatible with the declared slot. Pattern matching
  is whole-string, using the earliest separator match for unconstrained text spans.
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
- `DatePattern(Pattern)` parses and formats dates written in a pattern of `YYYY`,
  `MM`, `DD` and literal characters (`"YYYYMMDD"`, `"DD/MM/YYYY"`), into a
  `DateOnly` `DateTime`; an invalid or non-matching value raises
  `DatePattern.Invalid`.

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
- Namespaces nest: `Exchange.Instrument = slots {Isin: Text, ...}` declares
  `Exchange.Instrument.Isin`. The qualified name is every segment, and the last is
  the slot. Generated vocabularies (one namespace per described type) use nesting
  instead of flattening names. Nesting only qualifies: a namespace confers nothing
  on its children.
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

### Collection captures

```text
Instrument = slots {Isin: Text, Tags: [Text]}
InstrumentRow = {isin: $Instrument.Isin, tags: [$Instrument.Tags via TagName], ...}
```

- A slot whose canonical shape is a list `[S]` is a collection slot. Written as the
  item shape of a list, `[$N.S]` or `[$N.S via Codec]`, it matches each item against
  `S` (or the codec's source shape) and captures the list of canonical items, in
  source order, as one value.
- Item failures are issues at the item's path, as for any list; a codec gap on one
  item fails the whole capture, so a collection is never captured partially.
- Written plainly, `$N.S` matches and captures a list value as a whole. A collection
  slot never captures a single item, and an item slot never captures a list.
- Joins compare whole lists with JSON equality.

**Gathering.** A collection slot can also collect a position below the items of a
list:

```text
Order = slots {Id: Text, Skus: [Text]}
OrderRow = {id: $Order.Id, lines: [{sku?: gather($Order.Skus), ...}], ...}
```

- `gather($N.S)`, or `gather($N.S via Codec)`, stands at a position inside the item
  shape of one or more enclosing lists. It matches the value there against `S` (or
  the codec's source shape) and contributes one element per item, in document order
  (depth first across nested lists), to one capture of the collection slot.
- An item where the position is absent (`key?`) contributes nothing. When no item
  contributes, the slot is bound to `[]` if the enclosing list was present, and
  unbound if it was absent, so known-empty and unknown stay distinct.
- `[$N.S]` is shorthand for `[gather($N.S)]`.
- Failures follow collection captures: an item mismatch is an issue at that item's
  path, and a codec gap on any element fails the whole capture. Under
  `tolerant(S)`, the collection slot is marked failed as a whole.
- A collection slot is gathered at one position per shape. Gathering two positions
  into one slot would need an order between them, so it is an error when the shape
  is built.

### Evidence

A capture privately retains, for each slot, the raw source value and its location.
Two consumers may read it:

- `format` receives it as an optional second argument (below), so a codec can keep
  an original spelling when the canonical value is unchanged.
- `evidence(Captures, Slot) → {raw, location, codec?} | null` exposes it to host
  stages, for example to write a value back in the spelling it was read in. `codec`
  names the codec instance that parsed it, absent for a plain capture. For a
  gathered collection, `raw` and `location` are lists, one entry per element.
  Evidence contains source data: it follows the disclosure rules of the journal
  (spec-005).

Which codec parsed a capture is not source data: when a position is a union of
`via` branches (several date patterns, say), it names the format the source used.
Templates read it with `parsed($N.S)`, the name of the codec instance, such as
`"DatePattern(\"YYYYMMDD\")"`, or absent for a plain capture. For a gathered
collection it is the list of names, one per element. Hosts tally these to learn
which formats a source uses, without disclosing values.

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

parse(Source)               → Canonical | lossy(Canonical, Reason) | raises Gap(Reason)
format(Canonical, Original?) → Source | raises Gap(Reason)
```

- `gaps` and `losses` list reason names; each declares the code `<Codec>.<Name>`, as
  table `gaps` do. `Unmapped` and `Unformattable` are always declared.
- `parse` and `format` are pure functions of their arguments: no environment,
  effects, clocks or randomness.
- `Original` is the raw source value the slot was captured from, when the
  canonical value being formatted came from a capture through the same codec. A
  codec may return it when parsing it gives the value being formatted, so unchanged
  values keep their spelling. Codecs that ignore it are correct.
- `lossy(Value, Reason)` is data: the value plus a declared loss reason. The runtime
  unwraps it and reports the loss.
- Output must match the declared shape on its side, and every raised or returned reason
  must be declared; otherwise the codec is a `Defect`.
- A lossy parse reports a `Ledger.Loss` entry, with the slot's qualified name as
  `target`, the occurrence's location as `source` and the position within it as `path`.
- A failed union branch discards its captures and losses; only the matching branch
  reports.
- Within `tolerant(S)`, a codec gap does not fail the match: it is recorded like a
  mismatch (below).

### Parameterised codecs

```text
DatePattern(Pattern: Text) = codec {source: Text, canonical: DateTime where DateOnly,
                                    parse, format, gaps: [Invalid]}

PayableRow = {payableDate: $Dividend.PayableDate via DatePattern("YYYYMMDD"), ...}
```

- A codec may declare literal parameters. `via Codec(Arguments)` instantiates it when
  the shape is built; `parse` and `format` receive the arguments first. Arguments
  are JSON literals, checked against the declared parameter shapes.
- An instance's name is its call, `DatePattern("YYYYMMDD")`; its codes stay those of
  the codec, `DatePattern.Invalid`. Encodings write
  `{"$": "Dividend.PayableDate", "via": {"$": "DatePattern", "arguments": ["YYYYMMDD"]}}`.
- Parameters supply literals only, as for parameterised shapes. A host chooses
  arguments (for instance from a learned format) when it builds the shape, never
  during a match.

### Examples

```text
DatePattern("YYYYMMDD") examples {"20260915" ⇄ "2026-09-15", "2026-02-30" ⇥ Invalid}
```

- Codecs and derivations may declare examples: input, then `⇄` (parses to and formats
  back), `→` (parses or derives to only), `←` (formats from only) or `⇥` (raises the
  gap). Examples are data in the vocabulary and encode as
  `{"input", "output" | "gap", "direction"?}`, with `direction` one of `"both"`
  (`⇄`, the default for codecs), `"forward"` (`→`, the only one for derivations) or
  `"backward"` (`←`); `gap` holds the reason name and implies the direction
  `"forward"` for derivations and parsing, or `"backward"` when stated, for
  formatting. A derivation's `input` is the list of its arguments; each example of a
  parameterised codec also holds the instance's `arguments`.
- A conformance run executes every example in every language binding and fails on
  any difference. A registered transform implemented in two languages agrees because
  both pass the same examples, not because the code is shared.
- Examples are required for code codecs and derivations published in a catalog;
  table codecs are their own examples.

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
- **Enclosing captures.** A capture block may also open the selector or follow an
  intermediate segment:
  `instruments/*/{isin:$Instrument.Isin}/dividends/*` captures each instrument's ISIN
  and enumerates its dividends. Each occurrence carries the captures of its enclosing
  blocks as `{value, location, captures}`, and `project` joins them with its own, by
  the join rule. This is how a nested record reads its parent's fields, without
  ancestor references in selectors. A failed intermediate block is a gap at that
  location and supplies no occurrences below it.
- Locations are JSON Pointers into the value given to the selector, not into the
  response it came from: in the spec-001 example, `/clients/0` points into the data
  `MortgageRetrieve` returns.
- An occurrence may also carry `origin: {strand, seq, pointer}`: the effect whose
  result holds it, identified as in the journal (spec-005), and the pointer within
  that result. A selector sets `origin` when the runtime knows where the selected
  value came from ([spec-005 provenance](spec-005-shape_pipelines_stages.md#provenance));
  otherwise it is absent. `origin` never holds source data, so it may appear in
  ledger entries and outputs.
- `$Origin` is the standard slot bound to an occurrence's `origin`, as `$Location`
  is to its location.
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
references (`$S via Codec`), parsed-codec names (`parsed($S)`), derivations (`Name($A, "literal", Other($B))`, with
declared inputs) and literals. Structs, lists and tuples expand recursively.
`$Location` and `$Origin` are the standard slots bound to an occurrence's pointer
and origin.

| Operation | Signature | Meaning |
| --- | --- | --- |
| `errors` | `(Value, Shape) → Issues` | Diagnostics only |
| `check` | `(Value, Shape, Code) → Value` | The original value, or a `Contract` failure with `Code` |
| `capture` | `(Value, Shape) → Captures` | Match and parse; a `Shape` failure otherwise |
| `expand` | `(Template, Captures) → [Value, Holes]` | A fresh value and the paths of unbound references |
| `reshape` | `(Shape, Template, Value, Captures?) → Value` | `capture`, then `expand`; extra captures follow the join rule |
| `matches` | `(Value, Selector) → [Occurrence]` | Enumerate occurrences |
| `evidence` | `(Captures, Slot) → {raw, location} \| null` | The raw source value a slot was captured from |

- Expansion runs only after a successful match and copies: outputs share no mutable
  structure with inputs or each other. Output is a whitelist of template keys.
- An unbound reference is a **hole**. `expand` leaves its key out and returns its
  path; `project` reports each hole as a gap at that path. `$S?` marks a reference
  whose absence is expected and reports nothing.
- Holes stay local to their missing paths. A list or tuple with missing positions
  becomes an index map retaining the original indexes: `[10, missing, 30]` expands
  to `{0: 10, 2: 30}`, with a hole at `[1]`. JSON uses string index keys. Complete
  lists and tuples remain arrays; missing positions never shift or become null.
  Nested containers remain present. A root reference that cannot expand raises a
  gap because there is no containing position to omit.
- `project` checks when it is built that every template reference, including
  derivation inputs, is captured by its shape or listed in its `outer` option:
  `project(S, T, {outer: [Instrument.Isin]})` expects those slots from enclosing
  capture blocks. An occurrence that does not bind one leaves it unbound, as an
  optional key would, so references to it are holes. A slot captured only in some
  union branches must be referenced as optional.
- Derivations apply transforms: registered, pure functions with a declared
  signature. A failed derivation raises a gap naming its reason; within a template,
  that gap is a hole.

  ```text
  PersonNameCompose = transform {inputs: [Text, Text], output: Text, gaps?: [Empty],
                                 examples?: [...]}
  ```

  `inputs` are parameter shapes, the last of which may be written `[S, ...]` for any
  number of further arguments. `gaps` declares the codes `<Transform>.<Reason>`, as
  for codecs, and an undeclared reason or an output not matching `output` is a
  `Defect`.
- A derivation's arguments are slot references (`$S`, `$S?`), literals or other
  derivations: `TextJoin($Instrument.Isin, "/", DateText($Dividend.ExDate))`. Each
  argument is checked against the derivation's declared parameter shapes when the
  template is built. Nesting keeps every step a registered name, so an expression
  stays data and its slot inputs stay visible to coverage. An optional argument that
  is unbound is passed as absent, and the derivation decides what that means.
- **Leaf failures are holes.** When a template leaf cannot produce a value, because
  its derivation raises a gap or its `via` codec cannot format, `expand` treats it as
  a hole: the key is left out and its path returned with the reason. `project`
  reports it as a gap at that path and returns the rest of the value. The other keys
  are unaffected, so one failed field never loses a record.
- A hole with a reason is still a gap: under a strict template (`project(S, T,
  {holes: Policy.Abort})`) the first hole raises `Failure.Gap` with its reason
  instead, and the item's policy applies. The default is `{holes: Policy.Drop}`:
  drop the key, keep the value. A hole in a key required by the stage's output
  contract fails that contract, as before.
- A derivation with an unbound required input, or a nested derivation that is a
  hole, is a hole, reported once, at the template path.
- **Tolerant positions.** `tolerant(S)` isolates a failure at one position, as holes
  do on the output side. When `S` does not match, or its codec raises a gap, the
  enclosing match continues, nothing under `S` is captured, and each slot `S` would
  have bound is marked failed with the first issue's code or the gap's reason. A
  template reference to a failed slot, plain or `$S?`, is a hole with that reason,
  so the failure is reported where the value would have been used. `tolerant` suits
  records of independent fields read from loosely typed sources; a strict format
  should fail instead. A failed slot is not bound, so it never joins.
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
  or a slot (`$Client.Id`). Slot references are qualified; `$Location` and `$Origin`
  are the only unqualified standard slots.
- An object with a `$`-key is an operator: exactly one `$`-key plus camelCase
  options. `{"$": Ref, ...}` is a reference with options (`via`, `optional`);
  constructors are `$loose`, `$tuple`, `$rest`, `$either`, `$refine`, `$pattern`,
  `$tolerant`, `$gather`, `$parsed` and `$derive`.
- Canonical constructor forms include `{"$rest": "$Any"}`,
  `{"$refine": "$Int", "constraints": ["$Positive"]}` and
  `{"$derive": "$PersonNameCompose", "arguments": ["$Client.GivenNames", "$Client.FamilyName"]}`.
  Patterns encode their source unchanged, e.g.
  `{"$pattern": "{Name}-{Client.Id:IntegerText}"}`. References resolve through the
  registry; decoding never evaluates code or imports arbitrary names.
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
2. Index access (`a[0]`) in selectors or shapes. Selectors exclude indexes on
   purpose; a need for them has not yet outweighed that.
