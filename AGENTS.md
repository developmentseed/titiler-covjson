# AGENTS.md

This file provides guidance to AI coding agents (e.g., Claude Code) when
working with code in this repository.

## Project

`titiler-covjson` adds CoverageJSON (CovJSON, OGC Community Standard 21-069r2)
as an output format for TiTiler. It is a FastAPI router extension, not a
standalone service. Python >= 3.11, managed with `uv`.

## Commands

```bash
uv sync                      # install/update environment
uv run pytest                # run all tests (includes doctests in src/)
uv run pytest tests/test_input.py                    # one file
uv run pytest tests/test_input.py::TestX::test_y     # one test
uv run ruff check            # lint (preview rules enabled, incl. DOC docstring lint)
uv run ruff format --check   # format check
uv run mypy                  # type check (strict mode, covers src and tests)
```

Note: `pytest` is configured with `--doctest-modules` and
`testpaths = ["src", "tests"]`, so docstring examples in `src/` are executed as
tests. Keep doctest output exact (NORMALIZE_WHITESPACE and ELLIPSIS flags are
enabled).

## Architecture

Data flow (see `docs/01-design-overview.md` for the full design):

```text
FastAPI endpoint → rio-tiler read (ImageData/PointData/...)
    → CoverageInput (src/titiler_covjson/input.py: neutral intermediate representation)
    → to_coverage (src/titiler_covjson/modeler.py)
    → covjson-pydantic models (Coverage, Domain, Range, Parameter)
    → JSON response (application/prs.coverage+json)
```

The `CoverageInput` layer is the central design decision: endpoint code converts
rio-tiler results into `CoverageInput`, and the modeler consumes only
`CoverageInput`. The modeler must never depend on rio-tiler types, and it can be
tested from plain numpy arrays without raster files. Masked-array entries
represent nodata and serialize as JSON `null`.

- `helpers.py`: CRS-to-OGC-URI mapping, numpy dtype → CovJSON NdArray type
  selection, UCUM unit lookup (via `ucumvert`)
- `input.py`: `CoverageInput` / `BandInfo` dataclasses plus converters from
  rio-tiler results
- `modeler.py`: `to_coverage` and the helpers that build covjson-pydantic models
  from a `CoverageInput` (Grid implemented; other domains follow)
- `factory.py`: `CovJSONFactory(BaseFactory)`, owning the `/bbox` route and
  wiring the reader through the model layer to a `CovJSONResponse`
- `dependencies.py`: `CovJSONBandParams` (EDR `parameter-name` alias) and
  `validate_covjson_format` (the `f` guard)
- `responses.py`: `CovJSONResponse` + `COVJSON_MEDIA_TYPE`

## Code style

- Private function ordering: place module-private functions (those with a
  leading underscore) at the **bottom** of the file, below the public surface
  they support, so the public API a reader imports comes first. This applies to
  both `src/` modules and test modules. Module-level constants may stay near the
  imports at the top. `pytest` fixtures are named collaborators, not private
  helpers: keep them in `tests/conftest.py` (or near the top of a test module
  when genuinely module-local), not swept to the bottom.
- Vertical spacing: surround block statements (`if` / `for` / `while` / `with` /
  `try` / `match`) with blank lines, and precede a `return` statement with a
  blank line. Both rules apply only relative to sibling statements at the **same
  indentation level**: no leading blank when the statement is the first in its
  enclosing block, and no trailing blank when it is the last. A statement
  immediately following a function's docstring is treated as first in the block
  (the docstring is header-like), so it takes no leading blank.
- Imports: use absolute imports only (e.g., `from titiler_covjson.input import
  CoverageInput`); never relative imports (`from .input import ...`). Enforced
  by ruff (`TID`, `ban-relative-imports = "all"`).
- Exception messages: assign the message to a local `msg` variable and
  `raise SomeError(msg)`, rather than building the string inside the `raise`.
  Enforced by ruff (`EM`, `TRY003`).
