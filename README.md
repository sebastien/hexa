```
▄▄▄▄▄▄▄▄ ▄▄▄▄▄▄    ▄▄▄▄▄▄▄▄▄▄ ▄▄▄▄▄▄▄▄ ▄▄▄▄▄▄    ▄▄▄▄▄▄▄▄▄
█  ░▓░ ▓      █  ▄▀░▓░      ▓ █  ░▓░ ▓      █  ▄▀░▓░      ▀▄
█   ▒  ▒      █ ▐▌  ▒       ▒ ▐▌  ▒  ▒     ▐▌ ▐▌  ▒        ▐▌
█   ░  ░      █ █   ░ ▄▄▄▄▄▄░  █  ░  ░     █  ▒   ░  █      ▓
█     ▄▓      █ █        █      ▀▄   ▓   ▄▀   ▒             ▒
▓             █ ▓        █     ▄▀         ▀▄  ░░     ░      ░
▒      █▀     ▓ ▓     ▀▀▀▀▀▀▀ ▐▌     █▀    ▐▌ ▒▒     ▒     ░░
▓░     ▓      ▒ ▒░          ▓ ▓░     ▓      ▓ ▒▓░    ▓    ░▒░
▓▓▒░   ▓      █ ▐▓▒░        ▒ ▓▓▒░   ▓      ▒ ▓▓▒░  ░█   ░▒▓▒
██▓▒░  ▒      █  ▀▓▒░       ░ ██▓▒░  ▒      ░ ██▓▒░ ▒█  ░▒▓█▓
▀▀▀▀▀▀▀▀ ▀▀▀▀▀▀    ▀▀▀▀▀▀▀▀▀▀ ▀▀▀▀▀▀▀▀ ▀▀▀▀▀▀ ▀▀▀▀▀▀▀▀  ▀▀▀▀▀
```

*hexa* is a library for writing data pipelines as projections between
shapes. You declare what to match in the source data (a *shape*, the regex of
data structures), what to build from it (a *template*), and compose the two
into stages that select, project, recover and retry, with I/O yielded as
inert effects that the runtime performs, records and replays.

It comes from extraction work: pulling records out of CRM and partner APIs,
normalising their formats, and reporting what did not fit instead of failing
or silently guessing. In our view most of such a program is configuration,
so *hexa* makes the shapes, codecs and pipeline declaration the program, and
hand-written code the exception. It is a specification first: the Python and
JavaScript bindings are experimental implementations, and neither claims
full conformance yet.

## Get started

You need Python 3.14 or later; *hexa* has no dependencies.

```sh
python3 -m pip install -e .
```

The Python distribution is named `hexa-pipelines`; imports use `hexa`.
The JavaScript binding uses ES2022 modules without runtime dependencies.
From a checkout, run `bun run example` or
`node examples/mortgage-people/run.js`; import its public API from
`hexa/shapes.js`, `hexa/combinators.js` and `hexa/runtime.js`.

To test or prepare a release from a checkout, install `uv` and run:

```sh
make test
make test-js       # JavaScript tests; requires Bun.
make test-all      # Both bindings.
make test-release  # Build the source distribution and wheel.
make release       # Test, build, and publish to PyPI.
```

## In a nutshell

```python
import asyncio, json
from hexa.shapes import Text, Int, Positive, either, loose, slots, codec
from hexa.combinators import pipe, select, each, project, collect, Policy
from hexa.runtime import run

# Canonical values, and the one place a source format is converted.
CountryCode = either("AU", "NZ")
CountryName = codec("CountryName", source=Text, canonical=CountryCode,
	map={"AU": "Australia", "NZ": ["New Zealand", "Aotearoa New Zealand"]})

# Slots: named, typed capture points.
Client = slots("Client", Id=Int & Positive, Name=Text, Country=CountryCode)

# What to match in the source (`loose` allows extra keys)...
ClientRow = loose({"id": Client.Id, "name": Client.Name,
	"country": Client.Country @ CountryName})
# ...and the record to fill from the captures.
PersonRecord = {"Name": Client.Name, "Country": Client.Country}

# Select every client, project each row, drop the ones that do not fit.
PeopleExtract = collect(pipe(select("clients/*"),
	each(project(ClientRow, PersonRecord), Policy.Drop)))

data = {"clients": [
	{"id": 7, "name": "Aroha", "country": "Aotearoa New Zealand"},
	{"id": 8, "name": "Mere", "country": "Narnia"},
	{"id": -3, "name": "Sam", "country": "Australia"}]}
print(json.dumps(asyncio.run(run({}, PeopleExtract, data)), indent=1))
```

