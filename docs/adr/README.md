# Architecture Decision Records

This directory holds Architecture Decision Records (ADRs): the detailed,
append-only historical record of cross-cutting decisions whose rationale a reader
could not recover from the code alone.

## When to write an ADR

Write an ADR when a decision is **cross-cutting** (it shapes more than one module
or the public surface), its rationale would otherwise be lost (a reader could not
recover the "why" from the code or git history), and it had **genuine rejected
alternatives**. A choice with no real alternative is a convention -- record it in
CLAUDE.md, not here. Larger supporting analyses (e.g.,
[07-api-design-alternatives.md](../07-api-design-alternatives.md)) stay as
standalone studies; the ADR captures just the chosen direction and links to the
study.

## Numbering

ADRs are numbered with a zero-padded, plain-sequential `NNNN` prefix
(`0001-title.md`, `0002-title.md`, ...), assigned in the order they are created,
regardless of topic or eventual status. The number is permanent: a `Proposed` ADR
keeps its number when accepted, and a superseded ADR keeps its file and number and
is marked `Superseded by ADR-NNNN` rather than deleted or renumbered.

`template.md` is the copyable starting point and is intentionally not numbered, so
it never consumes a sequence number.

## Format

Each ADR follows the lightweight template in [template.md](template.md):

- **Title** -- `# ADR-NNNN: <decision>`.
- **Status** -- one of `Proposed` (drafted and under review, e.g., in an open
  PR), `Accepted` (ratified and in force), `Rejected` (written up for the record
  but decided against), `Deprecated` (no longer relevant but not replaced by a
  specific successor), or `Superseded by ADR-NNNN` (replaced by a later
  decision). A `Proposed` ADR keeps its number from creation; it is not
  renumbered when accepted.
- **Context** -- the forces at play; what made this a decision worth recording.
- **Decision** -- what we chose, stated plainly.
- **Alternatives considered** -- the real rejected options and why they lost.
- **Consequences** -- what follows, including the costs we accept.

Keep each ADR self-contained, and do not restate conventions already in CLAUDE.md.

## Index

- [ADR-0001](0001-covjson-http-api-direction.md) -- CoverageJSON HTTP API
  direction: EDR-vocabulary surface on a dedicated `BaseFactory` subclass
