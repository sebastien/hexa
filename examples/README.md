# Examples

Programs following
[spec-002](../docs/specs/spec-002-shape_pipelines_javascript.md) (JavaScript) and
[spec-003](../docs/specs/spec-003-shape_pipelines_python.md) (Python). The Python
example imports the implemented `hexa` package and runs on Python 3.14+.
JavaScript remains a syntax-only example until its binding exists.

From the repository root, install the Python package in editable mode with
`python3 -m pip install -e .`, then run `python3 -m unittest tests.test_conformance -v` to
check live fixture responses, journal replay, inert effects, provenance and shape
encodings. The shared JSON fixtures are unchanged.

Run `python3 examples/mortgage-people/people_extract.py` to print the fixture-backed
pipeline result (person records and ledger) as JSON.

## Mortgage people

[`mortgage-people/`](mortgage-people/) projects a mortgage's clients into person
records. It is the spec-001 example, with the application and the client list
retrieved concurrently.

```text
PeopleExtract: MortgageRetrieve → MortgageValidate → MortgageClientsSelect → MortgageClientsProject
```

| Stage | Input → output | Policy |
| --- | --- | --- |
| `MortgageRetrieve` | Root → `{application, clients}` | Two requests joined; host retry defaults; failures abort |
| `MortgageValidate` | Data → `RootBound` data | `Failure.Contract`: `Mortgage.RootMismatch` |
| `MortgageClientsSelect` | Data → `[Occurrence]` | — (`MortgageValidate` guarantees `clients`) |
| `MortgageClientsProject` | `[Occurrence]` → `[PersonRecord]` | Shape or gap per row: `Policy.Drop` |
| `PeopleExtract` | () → `{value: [PersonRecord], ledger}` | — |

| File | Meaning |
| --- | --- |
| `people_extract.js`, `people_extract.py` | The same program in each binding, declared as the `People` stage namespace (`stages(S => ({ … }))` in JavaScript, `class People(stages)` in Python), with an inlined `CountryCode` type and `CountryName` table. Python uses its `&`, `@` and `~` operators and keyword options; Python 3.14+ for `p(t"...")` |
| `fixtures/responses.json` | Host transport fake: request `path` → operation result `{data}`. Not a journal |
| `fixtures/expected.json` | `{value, ledger}` that `run(env, People.PeopleExtract)` must return in both bindings |
| `fixtures/journal.json` | A recorded run: replaying it with `env.replay` must also return `expected.json`, without `RequestSend` |
| `fixtures/shapes.json` | `ClientRow` and `PersonRecord` in the canonical encoding; the name → encoding map is example-local |

The programs are configuration and reference members (`Policy.Drop`,
`Mortgage.RootMismatch`); the fixtures are data and hold their names. In
`expected.json`, `kind` is the member name `"Gap"`, `reason` a qualified code such as
`"Issue.Missing"`, and `target` the stage that dropped the row, which defaults from
`Policy.Drop` inside `MortgageClientsProject`.

Each client row exercises one rule:

| Occurrence | Data | Outcome |
| --- | --- | --- |
| `/clients/0` | Country `Aotearoa New Zealand` | Record with `Country: NZ` through a parse-only source |
| `/clients/1` | No `firstName` | Dropped; `Issue.Missing` at `[basicDetails, firstName]` |
| `/clients/2` | `dateOfBirth: null`, no country, extra `email` | Record with `BirthDate: null`, no `Country`, no report; `email` not copied |
| `/clients/3` | Country `Narnia` | Dropped; `CountryName.Unmapped` |
| `/clients/4` | `id: -3` | Dropped; `Issue.Constraint` (`Positive`) at `[id]` |

In `journal.json` the two requests ran in the `join`'s child strands `/Application`
and `/Clients`, each as its strand's effect `seq` 0. The clients request happened to
complete first (`index` 0); replay matches by `(strand, seq)`, so completion order
does not matter ([spec-005](../spec-005-shape_pipelines_stages.md), Strands and replay
matching).

Driving the effectful stage by hand with `inert` checks what it yields without a
runtime:

```js
const env = inert({ root: { mortgage: 42 } });
const it = People.MortgageRetrieve(env);
assert.deepEqual(it.next().value, join({
	Application: env.RequestSend({ method: "GET", path: "/mortgages/42" }),
	Clients: env.RequestSend({ method: "GET", path: "/mortgages/42/clients" }),
}));
```

```python
env = inert({"root": {"mortgage": 42}})
it = People.MortgageRetrieve(env)
assert next(it) == join({
    "Application": env.RequestSend({"method": "GET", "path": "/mortgages/42"}),
    "Clients": env.RequestSend({"method": "GET", "path": "/mortgages/42/clients"}),
})
```
