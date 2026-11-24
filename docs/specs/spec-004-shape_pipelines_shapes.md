# Shape-directed pipelines: shapes and projection

The data side of [spec-001](spec-001-shape_pipelines.md): shapes that match values,
slots that capture them, codecs that convert source formats, selectors that find
occurrences, templates that reproject captures, and the canonical encoding of all of
them, and the profiles and plans that admit caller requests.

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
| `Any` | Every JSON data value |
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
  Literal arguments use the same JSON notation in both bindings: `1.0` is named
  `1`, negative zero is named `0`, and exponent notation uses the same thresholds.
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
  has no recursive descent, indexes ([decided](#trade-offs-and-decisions)) or filters.
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

## Encoding

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
  `"$$ref"` is the literal key `$ref`. A verbatim key is never optional, so an
  optional key cannot begin with `$`: `encode` refuses it rather than write a
  verbatim key that decodes as a different, required one.
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
- `decode(Encoded, Profile?)` rebuilds configuration. Without a profile it trusts its
  input as host configuration, within the library's size limits; caller input is
  always decoded under a profile (below).

## Profiles

A **profile** is the host's statement of what a caller may say: the terms an encoded
shape may reference, the constructors it may use, the request formats it may send,
and the budgets that bound its size. A profile governs the language of caller input;
`env.policy` governs execution: capabilities, calls, retries and concurrency
(spec-005). A caller never sends, names or widens a profile.

```text
AdvisorProfile = profile {
  constructors: [loose, either, refine],
  types:        [Text, Int, DateTime, Null],
  shapes:       [ClientRow],
  slots:        [Client],
  constraints:  [Positive, DateOnly],
  derivations:  [],
  codecs:       [CountryName, DatePattern],
  requests:     {SelectionRequest: {versions: [1], maxSelections: 8, maxRelations: 8,
                                    entitySets: {Clients: [GivenNames, FamilyName, BirthDate],
                                                 Households: []},
                                    relations: [HouseholdHasMemberPerson]}},
  budgets:      {maxDepth: 16, maxNodes: 2000, maxBytes: 65536}}
```

| Element | Holds | Permits |
| --- | --- | --- |
| `constructors` | Constructor names (`loose`, `tuple`, `either`, ...) | Encoded operators of those names |
| `types`, `shapes`, `constraints`, `derivations`, `codecs` | Registered names | References to those names |
| `slots` | Slot or slot namespace names | References to those slots; a namespace permits its own slots |
| `requests` | Request format name → format options, always including `versions` | Requests in that format, under its options |
| `budgets` | `maxDepth`, `maxNodes`, `maxBytes` | Caller input within those bounds |

- A profile is configuration. It is registered by name and declared by the host.
  `env.policy.profile` names the profile applied to caller input.
- A profile encodes as `{"$profile": {name, constructors?, types?, shapes?, slots?,
  constraints?, derivations?, codecs?, requests?, budgets?}}`. Constructors are
  written as their names (`"loose"`), registered terms as references (`"$Client"`),
  `requests` as an object keyed by format name, and `budgets` as numbers. Empty
  elements are omitted. Decoding a registered name requires an equal encoding, as
  for codecs. A caller never sends `$profile`, `$codec` or an effect: under a
  profile, each is refused.
- A profile is an allow-list. Whatever it does not list is refused; there is no
  deny-list and no pattern. An empty profile admits only plain structs, one-item
  lists and literals.
- Two names permit families: a slot namespace permits its own slots but not those of
  nested namespaces, as nesting confers nothing; a codec family permits its
  instances, whose arguments are still checked against the family's parameters.
- Permission governs what the caller writes, not what registered terms contain. A
  permitted shape `$ClientRow` brings its slots, codecs and constraints without
  listing them, so a host exposes a vetted shape by permitting the shape rather than
  its parts.
- Every name a profile lists must be registered, and each format's options are
  checked by that format when the profile is declared. A profile's budgets never
  exceed the library's limits.

### Decoding under a profile

`decode(Encoded, Profile)` admits caller input in three steps, each completing
before the next begins:

1. **Data-only check.** The input must be JSON values, acyclic, free of prototype
   keys, and within the profile's depth, node and byte budgets. An overrun is a
   `Failure.Budget` with code `Profile.Exceeded`, raised before the input is
   traversed further.
2. **Permission.** Every constructor and reference is checked against the profile.
   Refusals are collected, as matching collects issues, into one `Failure.Contract`
   whose issues are `{code: Profile.Forbidden, path}`. A name outside the profile is
   refused with the same code whether or not it is registered, so refusals never
   disclose the host's vocabulary.
3. **Construction.** The decoded terms are built with the same checks as
   programmatically built configuration (slot shapes, codec arguments, gather
   positions, pattern placeholders). A failure is a `Failure.Contract` with its code.

The result is configuration the host could have written itself: it holds only
registered terms and literals, and introduces no term, path, predicate or effect the
host did not register. Decoding performs no effects.

`describe(Profile)` returns the vocabulary a profile permits, as data:
`{constructors, types, shapes, slots, constraints, derivations, codecs, requests}`.
Shapes and slots map names to encoded shapes, derivations to their signatures
`{inputs, output, gaps, examples?}`, codecs to their encoded declarations, and
request formats to their options plus `gaps`. A host's `CatalogDescribe` stage
returns it. A term outside the profile is never described.

## Requests and plans

A caller asks for work by sending a **request**: data in a request format the profile
permits. The host compiles the request into a **plan**: a data value stating what a
run will do. Compiling performs no effects, so a plan can be previewed, stored and
compared before anything runs.

```text
RequestPrepare: RequestValidate → PlanCompile
ExtractionPlan: RequestPrepare
```

A run then executes the plan, as `ExtractionRun` does in
[spec-005](spec-005-shape_pipelines_stages.md#pipeline-declaration).

### Model

| Entity | Identity | Holds | Owner |
| --- | --- | --- | --- |
| Request format | Registered name, plus a version per request | A validator, a compiler, its option shape, its gap codes | Host vocabulary |
| Request | None: a value | `version` and format-defined fields | Caller |
| Plan | None: a value, equal by JSON equality | `request`, `steps`, `allow`, `budgets`, `gaps` | The run that compiled it |
| Step | Index in `steps` | A registered stage or domain step name and literal arguments | Plan |
| Plan gap | `{code, path}` | A declared code; a path of keys into the request | Plan |

- A request format is registered like a codec, by
  `request(Name, versions, options, validate, compile, gaps)`. Its gap codes are
  `<Format>.<Reason>`, as for codecs. `options` is the shape of the format's profile
  options other than `versions`, checked when a profile is declared.
- `validate(Options, Value, Catalog) → Request` and
  `compile(Options, Request, Catalog) → {steps, allow, gaps}` are pure functions.
  `Catalog` is the host's vocabulary entry for the format, `env.vocabulary[Format]`.
  The validator collects every issue into one `Failure.Contract`; the compiler raises
  a gap code only if the format declares it, otherwise it is a `Defect`.
- The combinators `validate(Format)` and `plan(Format)` build the `RequestValidate`
  and `PlanCompile` stages (spec-005). A host names them in its stage namespace.
- A request carries the format's `version`. A missing version is `Issue.Missing`;
  a version the profile does not list is refused with `Profile.Forbidden`.
- A plan holds:
  - `request`: the validated, normalised copy of the request, so a plan describes
    itself;
  - `steps`: the work, in host declaration order, each `{step, arguments?}` naming
    a host step and carrying only literal arguments;
  - `allow`: the operations or capabilities the steps need, in step order;
  - `budgets`: the integer `max…` entries of `env.policy` the run will be bound to;
  - `gaps`: `{code, path}` for each thing requested but impossible to supply, known
    before any effect. The code is written as its qualified name.
- Plans are data. They encode canonically and never contain code, closures or
  handles.

### Operations

| Operation | Input → output | Fails with |
| --- | --- | --- |
| `RequestValidate` | Caller data → validated request | `Failure.Budget` with `Profile.Exceeded` over the profile's budgets; `Failure.Contract` with `Profile.Forbidden` when no profile permits the format, or with issues for a malformed or forbidden request |
| `PlanCompile` | Validated request → plan | `Failure.Contract` with `Profile.Forbidden` when `allow` exceeds `env.policy.allow`; `Failure.Defect` for an undeclared gap code or a catalog that names an unknown step |
| Running a plan | Plan → output and ledger | As its steps |

### Constraints

- **Refusal and gaps are distinct.** What is malformed or outside the profile or
  policy is refused, and nothing runs. What is permitted but cannot be supplied
  (no correspondence, no admitted supplier) is a plan gap: the plan still runs and
  supplies the rest.
- **A request only chooses.** It selects among registered alternatives and supplies
  literals. It never adds a step, binding, selector, path or operation, and step
  order comes from the host's declarations, never from the request.
- **Compilation is deterministic.** An equal request, vocabulary, profile and policy
  compile to an equal plan.
- **A plan runs as compiled.** A run executes exactly the plan's steps under its
  `allow` and `budgets`, and does not reread the request to add work. It reports
  each plan gap once to the ledger, so coverage reports it as `Coverage.Unmapped` or
  `Coverage.NotSupplied`.
- **A plan is bound to its environment.** It runs only where vocabulary, profile and
  policy equal those it was compiled under; elsewhere it is recompiled from its
  `request`, and the stored plan is not trusted.

### Selection requests

`SelectionRequest` is the first request format. A caller selects
entities by type and asks for relations between the selections.

```json
{"version": 1,
 "selections": {"People": {"from": "Clients", "attributes": ["GivenNames", "BirthDate"]},
                "Household": {"from": "Households", "attributes": []}},
 "relations": [{"type": "HouseholdHasMemberPerson",
                "participants": {"source": "Household", "target": "People"}}]}
```

- An **entity set** is a host-registered, named set of entities of one type
  (`Clients`), with the attributes it can supply and the discovery it requires. The
  term avoids `selector`, which names paths here.
- A **selection** binds a caller **alias** to an entity set and a list of attributes.
  Aliases are PascalCase, at most 64 characters, and name outputs only: they never
  decide which entities are members.
- A **relation request** names a registered relation type and binds each of its roles
  to one alias or a list of aliases.

| Element | Cardinality | Constraint |
| --- | --- | --- |
| Request → selections | 1 to `maxSelections` | Aliases unique |
| Selection → entity set | Exactly one | Permitted by the profile's `entitySets` |
| Selection → attributes | 0 or more, distinct | Each supplied by the entity set and permitted for it; an empty list selects entities only, for example as relation participants |
| Request → relations | 0 to `maxRelations` | Each relation type at most once, permitted by `relations` |
| Relation → roles | Exactly the relation type's roles | Each role binds at least one alias |
| Role → aliases | 1 or more | Each alias's entity set has the role's participant type |

Validation refuses, as `Failure.Contract`, any unknown key, unsupported version,
forbidden or unknown entity set, attribute or relation type, and any violated
cardinality above.

Compilation adds, for each selection, the discovery its entity set requires and a
supplier for each attribute; for each relation, a supplier of its links. When none
can be added it records a plan gap at the request path:

| Code | Condition | Coverage |
| --- | --- | --- |
| `SelectionRequest.Unmapped` | No correspondence supplies the attribute or relation | `Unmapped` |
| `SelectionRequest.Unresolved` | Correspondences exist, but none is reviewed and implemented | `Unmapped` |
| `SelectionRequest.NotSupplied` | A correspondence exists, but no admitted step supplies its source | `NotSupplied` |
| `SelectionRequest.ReferenceUnselected` | A reference attribute's target type has no selection in the request | `NotSupplied` |

The catalog, `env.vocabulary.SelectionRequest`, is host data:

```json
{"steps": {"MortgageRetrieve": {"capability": "mortgage_read"},
           "ClientsRetrieve": {"capability": "client_read", "requires": ["MortgageRetrieve"]}},
 "entitySets": {"Clients": {"type": "Person", "requires": ["MortgageRetrieve"],
                            "attributes": {"GivenNames": {"correspondences": [{"supplier": "ClientsRetrieve", "resolved": true}]},
                                           "Household": {"correspondences": [], "reference": "Household"}}}},
 "relations": {"HouseholdHasMemberPerson": {"roles": {"source": "Household", "target": "Person"},
                                            "correspondences": []}}}
```

- `steps` is ordered: its order is the plan's step order. Adding a step adds the
  steps it `requires`, transitively.
- A catalog attribute may carry `via: ["Link", ...]`, an ordered list of
  catalog relation entries needed to reach that attribute's population.
  Selecting the attribute implicitly plans their suppliers. At the first
  unsupplied link, the gap's path extends the requested attribute path with
  `["via", index]`; later links and the leaf are not scheduled for that
  selection. Shared steps still run once. The caller cannot override `via`;
  an unknown catalog link is a host defect. Materialization checks concrete
  instance joins; this planning step establishes only potential suppliers.
- A correspondence is resolved when it has been reviewed and implemented. Its
  `supplier` is admitted when it names a catalog step. Every admitted supplier of a
  resolved correspondence is added.
- An attribute or relation records at most one gap: the first of `Unmapped`,
  `Unresolved` and `NotSupplied` that applies. Otherwise, it records
  `ReferenceUnselected` when the attribute references a type no selection has.

The run's output is keyed by alias, and relation links name participants only by
alias. Record shapes and materialization belong to the domain, not to this
specification.

## Trade-offs and decisions

- **No index access.** Selectors and shapes have no index form (`a[0]`). A position
  in a source list rarely carries meaning a slot cannot state, and a positional pick
  hides the other items. A fixed position is matched with `Tuple` and `rest`
  (`Tuple($Client.Primary, rest(Any))`); plural data is enumerated with `*` and joined
  on a shared slot; `$Location` keeps the index of each occurrence. Revisit only for
  a concrete pipeline that none of these serve.
- **No caller-sent templates.** Callers choose among host templates through request
  formats and send shapes only. General caller templates are deferred until a
  pipeline needs them; they would need a profile element for template constructors
  and derivations.
- **Flat profiles.** Profiles do not inherit or compose. Hosts with many similar
  profiles may build them programmatically; composition is open if that proves
  insufficient.
- **JSON requests only.** A domain may offer a bounded text syntax for authoring
  requests, but it must parse into the JSON request and pass
  the same validation. Agents send JSON.
