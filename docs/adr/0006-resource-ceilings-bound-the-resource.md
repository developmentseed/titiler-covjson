# ADR-0006: A ceiling counts every array a read allocates; room for ordinary multi-band reads comes from a larger default, not a weaker count

## Status

Accepted

## Context

`max_cells` is the ceiling a deployer sets to bound what a single `/bbox` or
`/area` request may allocate. Until this change it counted `width * height`: the
footprint of one band's array.

A read allocates one array **per band**, and a caller chooses the band count.
`expression` splits on `;` into blocks, each evaluated into its own full-size
array, at a few characters per block and with no cap on how many a caller may
write. A wide `bidx`, or no selector at all on a many-band source, multiplies
the same way, bounded by the source rather than by the caller. Measured against
a factory configured at 16 cells, a 50-block expression over a single-band
source allocated 800 cells and returned `200`. The ceiling therefore bounded a
fraction of the allocation, and the fraction was the caller's to choose.

Counting the band axis is not free of consequence, which is what makes this
worth recording. The default (`default_max_size ** 2`, one megapixel) and the
construction invariant (`max_cells >= default_max_size ** 2`) were both written
when the ceiling counted one array. Multiplying by bands without touching either
would make a plain three-band RGB full-extent read exceed the default, so the
most ordinary request against a stock configuration would answer `400`.

The factory already carries three ceilings, each bounding a different resource:
`max_cells` (memory), `max_samples` (the number of point reads a `MULTIPOINT`
triggers), and `max_coords_length` (the text parsed to find them). How this one
is shaped sets the pattern for the ones still to come.

## Decision

A ceiling counts the whole of the resource a request consumes. Where that would
make ordinary requests fail, the headroom comes from raising the ceiling's
default value, never from narrowing what the ceiling counts.

Concretely:

- `max_cells` bounds `width * height * bands`, where bands is the number of
  full-size arrays the read allocates, not the number it returns. The two differ
  for an `expression`: it reads every source band its blocks reference and then
  derives one array per block, so `b1+b2+b3` reads three arrays to return one.
  Counting what is returned would have understated that read threefold.
- The default gains a named band allowance (`DEFAULT_BAND_ALLOWANCE`, four
  bands) so an ordinary multi-band full-extent read still serves out of the box:
  `DEFAULT_MAX_CELLS = DEFAULT_MAX_SIZE ** 2 * DEFAULT_BAND_ALLOWANCE`. The
  allowance is headroom in a default, not a cap: nothing rejects a request for
  more bands so long as the product fits.
- The construction invariant stays `max_cells >= default_max_size ** 2` and is
  documented as guaranteeing a **single-band** full-extent read. A constructor
  cannot promise more, because a dataset's band count is not known until a
  request names one.
- A request naming no sizing is never rejected for size. The cap is the
  factory's to choose there, so it chooses one that fits the ceiling at that
  band count: an 11-band source serves at 617x617 rather than returning `400`
  at 1024x1024. Only a size the caller named explicitly can exceed the ceiling.

## Alternatives considered

**Multiply by bands and leave the default at one megapixel.** The tightest bound
and the simplest to explain, and rejected for what it does out of the box: a
three-band RGB full-extent read would return `400` on a stock deployment until
the deployer discovered the knob. A correct ceiling that breaks the common case
by default teaches deployers to raise it blindly, which costs more safety than
the tighter bound buys.

**Add a separate `max_bands` knob and leave `max_cells` per-array.** This
follows the existing one-knob-per-resource shape (`max_samples`,
`max_coords_length`) and needs no change to the default or the invariant.
Rejected because bands and cells are not separate resources here: they multiply
into one number, the memory a read costs. Two knobs bound that number only as
their product, so a deployer would have to multiply two settings to learn their
real ceiling, and `max_cells` would still not mean what its name says.

**Hoist the check above the dataset open** so an over-limit request is rejected
without any I/O. Rejected as a half-measure: the band count is decidable from
the request alone only for `expression` and `bidx`, and the cell count only
when both `width` and `height` are explicit. It would never cover the
no-selector case or `/area`, so the check after `info()` would have to stay
anyway, leaving the same rule enforced at two altitudes. Opening a dataset reads
metadata, not pixels, so the saving was one metadata round-trip on an abusive
request.

