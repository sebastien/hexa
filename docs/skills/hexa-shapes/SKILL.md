---
name: hexa-shapes
description: Define data shapes with Hexa, in JavaScript or Python. Shapes describe source and caller data, slots capture canonical values, codecs convert source formats, templates build outputs, codes name failure reasons, and profiles bound what callers may send. Use to design, review or refactor shapes, slots, codecs, templates, selectors, codes and profiles, and their fixtures and encodings. For stages and pipelines built on them, use hexa-pipelines.
---

# Hexa shapes

Shapes are the data half of Hexa: they say what data looks like, where its values
go and how source formats convert. The specs in `docs/specs/` decide disputes.

| Need | Read |
| --- | --- |
| Concepts, naming, data vs configuration, enums and codes | [spec-001](../../specs/spec-001-shape_pipelines.md) |
| Shapes, slots, codecs, selectors, templates, profiles, encoding | [spec-004](../../specs/spec-004-shape_pipelines_shapes.md) |
| JavaScript syntax | [spec-002](../../specs/spec-002-shape_pipelines_javascript.md) |
| Python 3.14+ syntax | [spec-003](../../specs/spec-003-shape_pipelines_python.md) |
| Shapes in a complete program | [examples/mortgage-people](../../../examples/README.md) |

Both bindings are implemented: `hexa/shapes.js` (tests: `make test-js`) and
`hexa.shapes` (tests: `make test-py`). Report which binding and tests were
actually exercised; do not claim full draft-spec conformance from them.

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
6. **Fixtures.** One synthetic example per union branch, codec row and gap reason,
   plus a strictness failure; canonical encodings of public shapes.

Prefer a template or shape, then a registered codec, derivation or transform. Never
widen the shape language to avoid a codec.

## Rules

### Configuration vs data

- Program code references members and codes as symbols: `Mortgage.RootMismatch`,
  `Failure.Gap`. **Never** write a kind or code as a string literal, and never
  compare a data string against one.
- Strings in program code are only declarations (`slots("Client", …)`,
  `codes("Mortgage", …)`), small languages (selectors, patterns) and effect
  arguments.
- Data (outputs, ledger, fixtures, encodings) holds names: `"reason":
  "Issue.Missing"`. Only the runtime and `encode` convert.

### Naming

- PascalCase for everything a program names: shapes, slots, codecs, codes.
  camelCase for every key the spec defines. External keys keep their source spelling.
- Shapes are `Subject Role` (`ClientRow`, `PersonRecord`); codecs `Canonical
  SourceFormat` (`CountryName`, `InstantOffset`).

### Shapes and slots

- Shapes never convert: `"12"` does not match `Num`. Integral numbers are `Int` in
  both bindings; amounts are `Decimal` via `DecimalText`, never `Float`.
- Structs are strict unless `loose`. `key?` admits absence; `S | Null` admits null;
  neither implies the other.
- Tuples only via `Tuple(...)`; a list shape has exactly one item shape.
- A repeated slot is a join on canonical values; a mismatch is `Issue.CaptureConflict`.
  Join plural selections on a shared slot, never by position.
- `tolerant(S)` only for loosely typed sources of independent fields: a value that
  does not match or parse becomes a hole for the template keys that use it. Never
  wrap a whole record in it.
- `$Location` binds the occurrence's JSON Pointer; issue and ledger `path`s are key
  lists relative to it. `$Origin` binds the effect it came from, when known.

### Templates and selectors

- `$S?` (`optional`, Python `~`) marks an expected absence. A plain reference that
  is unbound is a hole, reported as a gap. A slot captured only in some union
  branches must be referenced as optional.
- A failed derivation or format is also a hole: the key is dropped, the rest of the
  value kept. Use `{holes: Policy.Abort}` when a record is wrong without every field.
- Collect a field from every list item with `gather($N.S)` into a collection slot;
  `[$N.S]` is the same for a list of scalars.
- Nested records read parent fields through a capture block in the selector
  (`items/*/{id?:$Order.Id}/lines/*`) and `project(…, {outer: [Order.Id]})`, never
  by passing the parent around.
- Match-only shapes (no slots, no `via`) are for routing with `choose`.

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

### Caller data and profiles

- Caller-sent shapes go through `decode(value, Profile)`; caller requests through
  `validate(Format)` then `plan(Format)`. Unpermitted terms are `Profile.Forbidden`;
  unsuppliable ones are plan gaps.
- Public failure and ledger fields hold locations and codes, never source values.

## Binding cheat-sheet

| Notation | JavaScript | Python |
| --- | --- | --- |
| `{k: S, k?: S}` | `{ k: S, "k?": S }` | `{"k": S, "k?": S}` |
| `{k: S, ...}` | `loose({ k: S })` | `loose({"k": S})` |
| `A \| B` | `either(A, B)` | `either(A, B)` (never `\|`) |
| `S where C` | `refine(S, C)` | `S & C` |
| `$N.S via Codec` | `via(N.S, Codec)` | `N.S @ Codec` |
| `Name($A, "x")` (template) | `derive(Name, A, "x")` | `derive(Name, A, "x")` |
| `$N.S?` (template) | `optional(N.S)` | `~N.S` |
| Null | `null` | `None` |

- Import canonical names unchanged. Python: do not import `typing.Any`/`List` next to
  the library's `Any`/`List`; allow PascalCase in linters (ruff `N802`, `N803`, `N806`).

```js
import { Int, Text, Positive, loose, refine, slots, optional, via, Location } from "hexa/shapes.js";

const Client = slots("Client", { Id: refine(Int, Positive), GivenNames: Text, Country: CountryCode });
const ClientRow = loose({ id: Client.Id, firstName: Client.GivenNames,
	"country?": via(Client.Country, CountryName) });
const PersonRecord = { GivenNames: Client.GivenNames, Country: optional(Client.Country), Source: Location };
```

```python
from hexa.shapes import Int, Text, Positive, loose, slots, Location

Client = slots("Client", Id=Int & Positive, GivenNames=Text, Country=CountryCode)
ClientRow = loose({"id": Client.Id, "firstName": Client.GivenNames,
    "country?": Client.Country @ CountryName})
PersonRecord = {"GivenNames": Client.GivenNames, "Country": ~Client.Country, "Source": Location}
```

## Testing

- Keep fixtures as data, including canonical encodings of shapes; they are shared by
  both bindings.
- Assert on `kind`, `code` and `path` as members, never on message text.

## Review checklist

- [ ] No kind or code written as a string; no data string compared to a literal.
- [ ] Every conversion goes through a codec; no `Date.parse`, `Number(…)`,
      `fromisoformat` or `Decimal(…)` outside codecs.
- [ ] Struct strictness is deliberate; `key?` and `| Null` used for what they mean.
- [ ] Optional template references only where absence is expected; other holes report.
- [ ] `tolerant` only around single fields, and `holes` policy chosen deliberately.
- [ ] Table codecs have no default/`Other` mapping; reasons are declared.
- [ ] Names follow `Subject Role` for shapes, `Canonical SourceFormat` for codecs.

## Known open points

Do not invent answers for these; flag them:

- Index access is excluded by decision (spec-004); match fixed positions with
  `Tuple` and `rest`.
- A stored plan is recompiled, never trusted, when its environment may differ.
- Encodings cover registered names only, never closures, handles or signals.
- `SlotRedeclared` needs stable shape equality, not object identity.