The result holds one record, `{"Name": "Aroha", "Country": "NZ"}`, and a
ledger with two gaps: `CountryName.Unmapped` at `/clients/1` and
`Issue.Constraint` on `id` at `/clients/2`.

`Policy.Drop` is a *policy*: the declared response when a stage hits a
recoverable failure, such as a row that does not match its shape or a value
that cannot be mapped. `Policy.Abort`, the default for `each`, stops the run,
`Policy.Drop` skips the item and records it in the ledger,
`Policy.Substitute(Value)` uses a fixed value instead, and
`Policy.Fallback(Stage)` runs another stage. For a full program that fetches
its data through effects, with fixtures and a recorded journal, see
[examples/mortgage-people](examples/README.md).

## Features

- *Shapes and slots*: match structures, capture typed values into named
  slots, and get value-free issues for what did not match.
- *Templates and projection*: expand captures into fresh records; a shape and
  a template together are a function from one structure to another.
- *Codecs*: convert between a source format and a canonical value (dates,
  decimals, lookup tables), in both directions.
- *Combinators*: `pipe`, `each`, `choose`, `guard`, `recover`, `retry`,
  `parallel`, `always` and more, building stages from shapes and policies.
- *Effects as data*: stages yield `{operation, arguments}` and the runtime
  performs them, so a run can be journaled, replayed, or tested with `inert`.
- *Ledger*: gaps and losses are reported per row with their reason and
  location, and summarised as coverage.
- *Profiles and requests*: callers send shapes as JSON, checked against what
  the host allows them to reference and how large they may be.

## Design

A program is a set of declarations read in one direction: output template,
slots, codecs, source shapes, then combinators. Hand-written stages are
generators, used only to yield effects or fan in earlier results.

- *Shapes never convert.* Conversion happens only in codecs, so every format
  change has a name and a reverse. The cost is declaring a codec where an
  inline expression would have been shorter.
- *Gaps are reported, never returned.* A missing or unmappable value becomes a
  ledger entry and a policy decides whether the row is dropped, substituted or
  aborts the run. You never get a half-filled record by accident, but you must
  choose a policy for each place data may be absent.
- *Effects are inert.* Because stages describe I/O rather than perform it,
  replay and testing need no mocks. The cost is that effectful code is written
  as generators and cannot call native awaitables directly.
- *Configuration is not data.* Program code references codes and enum members
  as symbols (`Policy.Drop`), and only the runtime turns them into strings in
  outputs. A misspelt member fails at import, not on the first bad row.
- *One vocabulary across languages.* Every binding uses the same canonical
  names and data keys; only syntax differs. This rules out names that clash
  with a builtin in any target language.

Known limits: selectors have no index access, callers cannot send templates,
profiles do not compose, and requests are JSON only. Each is a deliberate
deferral, explained in
[spec-004](docs/specs/spec-004-shape_pipelines_shapes.md#trade-offs-and-decisions).

## Where to go next

- [docs/specs/](docs/specs/): the specification. Start with
  [spec-001](docs/specs/spec-001-shape_pipelines.md) for concepts and naming,
  then spec-004 (shapes) and spec-005 (stages); spec-002 and spec-003 cover
  the JavaScript and Python bindings.
- [docs/skills/shape-pipelines/](docs/skills/shape-pipelines/SKILL.md):
  working rules for writing programs in this style, also usable as an agent
  skill.
- [examples/](examples/README.md): the mortgage-people pipeline in both
  bindings; run it with
   `python3 examples/mortgage-people/people_extract.py` or `bun run example`.