**Reject an unsized read of a many-band source, as any other over-limit read.**
Uniform, and it keeps the ceiling a pure rejection rule with no resolution
arithmetic. Rejected because it breaks the one request a caller can make without
asking for anything: at the default allowance of four bands, a bare URL against
Landsat (11 bands) or any Sentinel-2 stack would answer `400`, and both the
class docstring and this ADR promise that omitting sizing still serves, just
coarsely. Reserving rejection for a size the caller actually named keeps that
promise true, and costs one `min` against an integer square root.

**Raise `DEFAULT_BAND_ALLOWANCE` until common imagery fits.** A one-line change
that keeps the reject-only rule. Rejected as a cliff that moves rather than
disappears: sixteen covers Landsat and not a hyperspectral source, the number is
answerable only by guessing at deployers' data, and it loosens the bound
four-fold for the single-band deployments that never needed it.

## Consequences

- `max_cells` means total cells allocated, so the number a deployer sets is a
  memory budget across all bands rather than a per-band grid size. The error
  message names the band count, so an over-limit request says which half of the
  product it exceeded on.
- The raised default admits a single-band read of up to four megapixels, four
  times what a one-band-sized default would allow. This is accepted: a looser
  bound on the narrowest case buys a bounded one on every other, where the
  multiple was previously the caller's to choose.
- `max_cells` now sets, for an unsized request, the resolution rather than
  whether it is served at all. A deployer who lowers it does not narrow what the
  API answers; they coarsen it. That is a gentler failure mode than a `400`, and
  a quieter one: a deployment serving unexpectedly coarse grids looks like a
  data problem, not a configuration one, so the knob's effect on resolution is
  documented where the knob is.
- The ceiling counts cells, not bytes. A cell costs between two and eight bytes
  depending on dtype and on whether `unscale` promotes an integer band to
  floating point, so the default bounds a request at roughly 8 MB to 34 MB. A
  bytes-based ceiling would track memory exactly; it is not adopted here because
  the cell count is what can be resolved before the read, and it would revisit
  this decision rather than extend it.
- An expression that yields no blocks at all (`expression=;`) would make the
  product zero and pass any ceiling, so it is rejected as an empty selection
  before the count is taken. It was tempting to leave this to rio-tiler, which
  does reject it first today, but that made a bound we advertise depend on an
  upstream library's call ordering.
- The count is deliberately *not* taken from the resolver that supplies band
  metadata, though the two look interchangeable. That resolver answers "which
  bands come back", and an expression returns one band per block while reading
  one array per source band referenced, so `b1+b2+b3` returns one and reads
  three. An earlier revision of this change counted the returned bands and
  undercounted such a read by the source's band count: `expression=b1+...+b40`
  on a 40-band source measured as one array and read forty. The ceiling takes
  its own count, and endpoint tests check that count against real reads. Each
  test pairs a request that fits under the ceiling with one that exceeds it
  only because the read allocates arrays it does not return.
- One band-multiplied path is deliberately left uncovered: a `/position`
  `MULTIPOINT`, which `max_cells` does not govern at all (`max_samples` does,
  capping how many positions one request may name). Bands multiply there too:
  N positions asking for M bands produce N x M values from a cap that counted
  only N, so 1000 positions with 50 expression blocks return 50,000 values
  where one block returns 1,000. This ADR does not extend to it because what
  such a request consumes is different in kind: each position reads a single
  cell, so no large array is ever allocated and the cost lands elsewhere.

  Whoever bounds it should therefore begin by deciding which quantity the new
  ceiling counts, rather than copying this change. Unlike `max_cells`, there is
  no single obvious candidate here: the growth shows up at once as more point
  reads, more CPU evaluating expressions, and more bytes in the response, and
  those do not collapse into one product the way cells x bands do. That choice
  is what fixes both the formula and the knob's name, so it comes first.

  Multiplying bands into `max_samples` is the obvious move and the one to be
  most careful about: it would silently redefine a "sample" from a position
  into a value, so a configured `max_samples` would suddenly permit fewer
  positions than it names. Tracked as issue #103.
