# ADR-0002: Dependency floor policy -- deliberate, verified minimums

## Status

Accepted

## Context

titiler-covjson is a library (a titiler router extension), so the lower bounds
on its runtime dependencies in pyproject.toml are its compatibility contract:
they define the widest set of dependency versions a downstream project can
resolve alongside it. Three forces had made those floors unreliable:

- They were a mix of deliberate and inherited. Some carried an upper bound or a
  rationale; others were set once and never revisited.
- Dependabot silently raised them. Under its default versioning strategy it
  rewrote the bounds in pyproject.toml on each upstream release (for example
  numpy from 2.2.6 to 2.4.6), narrowing downstream compatibility even though the
  library used nothing from the newer releases.
- They were untested. Continuous integration only ever resolved the latest
  versions (uv sync --locked), so every floor was an unverified claim. The audit
  behind this record found floors wrong in both directions: a numpy floor
  inflated well above the basic API the code uses, and a pyproj floor (3.0) so
  low it resolved to a release with no wheel for the supported Python.

## Decision

Adopt a deliberate, verified floor policy.

- Floor-selection principle: each runtime floor is the lowest version that both
  provides the API the library actually uses and ships a wheel for the Python
  floor (currently 3.11). Floors rise only by deliberate decision.
- Test both ends. Continuous integration resolves the dependency graph two ways:
  the `highest` resolution (the committed lockfile) across the full Python
  matrix, and a single `lowest-direct` leg, pinned to the Python floor, that
  resolves the direct dependencies to their declared minimums. Both are
  blocking.
- The `lowest-direct` leg runs only on the Python floor because the floors are
  chosen against that interpreter's available wheels. Pairing the oldest
  dependencies with a newer interpreter would test a combination no user runs
  and would force floors upward for no downstream benefit.
- Dependabot uses versioning-strategy: lockfile-only on the uv ecosystem, so it
  refreshes uv.lock but never rewrites the pyproject bounds.

## Alternatives considered

- Leave floors Dependabot-managed (the prior state). Rejected: silently
  ratcheting floors narrows compatibility with no corresponding need, and an
  untested floor is a claim rather than a guarantee.
- Test only the latest resolution. Rejected: it never exercises the declared
  minimums, so a floor that is too low or unbuildable is discovered by a
  downstream user instead of by continuous integration.
- Cross `lowest-direct` with the full Python matrix. Rejected: it would force
  each floor up to the lowest version with a wheel for the newest interpreter,
  narrowing reach to satisfy a dependency-and-interpreter pairing no one runs.
- Add upper bounds to stabilize resolution. Rejected for runtime dependencies: a
  library avoids artificial ceilings. The existing caps (for example
  titiler.core<3.0) are deliberate framework-coupling bounds, and this policy
  introduces no new ones.

## Consequences

- The `lowest-direct` leg holds direct dependencies at their floors while
  resolving transitive dependencies to modern versions, so a newly released
  transitive dependency can occasionally clash with an old floor and turn the
  blocking leg red even though nothing in the library changed. The resolution is
  always to raise the offending floor to the lowest version compatible with the
  current graph, never to cap the transitive dependency.
- Lowering a floor now requires evidence: the `lowest-direct` leg must stay
  green at the new minimum.
- The development and documentation tooling carries lower bounds too, so the
  `lowest-direct` resolve does not drag it down to unbuildable ancient releases.
  Those bounds are dev-only and are not part of the runtime compatibility
  contract.
- The Python floor itself is out of this policy's scope.
