# Pipeline style

A programming style built on two ideas: inputs and outputs are declared as data
templates to extract and reproject, and operations are combinators along a
declared pipeline. The focus is on the shape of the data and on projections.

| Document | Status |
| --- | --- |
| [spec-001](docs/specs/spec-001-shape_pipelines.md) | Overview: concepts, naming, data and configuration, enums and codes, language binding rules, example. Draft |
| [spec-004](docs/specs/spec-004-shape_pipelines_shapes.md) | Shapes, slots, codecs, selectors, templates, encoding. Draft; open items at the end |
| [spec-005](docs/specs/spec-005-shape_pipelines_stages.md) | Stages, combinators, effects, failures and policies, cancellation, replay, environment, testing. Draft |
| [spec-002](docs/specs/spec-002-shape_pipelines_javascript.md) | JavaScript language binding. Draft; no implementation yet |
| [spec-003](docs/specs/spec-003-shape_pipelines_python.md) | Python language binding. Experimental implementation in `hexa/` |
| [examples](examples/README.md) | One pipeline in both language bindings with shared fixtures. Python runs against live fixture responses and journal replay |
| [skill](docs/skills/shape-pipelines/SKILL.md) | Agent skill: workflow, rules, language binding cheat-sheet, testing, review checklist. Draft |

## Python

Requires Python 3.14+; no runtime dependencies. Import from `hexa.shapes`,
`hexa.combinators` and `hexa.runtime`.

```python
from hexa.shapes import Int, Text, Tuple, __, capture, expand, slots

Person = slots("Person", Name=Text)
captures = capture({"name": "Ada"}, {"name": Person.Name})
value, holes = expand({"Name": Person.Name}, captures)
# value == {"Name": "Ada"}; holes == []

Row = Tuple(Int, __, Text)  # Fixed prefix/suffix, arbitrary intervening positions.
```

Run the standard-library tests from the repository root:

```sh
python3 -m unittest discover -v
```

The implementation includes shapes, codecs, templates, holey index maps,
selectors, stage contracts, policies, inert effects, structured concurrency,
recording, replay and provenance. The shared mortgage fixtures are unchanged.

Race journals include explicit decisions keyed by `(strand, ordinal)`, separate
from effect positions `(strand, seq)`. Replay preserves the recorded selection and
losing participants' recorded work, even when both alternatives succeeded. Legacy
journals without races remain compatible; race journals without decision entries
fail with `Runtime.ReplayMismatch`.

### Draft limitations

- Domain profile formats and selections/relations compilation remain deferred.
- Selectors do not support indexes or recursive descent.
- This is an experimental implementation, not a declaration of full draft-spec
  conformance. The initial suite was run on Python 3.15; Python 3.14 should also be
  exercised before release.

hexa is independent of any domain. Domain languages build on it without changing
it: onto compiles its document bindings to hexa programs
through an optional integration (onto spec-06). In hexa, "binding" always means a
language binding (JavaScript, Python); onto's bindings are document-to-ontology
correspondences.
