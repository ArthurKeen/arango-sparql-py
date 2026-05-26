"""Print actual-vs-expected diffs for a list of W3C live-execution cases.

Used to triage the "binding divergence" bucket in
``tests/w3c/test_w3c_live_execution.py`` — for each case, the script
loads the data, translates the SPARQL, runs the AQL against ArangoDB,
parses the expected ``.srx`` / ``.srj``, and prints a side-by-side
summary so the operator can group divergences by root cause (datatype
preservation, language tags, ordering, predicate-existence already
fixed, etc.).

USAGE::

    RUN_INTEGRATION=1 ARANGO_PORT=8532 python scripts/diagnose_live_divergences.py \
        functions/if01 functions/concat02 functions/strdt01

Intentionally read-only against ArangoDB except for the per-case
load/teardown the harness already does (drops every ``w3c_diag_*``
collection on exit).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from arango import ArangoClient  # noqa: E402

from arango_sparql.api import translate  # noqa: E402
from arango_sparql.translate.resolver import SchemaResolver  # noqa: E402
from tests.w3c.loader import (  # noqa: E402
    load_w3c_data_to_arango,
    sanitize_for_collection,
    teardown_collections,
)
from tests.w3c.runner import QUERY_EVAL, collect_cases  # noqa: E402
from tests.w3c.srx_parser import (  # noqa: E402
    UnsupportedResultFormat,
    normalize_actual_rows,
    parse_results_file,
)

PREFIX = "w3c_diag_"


def diagnose(short_ids: list[str]) -> int:
    port = os.getenv("ARANGO_PORT", "8532")
    client = ArangoClient(hosts=f"http://localhost:{port}")
    db = client.db("_system", username="root", password="rootpw")
    teardown_collections(db, PREFIX)

    cases_by_id = {c.short_id: c for c in collect_cases(types=frozenset([QUERY_EVAL]))}

    for sid in short_ids:
        case = cases_by_id.get(sid)
        print(f"\n=================== {sid} ===================")
        if case is None:
            print(f"  [skip] short_id {sid!r} not found")
            continue
        sanitized = sanitize_for_collection(sid)
        case_prefix = f"{PREFIX}{sanitized}_"
        default_coll = f"{case_prefix}Document"
        try:
            ontology, _ = load_w3c_data_to_arango(db, case.data_paths, case_prefix)
        except Exception as exc:  # noqa: BLE001
            print(f"  [load failed] {exc}")
            continue

        sparql = case.query_path.read_text(encoding="utf-8")
        print("--- SPARQL ---")
        print(sparql.rstrip())

        resolver = SchemaResolver.from_turtle(ontology, default_collection=default_coll)
        try:
            translated = translate(sparql, resolver=resolver)
        except Exception as exc:  # noqa: BLE001
            print(f"  [translate failed] {type(exc).__name__}: {exc}")
            teardown_collections(db, case_prefix)
            continue
        print("--- AQL ---")
        print(translated.aql)
        print("--- BINDS ---", dict(translated.bind_vars))

        try:
            cursor = db.aql.execute(translated.aql, bind_vars=translated.bind_vars)
            actual_rows = list(cursor)
        except Exception as exc:  # noqa: BLE001
            print(f"  [AQL execution failed] {exc}")
            teardown_collections(db, case_prefix)
            continue

        try:
            expected = parse_results_file(case.expected_path)
        except UnsupportedResultFormat as exc:
            print(f"  [expected parse failed] {exc}")
            teardown_collections(db, case_prefix)
            continue

        if expected.is_ask:
            print("--- EXPECTED (ASK) ---", expected.ask)
            print("--- ACTUAL ROWS ---", actual_rows)
        else:
            print(f"--- EXPECTED ({len(expected.rows or [])} rows) ---")
            for r in (expected.rows or [])[:12]:
                print(f"  {r}")
            if expected.rows and len(expected.rows) > 12:
                print(f"  ... ({len(expected.rows) - 12} more)")
            actual_norm = normalize_actual_rows(actual_rows)
            print(f"--- ACTUAL ({len(actual_norm)} rows) ---")
            for r in actual_norm[:12]:
                print(f"  {r}")
            if len(actual_norm) > 12:
                print(f"  ... ({len(actual_norm) - 12} more)")

        teardown_collections(db, case_prefix)

    teardown_collections(db, PREFIX)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: diagnose_live_divergences.py <short_id> [<short_id> ...]")
        sys.exit(1)
    sys.exit(diagnose(sys.argv[1:]))
