"""Guard the committed demo Cloud-Optimized GeoTIFF (COG) against drift.

Regenerates the sample COG from ``write_sample_cog`` into a temporary file and
asserts it matches the committed ``docker/data/sample.tif`` *semantically*:
band arrays and key profile fields, never raw bytes (GeoTIFF bytes vary across
GDAL and libtiff versions, so a byte compare would be flaky). It also asserts
the absolute expectations the format and endpoint rely on (the band
descriptions and the presence of overviews), so a future library change that
silently dropped either fails here. Exits non-zero on any mismatch.

Run: ``uv run python docker/check_sample.py``. Because it is invoked as a
script, Python puts this file's directory on ``sys.path[0]``, so
``make_sample_cog`` imports with no packaging.
"""

import sys
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from make_sample_cog import BAND_NAMES, FIXTURE_PATH, write_sample_cog


def main() -> int:
    """Compare the committed fixture to a fresh regeneration.

    Returns:
        Process exit code: 0 when in sync, 1 on a missing fixture or any
        mismatch.
    """
    if not FIXTURE_PATH.exists():
        print(
            f"error: {FIXTURE_PATH} not found; generate it with "
            "`uv run python docker/make_sample_cog.py`",
            file=sys.stderr,
        )

        return 1

    with tempfile.TemporaryDirectory() as tmp:
        regen_path = Path(tmp) / "regen.tif"
        write_sample_cog(regen_path)
        problems = _compare(FIXTURE_PATH, regen_path)

    if problems:
        print(
            "docker/data/sample.tif is out of sync with its generator:",
            file=sys.stderr,
        )

        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)

        print(
            "regenerate and commit it with `uv run python docker/make_sample_cog.py`",
            file=sys.stderr,
        )

        return 1

    print("docker/data/sample.tif is in sync with make_sample_cog.py")

    return 0


def _compare(committed_path: Path, regen_path: Path) -> list[str]:
    """Collect semantic + absolute mismatches between two sample COGs.

    Args:
        committed_path: Path to the committed fixture.
        regen_path: Path to a freshly regenerated COG.

    Returns:
        Human-readable mismatch descriptions; empty when the fixture is valid
        and in sync.
    """
    problems: list[str] = []

    with (
        rasterio.open(committed_path) as committed,
        rasterio.open(regen_path) as regen,
    ):
        if committed.crs != regen.crs:
            problems.append(f"crs: {committed.crs} != {regen.crs}")

        if committed.transform != regen.transform:
            problems.append("transform differs")

        if committed.dtypes != regen.dtypes:
            problems.append(f"dtypes: {committed.dtypes} != {regen.dtypes}")

        committed_shape = (committed.count, committed.width, committed.height)
        regen_shape = (regen.count, regen.width, regen.height)

        if committed_shape != regen_shape:
            problems.append(f"shape: {committed_shape} != {regen_shape}")

        if committed.nodata != regen.nodata:
            problems.append(f"nodata: {committed.nodata} != {regen.nodata}")

        if not np.array_equal(committed.read(), regen.read()):
            problems.append("band arrays differ")

        if committed.descriptions != BAND_NAMES or regen.descriptions != BAND_NAMES:
            problems.append(
                f"descriptions: committed {committed.descriptions}, "
                f"regen {regen.descriptions}, expected {BAND_NAMES}"
            )

        if not committed.overviews(1) or not regen.overviews(1):
            problems.append("missing band-1 overviews (not a COG with a pyramid)")

    return problems


if __name__ == "__main__":
    sys.exit(main())
