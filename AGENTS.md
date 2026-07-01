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
    → RasterCovJSONModeler (src/titiler_covjson/modeler.py)
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
- `modeler.py`, `routes.py`, `router.py`: stubs; implementation roadmap is in
  `docs/05-implementation-roadmap.md`

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

## Architecture decisions (ADRs)

Cross-cutting decisions are recorded as Architecture Decision Records in
[docs/adr/](docs/adr/) using the lightweight template there (Status / Context /
Decision / Alternatives considered / Consequences). Write an ADR when a decision
shapes more than one module or the public surface, its rationale would otherwise
be unrecoverable from the code, and it had genuine rejected alternatives; a
choice with no real alternative is a convention, so record it here in AGENTS.md
instead. See [docs/adr/README.md](docs/adr/README.md) for numbering and
mechanics.
