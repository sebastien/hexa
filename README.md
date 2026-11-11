# Pipeline style

A programming style built on two ideas: inputs and outputs are declared as data
templates to extract and reproject, and operations are combinators along a
declared pipeline. The focus is on the shape of the data and on projections.

| Document | Status |
| --- | --- |
| [spec-001](spec-001-shape_pipelines.md) | Overview: concepts, naming, data and configuration, enums and codes, binding rules, example. Draft |
| [spec-004](spec-004-shape_pipelines_shapes.md) | Shapes, slots, codecs, selectors, templates, encoding. Draft; open items at the end |
| [spec-005](spec-005-shape_pipelines_stages.md) | Stages, combinators, effects, failures and policies, cancellation, replay, environment, testing. Draft |
| [spec-002](spec-002-shape_pipelines_javascript.md) | JavaScript binding. Draft |
| [spec-003](spec-003-shape_pipelines_python.md) | Python binding. Draft |
| [examples](examples/README.md) | One pipeline in both bindings with shared fixtures. Syntax-checked; not runnable until the libraries exist |
| [skill](skills/shape-pipelines/SKILL.md) | Agent skill: workflow, rules, binding cheat-sheet, testing, review checklist. Draft |

