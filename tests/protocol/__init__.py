"""Unit tests for the W3C SPARQL Protocol helper modules.

These tests target the helpers in :mod:`arango_sparql.service.protocol`
in isolation — no FastAPI, no DB, no rdflib — so they catch
spec-compliance regressions before the integration-level
``tests/test_service_sparql_protocol_*.py`` runs.
"""
