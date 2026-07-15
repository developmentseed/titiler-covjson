# ADR-0003: Dataset open/read failures return HTTP 500 by default

## Status

Accepted

## Context

Every endpoint opens a caller-supplied dataset `url` through rio-tiler and
rasterio. When that open or read fails (a missing or malformed `url`, a
non-raster file, an unreachable or forbidden remote, or a transient network
error), rasterio raises `rasterio.errors.RasterioIOError`. With TiTiler's
exception handlers installed (`add_exception_handlers`), that exception renders
as an HTTP `500` with a JSON `detail` body.

A review of the `/bbox` endpoint spec (PR #36, on
[docs/08-bbox-endpoint-spec.md](../08-bbox-endpoint-spec.md) Section 10) flagged
that `500` reads as a server error for what is often bad client input (a
mistyped or missing `url`), and that a `400` or `404` would be more
conventional. That is a fair observation, so the status code for this failure is
worth recording rather than leaving implicit.

Two facts constrain the choice:

- rasterio collapses every open/read failure into the same `RasterioIOError`.
  There is no structured error code to branch on, only a human-readable message,
  and that message is not a stable contract: it varies with the GDAL version,
  with the driver and virtual file system handler that serviced the `url` (a
  local `GTiff` read versus `/vsicurl/` versus `/vsis3/`), and it can be
  localized. A local miss reads `No such file or directory`; a remote miss via
  `/vsicurl/` surfaces an HTTP status instead; an absent S3 object can report
  `Access Denied` (403), because S3 returns 403 for objects in buckets the
  caller cannot list. Verified locally: a nonexistent file, an
  existing-but-non-raster file, and an unreachable remote URL all raise the
  identical `RasterioIOError`.
- The server opens `url` with its own identity (an Identity and Access
  Management (IAM) role, network position, and local filesystem) that differs
  from the caller's. What the server can and cannot reach is not what the caller
  can and cannot reach.

## Decision

Keep `500` as the library default for a dataset open/read failure. Do not
inspect the failure cause, and do not split `404` versus `500` (or any other
client-versus-server distinction) inside the library. Treat the "right" status
as a property of the deployment's trust model, and let a host application remap
`RasterioIOError` when its trust model permits. The README documents the
override recipe.

## Alternatives considered

- **Split `404`/`500` by detecting the cause.** Rejected on two independent
  grounds. First, it is an information-disclosure oracle: because the server
  opens `url` from a privileged vantage, distinguishing "not found" from "exists
  but forbidden" lets a caller use the server as a confused deputy to enumerate
  the existence and authorization of internal buckets, hosts, or paths it cannot
  reach directly. (This is why S3 itself returns `403` rather than `404` for
  objects in buckets you cannot list.) The oracle exists regardless of how
  robustly the cause is detected, so robustness does not rescue the split.
  Second, robust detection is unavailable anyway: it would mean matching the
  unstable GDAL message strings above, coupling the status code to GDAL
  internals so a routine upgrade could silently misroute codes (tests green,
  production wrong).
- **Return a uniform `4xx` (e.g., a `400` "could not open the dataset").**
  Rejected: it avoids the oracle and gives client-error semantics, but it
  mislabels a genuinely transient upstream failure (a momentary network error,
  an expired server credential) as the client's fault, and separating transient
  from permanent needs the same cause classification the first alternative was
  rejected for.

## Consequences

- `500` is the conservative default: it never wrongly blames the client, never
  turns the status code into an existence-or-authorization oracle, and matches
  the rio-tiler / GDAL / TiTiler stack the extension proxies.
- The cost: a client cannot tell a mistyped `url` from a server-side outage by
  the status code alone.
- The uniform status code closes the status-code oracle, not the body. TiTiler
  still renders the raw exception message into the response `detail` (e.g.,
  `No such file or directory`), and that message is itself unreliable: an
  existing non-raster file reports the same "not found" text. A deployment that
  must not disclose it can narrow the `detail` in its own exception handler.
- The decision is reversible per deployment, not per release. A single-tenant or
  otherwise trusted deployment (where the caller's access scope matches the
  server's, so the existence oracle is moot) can remap `RasterioIOError` to a
  `4xx` for better client experience, content-delivery-network behavior (content
  delivery networks retry `5xx`, not `4xx`), and service-level-objective hygiene
  (client mistakes stop polluting `5xx` metrics). The README carries the
  override recipe.
- Revisit if the library gains its own dataset-resolution layer (for example, a
  catalog or SpatioTemporal Asset Catalog (STAC) source) that can distinguish
  "no such dataset" from "backend failure" above rasterio without
  string-matching. That would be a genuine `404` the library could own.