- Prefer a functional style over an object-oriented one: reach for module-level
  functions and plain data over classes, inheritance, and mutable state. When a
  dependency is heavily object-oriented (FastAPI, the attrs-based titiler
  factories), adopt its idioms where fighting them costs more than it saves
  (e.g., subclassing `BaseFactory`), but keep our own logic in free functions
  (e.g., the `to_kwargs` function instead of a `DefaultDependency.as_dict`
  override).
- Prefer implicit iteration (comprehensions, generator expressions, `map`,
  `itertools.chain`) over explicit `for` / `while` loops, unless a loop is
  genuinely clearer (side effects, early exit, or a comprehension that would be
  hard to read).
- Compose with the standard library (`itertools`, `functools`) where it
  expresses intent, but do not fight the type checker to do so: `mypy` runs
  strict here, so prefer the straightforward form over a clever composition that
  needs casts or `type: ignore`.
- A checker or transform helper returns an iterable (or a value) rather than
  accepting and mutating a shared accumulator; callers combine the results
  (e.g., with a comprehension or `itertools.chain`). Object lifecycle hooks that
  must mutate their instance (e.g., a dataclass `__post_init__`) are exempt.
- Build behavior from small, single-purpose, composable functions, and use
  runnability as the granularity test: if a candidate helper cannot earn a real,
  runnable example (typically a doctest), it is too small to extract; inline it
  instead.
- Keep a functional core and an imperative shell: put derivation and
  transformation logic in pure functions whose result depends only on their
  inputs (no I/O, global state, logging, or hidden mutation), and concentrate
  side effects (dataset reads, time, randomness, building responses) in the thin
  endpoint layer. This mirrors the existing split: the modeler purely transforms
  a `CoverageInput`, while the reader and route are the shell.
- Favor immutability: prefer immutable data for the values you pass and return
  (tuples over lists, `frozenset`, a `NamedTuple` or a frozen dataclass); treat
  function arguments as read-only and never mutate caller-owned data; and never
  use a mutable default argument (use `field(default_factory=...)` or a `None`
  sentinel). Initialization-time normalization in a lifecycle hook (a dataclass
  `__post_init__`) and dataclasses a framework must populate (FastAPI
  dependencies) are exempt, as with the rules above.
- Prefer modeling expected failure as a value rather than an exception, where
  that does not fight Python or its libraries: return `T | None` for a single
  absent or failed case (as `helpers.create_unit` does), a small typed result (a
  union, `NamedTuple`, or `Enum`) when callers must distinguish several outcomes
  or need error detail, or a list of problems from a validator that checks many
  things (so callers see them all at once, not just the first). Reserve `raise`
  for genuinely exceptional conditions and for boundaries: FastAPI dependencies
  and route handlers raise to hand control to titiler's error mapping (e.g.,
  `BadRequestError` maps to 400). Do not thread a `Result` or `Either` type
  through layers where Python and the libraries expect exceptions, and do not
  use exceptions for ordinary control flow (both fight the language).
- Avoid behavior-switching flag parameters: prefer two well-named functions over
  one that takes a `flag=True/False` (or a mode string) that changes what it
  does.

## Dependency injection (FastAPI `Depends`)

FastAPI's `Depends` is a good tool for request-boundary plumbing and a poor
general dependency-injection container: dependencies are declared inline with no
composition root, so deep graphs are hard to trace, and the only native
substitution seam (`app.dependency_overrides`) is a global, identity-keyed dict.
We therefore treat the `titiler.core` `BaseFactory` subclass as the composition
root: its fields are where collaborators are wired and where tests inject
substitutes, and `Depends` is reserved for leaf request concerns.

- Use `Depends` only for request-boundary concerns: parsing and validating
  query/path/header parameters, auth, and request-scoped resources (with `yield`
  for teardown). Do not wrap first-party computation in `Depends`; the route
  handler calls pure functions directly for that.
- Wire collaborators (the reader, configuration, and the dependency callables
  themselves) through factory constructor fields, not nested `Depends`. The
  factory is both the composition root (read its fields to see what an endpoint
  needs) and the test seam (construct it with substitutes). Prefer this to
  `app.dependency_overrides`; use overrides only when no constructor field
  exists, and always reset them in a fixture.
