"""Generate the sample Cloud-Optimized GeoTIFF (COG) for the CoverageJSON demo.

Writes a small 2-band EPSG:4326 COG whose extent and band names mirror the
endpoint's test fixtures, so requests use known-good coordinates. Run this
module directly to (re)generate the committed fixture at
``docker/data/sample.tif``; ``docker/check_sample.py`` guards it against drift.
"""

from pathlib import Path

import numpy as np
from rasterio.io import MemoryFile
from rasterio.shutil import copy as rio_copy
from rasterio.transform import from_bounds

BOUNDS = (-10.0, -5.0, 10.0, 5.0)
WIDTH = 24
HEIGHT = 24
BLOCKSIZE = 16
NODATA = -9999.0
BAND_NAMES = ("red", "nir")
FIXTURE_PATH = Path(__file__).parent / "data" / "sample.tif"


def write_sample_cog(path: str) -> None:
    """Write the demo sample COG to ``path``.

    Two ``float32`` bands over the extent ``(-10, -5, 10, 5)`` in EPSG:4326:
    band 1 is a row-major ramp ``0 .. WIDTH*HEIGHT-1``; band 2 copies it and
    sets the top-left pixel to the nodata sentinel. The bands carry the
    descriptions ``red`` and ``nir``. The file is a real COG produced by GDAL's
    built-in COG driver (tiling, one overview level, COG layout).

    Args:
        path: Destination filesystem path for the COG.
    """
    transform = from_bounds(*BOUNDS, WIDTH, HEIGHT)
    band1 = np.arange(WIDTH * HEIGHT, dtype="float32").reshape(HEIGHT, WIDTH)
    band2 = band1.copy()
    band2[0, 0] = NODATA
    profile = {
        "driver": "GTiff",
        "dtype": "float32",
        "count": 2,
        "width": WIDTH,
        "height": HEIGHT,
        "crs": "EPSG:4326",
        "transform": transform,
        "nodata": NODATA,
    }

    # Write an in-memory source GeoTIFF, then copy it out through GDAL's built-in
    # COG driver -- a real COG (tiling + overviews + layout) with no rio-cogeo.
    # Copy from the open source dataset, not the MemoryFile object.
    with MemoryFile() as mem, mem.open(**profile) as src:
        src.write(band1, 1)
        src.write(band2, 2)
        src.set_band_description(1, BAND_NAMES[0])
        src.set_band_description(2, BAND_NAMES[1])
        # nearest overview resampling: averaging would invent physical values
        # the sensor never recorded (the same honesty reason the endpoint omits
        # rescale). predictor=3 is GDAL's lossless floating-point predictor: it
        # compresses this smooth float ramp well and round-trips arrays exactly,
        # so the semantic drift guard is unaffected.
        rio_copy(
            src,
            path,
            driver="COG",
            blocksize=BLOCKSIZE,
            overview_resampling="nearest",
            compress="deflate",
            predictor=3,
        )


if __name__ == "__main__":
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_sample_cog(str(FIXTURE_PATH))
    print(f"wrote {FIXTURE_PATH}")
