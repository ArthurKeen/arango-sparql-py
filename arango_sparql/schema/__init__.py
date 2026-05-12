"""Schema layer (PRD §6.3).

Modules in this package own everything between *acquiring* a customer's
physical mapping and *handing it* to the translator: detection,
fingerprinting, caching, and analyzer integration. The translator
itself (under :mod:`arango_sparql.translate`) reads the resulting
``MappingBundle`` via
:meth:`~arango_sparql.translate.resolver.SchemaResolver.from_mapping_bundle`
and never touches anything here directly.

Modules:

* :mod:`arango_sparql.schema.fingerprint` — deterministic SHA-256
  fingerprints over a :class:`MappingBundle` for cache-key derivation
  and drift detection (PRD §6.3.3).

Pending slices add detection (``detect.py``), acquisition
(``acquire.py``), and the L1+L2 cache (``cache.py``).
"""

from arango_sparql.schema.detect import (
    COVERAGE_THRESHOLD,
    DEFAULT_SAMPLE_SIZE,
    CollectionClassification,
    RptDetectionResult,
    SchemaType,
    build_heuristic_mapping,
    classify_schema,
    detect_rpt_pattern,
)
from arango_sparql.schema.fingerprint import (
    FINGERPRINT_PAYLOAD_VERSION,
    BundleFingerprint,
    FingerprintDrift,
    bundle_counts_fingerprint,
    bundle_shape_fingerprint,
    compute_bundle_fingerprint,
)

__all__ = [
    "BundleFingerprint",
    "COVERAGE_THRESHOLD",
    "CollectionClassification",
    "DEFAULT_SAMPLE_SIZE",
    "FINGERPRINT_PAYLOAD_VERSION",
    "FingerprintDrift",
    "RptDetectionResult",
    "SchemaType",
    "build_heuristic_mapping",
    "bundle_counts_fingerprint",
    "bundle_shape_fingerprint",
    "classify_schema",
    "compute_bundle_fingerprint",
    "detect_rpt_pattern",
]
