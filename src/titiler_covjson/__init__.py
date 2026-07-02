"""titiler-covjson: CoverageJSON output format for TiTiler.

Public API: the CoverageJSON factory and its HTTP response surface, re-exported
here for convenience, e.g., ``from titiler_covjson import CovJSONFactory``. The
model layer (``input``, ``modeler``, ``helpers``) remains accessible via its
submodules but is intentionally not part of the root API.
"""

from titiler_covjson.factory import CovJSONFactory
from titiler_covjson.responses import COVJSON_MEDIA_TYPE, CovJSONResponse

__version__ = "0.1.0"

__all__ = [
    "COVJSON_MEDIA_TYPE",
    "CovJSONFactory",
    "CovJSONResponse",
]