- Keep the dependency graph shallow: a first-party dependency must not depend on
  another first-party dependency (no nesting of our own dependencies), so an
  endpoint's request inputs stay legible at the handler signature. Depending on
  titiler-provided dependencies is fine.
- Keep dependencies thin and logic pure: a dependency only parses, validates, or
  adapts; any non-trivial rule lives in a pure function it calls, testable
  without an app and free of FastAPI (the functional-core/imperative-shell rule
  under Code style, applied to the dependency-injection seam).
- Dependencies are module-level named callables (functions or classes), never
  lambdas or local closures, so they are importable, type-checked, callable
  directly in tests (e.g., `validate_covjson_format("png")`), and overridable by
  identity if ever needed.
- Confine `fastapi` imports (`Depends`, `Query`, framework exceptions) to the
  shell modules (`dependencies.py`, `factory.py`, and routes); the core
  (`input`, `modeler`, `helpers`) never imports FastAPI.
- A dependency returns plain data or nothing: a params dataclass or a kwargs
  dict, or `None` for a pure validator. It never returns a framework object the
  handler must interpret.

## Testing conventions

- `tests/conftest.py` provides `validate_covjson` / `assert_schema_valid`, which
  validate serialized models against the vendored CoverageJSON JSON Schema at
  `tests/fixtures/schemas/coveragejson.json`. New model-producing code should be
  schema-validated this way.
- Serialization uses `model_dump_json(exclude_none=True)`: the schema rejects
  explicit `null` members, but `null` *elements* inside `values` arrays (missing
  data) are preserved.
- `test_spec_roundtrip.py` and `test_playground_roundtrip.py` verify parse →
  serialize → re-parse stability against spec section 9 examples and
  covjson.org playground examples. Known upstream covjson-pydantic gaps (missing
  DomainType enum members, integer/string TiledNdArray) are documented in
  module-level comments in those files; check there before assuming a model bug.

## Documentation style

- Spell out acronyms on first use with brief motivation (e.g., "UCUM (Unified
  Code for Units of Measure)").
- Use American English spelling (e.g., "meter", "serialize", "recognized",
  "center", not "metre", "serialise", "recognised", "centre").
- Use `e.g.,` / `i.e.,` with a trailing comma; write "Section" rather than the
  section symbol.
- Em dashes: prefer colons, parentheses, and shorter sentences over reaching for
  one. When an em dash genuinely earns its keep, write it as a double hyphen
  (`--`), never the em-dash character. Reserve the single hyphen (`-`) for word
  hyphenation and numeric or date ranges (e.g., `2010-2020`).
- Line breaks: wrap git commit messages by the standard convention (subject ~50
  chars, body ~72); do not hard-wrap text in GitHub issues and pull requests
  unless specifically required (let it soft-wrap); hard-wrap Markdown documents
  at 80 characters.
- Fenced code blocks: always give the fence a language. When a block has no
  specific or obvious language (e.g., monospaced plain text or an ASCII
  diagram), mark it `text` rather than leaving the fence bare.
- Docstrings are externally facing (they surface in `help()`, IDE tooltips, and
  generated API documentation), and this includes module-level docstrings. Keep
  every docstring self-contained: no references to internal repository artifacts
  a reader outside the repo cannot resolve, e.g., "the endpoint spec",
  "Section 6", "Finding 4", an ADR number, or a `docs/` filename. State the
  behavior directly instead. Internal cross-references belong in code comments,
  not docstrings, and must name a resolvable artifact (e.g.,
  `docs/08-bbox-endpoint-spec.md` or `ADR-0001`), never a bare "the spec" or a
  pointer into an uncommitted planning document.

## Architecture decisions (ADRs)

Cross-cutting decisions are recorded as Architecture Decision Records in
[docs/adr/](docs/adr/) using the lightweight template there (Status / Context /
Decision / Alternatives considered / Consequences). Write an ADR when a decision
shapes more than one module or the public surface, its rationale would otherwise
be unrecoverable from the code, and it had genuine rejected alternatives; a
choice with no real alternative is a convention, so record it here in AGENTS.md
instead. See [docs/adr/README.md](docs/adr/README.md) for numbering and
mechanics.
