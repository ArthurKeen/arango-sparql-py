"""End-to-end tests for the W3C SPARQL Protocol endpoint (PRD §5.2).

Three test modules in this folder share fixtures via ``conftest.py``:

* :mod:`test_happy` — happy-path SELECT / ASK across the four
  result-format media types, observability headers, session-binding
  alternatives, and the Service Description path.
* :mod:`test_accept` — RFC 9110 §12.5.1 result-format negotiation
  rules 1-4 (PRD §5.2 priority list, tie-break by priority order,
  406 with the supported-list body).
* :mod:`test_errors` — every documented error row in the PRD §5.2
  error table (405 / 400 / 422 / 503 / 504 / 200+truncated /
  429 / 401 / 413).
"""
