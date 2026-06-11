"""titiler-covjson: CoverageJSON output format extension for TiTiler."""

from titiler_covjson.helpers import (
    create_spatial_2d_reference,
    create_temporal_reference,
    create_unit,
    crs_to_ogc_uri,
    numpy_dtype_to_ndarray,
    numpy_to_covjson_dtype,
)
from titiler_covjson.input import (
    BandInfo,
    CoverageInput,
    band_info_from_reader_info,
    imagedata_to_coverage_input,
)

__version__ = "0.1.0"

__all__ = [
    "BandInfo",
    "CoverageInput",
    "band_info_from_reader_info",
    "create_spatial_2d_reference",
    "create_temporal_reference",
    "crs_to_ogc_uri",
    "imagedata_to_coverage_input",
    "numpy_dtype_to_ndarray",
    "numpy_to_covjson_dtype",
    "create_unit",
]
