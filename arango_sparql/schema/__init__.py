"""Schema layer (PRD §6.3).

Modules in this package own everything between *acquiring* a customer's
physical mapping and *handing it* to the translator: detection,
fingerprinting, caching, and analyzer integration. The translator
itself (under :mod:`arango_sparql.translate`) reads the resulting
``MappingBundle`` via
:meth:`~arango_sparql.translate.resolver.SchemaResolver.from_mapping_bundle`
and never touches anything here directly.

Modules:

* :mod:`arango_sparql.schema.detect` — heuristic schema classification
  and bundle synthesis (PRD §6.3.1).
* :mod:`arango_sparql.schema.fingerprint` — deterministic SHA-256
  fingerprints over a :class:`MappingBundle` for cache-key derivation
  and drift detection (PRD §6.3.3).
* :mod:`arango_sparql.schema.acquire` — analyzer-first / heuristic-
  fallback acquisition with RPT post-processing (PRD §6.3.2).
* :mod:`arango_sparql.schema.cache` — L1 in-process cache (and a
  documented stub for the L2 persistent tier; PRD §6.3.3).
"""

from arango_sparql.schema.acquire import (
    ANALYZER_INSTALL_HINT,
    ANALYZER_VERSION_RANGE,
    W_ANALYZER_NOT_INSTALLED,
    W_SCHEMA_HEURISTIC_FALLBACK,
    AnalyzerNotInstalledError,
    Strategy,
    acquire_mapping_bundle,
    analyzer_available,
    db_counts_fingerprint,
    db_shape_fingerprint,
)
from arango_sparql.schema.cache import (
    DEFAULT_TTL_SECONDS,
    L2_COLLECTION_NAME,
    TTL_ENV_VAR,
    CachedEntry,
    CacheStatus,
    SchemaCache,
)
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
    "ANALYZER_INSTALL_HINT",
    "ANALYZER_VERSION_RANGE",
    "BundleFingerprint",
    "COVERAGE_THRESHOLD",
    "CacheStatus",
    "CachedEntry",
    "CollectionClassification",
    "DEFAULT_SAMPLE_SIZE",
    "DEFAULT_TTL_SECONDS",
    "FINGERPRINT_PAYLOAD_VERSION",
    "FingerprintDrift",
    "L2_COLLECTION_NAME",
    "RptDetectionResult",
    "SchemaType",
    "SchemaCache",
    "Strategy",
    "TTL_ENV_VAR",
    "W_ANALYZER_NOT_INSTALLED",
    "W_SCHEMA_HEURISTIC_FALLBACK",
    "AnalyzerNotInstalledError",
    "acquire_mapping_bundle",
    "analyzer_available",
    "build_heuristic_mapping",
    "bundle_counts_fingerprint",
    "bundle_shape_fingerprint",
    "classify_schema",
    "compute_bundle_fingerprint",
    "db_counts_fingerprint",
    "db_shape_fingerprint",
    "detect_rpt_pattern",
]
