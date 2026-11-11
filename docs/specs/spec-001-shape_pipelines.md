# Shape-directed pipelines

A programming style in which inputs and outputs are declared as data templates,
and programs are pipelines of projections between them. A shape says what to
extract and which slots capture it; a template says how to reproject those
captures. Slots carry canonical types, and codecs convert each source format to
and from them. Operations are combinators: functions building stages from shapes,
templates, policies and other stages. Effects are data, so a run is inspectable,
recordable and replayable. Hand-written code covers only what no template,
codec or combinator expresses, and stays compact and declarative.

These specifications are self-contained: selectors, captures and codec tables are
defined in [spec-004](spec-004-shape_pipelines_shapes.md), and naming in
[Naming](#naming).

| Spec | Covers |
| --- | --- |
| spec-001 (this) | Concepts, naming, data and configuration, enums and codes, language binding rules, example |
| [spec-004](spec-004-shape_pipelines_shapes.md) | Shapes, slots, codecs, selectors, templates and projection, encoding and profiles |
| [spec-005](spec-005-shape_pipelines_stages.md) | Stages, combinators, effects and concurrency, failures and policies, tracing, environment, testing |
| [spec-002](spec-002-shape_pipelines_javascript.md), [spec-003](spec-003-shape_pipelines_python.md) | JavaScript and Python language bindings |

spec-001, spec-004 and spec-005 are language-agnostic and together define the
canonical names; the language bindings add syntax only.

## Concepts and approach

- **Shape:** a description of acceptable values. Matching yields value-free issues
  and captures. Shapes never convert.
- **Slot:** a named, typed capture point, declared once in a slot namespace with its
  canonical shape. Repeating a slot in one shape is a join.
- **Codec:** a named pair `Parse`/`Format` between a source format and a slot's
  canonical shape, attached to a position with `via`. The only place conversion
  happens.
- **Template:** a structure of literals, slot references and derivations that
  expands into a fresh value. A shape whose leaves are slots and literals is also a
  template.
- **Projection:** a shape and a template read as one function: extract, normalise,
  reproject. It need not be lossless or total. Domain languages built on this style
  (for example onto's bindings and mappings) give projection narrower meanings and
  compile to these projections.
- **Origin:** where an occurrence's data came from: an effect's journal position and
  a pointer within its result. Value-free, so outputs may carry it.
- **Stage:** a generator `Name(env, input, ...) → output` whose contract is a pair of
  shapes.
- **Combinator:** a function returning a stage: `pipe`, `each`, `project`, `recover`,
  `always`, etc. Combinators are stateless and defined once, at module level.
- **Pipeline:** an entry-point stage built from stages and combinators, with a
  declared order of work.
- **Effect:** an inert description `{operation, arguments}` yielded to the runtime,
  which performs it against host operations.
- **Environment:** host-owned authority, vocabulary, policy and operations.
- **Failure:** a classified interruption whose kind is a `Failure` member:
  `Contract`, `Budget`, `Cancel`, `Defect`, `Shape`, `Gap` or `Effect`. The last
  three are recoverable.
- **Code:** a symbol naming a failure, gap or loss reason, declared in a code
  namespace: `Mortgage.RootMismatch`, `CountryName.Unmapped`.
- **Policy:** a declared response to recoverable failures: `Policy.Abort`,
  `Policy.Drop`, `Policy.Substitute(Value)` or `Policy.Fallback(Stage)`.
- **Gap:** an expected absence: unsupplied, inadmissible, unmappable or unparseable
  data. A gap is reported, never returned as a value. A gap in one template field is
  a hole: the field is left out and the rest of the value kept.
- **Ledger:** the run-local record of gaps and losses; summarised as coverage.
- **Journal:** the optional record of performed effects and their results; valid
  replay input.
- **Vocabulary:** host-registered types, constraints, derivations, transforms and
  codecs, referenced by name. The extension point for hard-to-express transforms.

Read a program through its shapes, slots, combinators and pipeline declaration,
not its control flow. Write the output template first, then the slots and source
shapes, then compose combinators. Add a registered transform before widening the
shape language; write a stage by hand only when no combinator fits.

## Naming

Names read from the most general term to the most specific, with the verb or
qualifier last, so related names sort together.

| Named thing | Pattern | Examples |
| --- | --- | --- |
| Stages, combinator results, transforms | Namespace? Subject Object Verb | `MortgageClientsSelect`, `BridgeHouseholdMembersDiscover`, `PersonNameCompose` |
| Environment operations | Subject Verb | `RequestSend`, `DelayWait`, `FailureClassify`, `ProgressPublish` |
| Shapes | Subject Role | `ClientRow`, `MortgageResponse`, `PersonRecord` |
| Slot namespaces, slots | Subject, then Attribute | `Client.GivenNames`, `Client.Id` |
| Codecs | Canonical SourceFormat | `CountryName`, `CountryIso3`, `InstantOffset`, `DecimalText` |
| Code namespaces, codes | Subject, then Condition | `Mortgage.RootMismatch`, `CountryName.Unmapped` |
| Library enums | Concept, then Member | `Failure.Gap`, `Policy.Drop`, `Issue.Missing` |
| Constraints | Qualifier | `Positive`, `Nonempty`, `Zoned` |

- Everything a program names is PascalCase: slots, stages, shapes, codecs,
  operations, codes and enum members (`Failure.Gap`, `Policy.Drop`).
- Keys of data structures the spec defines are camelCase in every language binding: policies,
  ledger entries, failures, effects, options and the environment (`kind`, `code`,
  `maxGaps`, `env.root`).
- The namespace prefix is included when a name must be unique beyond its module.
  The verb is never first, and comes from the verb table in
  [spec-005](spec-005-shape_pipelines_stages.md#stages).
- Library functions are generic lowercase words (`pipe`, `refine`, `via`, `p`). They have
  no subject and read as operators on PascalCase names.
- Keys of external data keep their source spelling: in `{firstName: Client.GivenNames}`,
  `firstName` belongs to the source API.

## Data and configuration

A program is configuration: shapes, slots, codecs, stages, policies, failure kinds and
codes. What crosses a boundary is data: caller input, effect arguments and results,
outputs, the ledger, the journal and public failure fields. The two never share a
representation.

| | Configuration | Data |
| --- | --- | --- |
| Lives in | Source code and host setup | Requests, responses, outputs, fixtures, storage |
| Form | Identifiers and enum members, imported or declared | JSON values with camelCase keys |
| Names | Symbols: `Failure.Gap`, `Policy.Drop`, `Mortgage.RootMismatch` | Strings: `"Gap"`, `"Mortgage.RootMismatch"` |

- Program code never writes a category value or code as a string literal; it
  references a member. A misspelt member fails when the module loads, not when a
  policy first runs.
- Strings in program code are declarations (`slots("Client", …)`,
  `codes("Mortgage", "RootMismatch")`), small languages (selectors,
  patterns) or data sent to the host (effect arguments).
- Only the runtime and the encoder convert. In run data, a field holding members of
  one fixed enum holds the member name (`kind: "Gap"`); a field holding codes or
  program entities holds the qualified or declared name (`reason: "Issue.Missing"`,
  `target: "MortgageClientsProject"`). In encoded programs, members are references
  (`"$Policy.Drop"`), and `decode` maps them back through the registry.

### Library enums

| Namespace | Members |
| --- | --- |
| `Failure` | `Contract`, `Budget`, `Cancel`, `Defect`, `Shape`, `Gap`, `Effect` |
| `Policy` | `Abort`, `Drop`; constructors `Substitute(Value)`, `Fallback(Stage)` |
| `Ledger` | `Gap`, `Loss` |
| `Issue` | `Mismatch`, `Missing`, `Unexpected`, `Constraint`, `CaptureConflict` |
| `Coverage` | `Supplied`, `SourceNull`, `NotSupplied`, `Unmapped`, `Conflicting` |
| `Checks` | `Boundary`, `All`, `Off` |
| `Runtime` | `CloseYielded`, `MemberUnknown`, `NativeAwaitable`, `PathSegmentInvalid`, `ReplayMismatch`, `SlotRedeclared`, `SlotUnknown`, `StageRenamed`, `StrandUnjoined`, `StrandConflict` |

### Codes

```text
Mortgage = codes {RootMismatch}
```

- `codes(Namespace, ...Names)` declares program codes. Members are interned by
  qualified name, like slots; an unknown member is an error when it is referenced.
- `Issue` and `Runtime` are the library's code namespaces. A codec is the namespace
  of its own codes: `CountryName.Unmapped`, `CountryName.Unformattable` and the
  reasons its `gaps` declare. `InstantOffset.Discarded` is a loss reason.

## Language bindings

A language binding implements this specification in one programming language
(spec-002, spec-003), without renaming it. A program reads the same in every
language binding apart from syntax. Below, "binding" always means a language binding.

| Group | Canonical names |
| --- | --- |
| Types | `Any`, `Text`, `Num`, `Int`, `Float`, `Bool`, `Null`, `Struct`, `List`, `DateTime`, `Instant`, `Decimal` |
| Shape constructors | `Tuple`, `rest`, `loose`, `either`, `refine`, `where`, `pattern`, `nullable`, `tolerant`, `gather` |
| Slots, codecs and codes | `slots`, `slot`, `via`, `optional`, `derive`, `codec`, `transform`, `lossy`, `codes`, `Location`, `Origin` |
| Standard vocabulary | `DateOnly`, `Timed`, `Zoned`, `Positive`, `Nonempty`, `parts`, `InstantOffset`, `DecimalText`, `DatePattern` |
| Operations | `errors`, `check`, `capture`, `expand`, `reshape`, `matches`, `evidence`, `parsed`, `encode`, `decode` |
| Combinators | `stages`, `stage`, `pipe`, `lift`, `project`, `guard`, `select`, `each`, `parallel`, `choose`, `when`, `otherwise`, `admit`, `fold`, `tap`, `bound`, `recover`, `retry`, `always`, `collect` |
| Policy helpers | `backoff` |
| Runtime | `run`, `report`, `ledger`, `join`, `race`, `fork`, `inert`, `p` |
| Enums | `Failure`, `Policy`, `Ledger`, `Issue`, `Coverage`, `Checks`, `Runtime` |
| Failures | `Failure` (also the kind enum), `Gap` |

- Every canonical name is spelled identically in every binding: no case variants,
  trailing underscores or prefixes.
- No canonical name is a keyword, reserved word, builtin or common global in any
  bound language (e.g. `open`, `all`, `any`, `type`, `list`, `map`, `set`, `from`,
  `finally`, `Number`, `Boolean`, `Object`, `Date`). A conflict found by a new binding
  renames the canonical name here, for every binding.
- Bindings differ only in syntax: generator declaration, delegation, anonymous
  functions, the null literal, path templates, operator forms and the native
  representation of symbols. Data keys, codes and enum members are identical.
- A binding may accept compatibility aliases, but its documentation, examples and
  generated code use canonical names. Operators a binding defines as syntax for
  canonical functions (Python's `&`, `@`, `~`) may appear in its examples; encodings
  and traces always use the canonical names.
- A binding specifies the shape mapping, type semantics, slot namespaces, stage and
  effect syntax, failure classes, cancellation, the runtime, module layout and the
  example below.

## Example

Project a mortgage root's clients into person records, normalising country names and
dropping malformed rows.

```text
Client = slots {Id: Int where Positive, GivenNames: Text, FamilyName: Text,
                BirthDate: DateTime where DateOnly | Null, Country: CountryCode}

Mortgage = codes {RootMismatch}

RootBound(Root) = {application: {id: Root.mortgage, ...}, clients: [Any], ...}
ClientRow = {id: $Client.Id,
             basicDetails: {firstName: $Client.GivenNames, lastName: $Client.FamilyName,
                            dateOfBirth?: $Client.BirthDate,
                            countryOfBirth?: $Client.Country via CountryName, ...}, ...}
PersonRecord = {GivenNames: $Client.GivenNames, FamilyName: $Client.FamilyName,
                BirthDate: $Client.BirthDate?, Country: $Client.Country?, Source: $Location}

People = stages {
  MortgageRetrieve(env) → Data:
    {Application, Clients} ← yield join({Application: env.RequestSend({method: GET, path: p"/mortgages/{root.mortgage}"}),
                                         Clients: env.RequestSend({method: GET, path: p"/mortgages/{root.mortgage}/clients"})})
    Data = {application: Application.data, clients: Clients.data}
  MortgageValidate       = guard(RootBound(env.root), Mortgage.RootMismatch)
  MortgageClientsSelect  = select(clients/*)
  MortgageClientsProject = each(project(ClientRow, PersonRecord), Policy.Drop)
  PeopleExtract          = collect(pipe(MortgageRetrieve, MortgageValidate,
                                        MortgageClientsSelect, MortgageClientsProject))
}

PeopleExtract: MortgageRetrieve → MortgageValidate → MortgageClientsSelect → MortgageClientsProject
```

| Stage | Input → output | Policy |
| --- | --- | --- |
| `MortgageRetrieve` | Root → `{application, clients}` | Two requests joined; host retry defaults; failures abort |
| `MortgageValidate` | Data → `RootBound` data | `Failure.Contract`: `Mortgage.RootMismatch` |
| `MortgageClientsSelect` | Data → `[Occurrence]` | — (`MortgageValidate` guarantees `clients`) |
| `MortgageClientsProject` | `[Occurrence]` → `[PersonRecord]` | Shape or gap per row: `Policy.Drop` |
| `PeopleExtract` | () → `{value: [PersonRecord], ledger}` | — |

Each stage is named once, by its declaration in the `People` namespace; the host runs
`People.PeopleExtract`. Only `MortgageRetrieve` is hand-written, because it yields
effects. A row
without `firstName` is dropped and reported against `MortgageClientsProject`; a row
without `countryOfBirth` keeps the row and omits `Country`, with no report; an
unmapped country name reports `CountryName.Unmapped` and drops the row. The root
check aborts because the root binds authority.
[`examples/mortgage-people`](examples/README.md) holds both language bindings of this example
with shared fixtures.
