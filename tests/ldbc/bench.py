"""Performance runner for the LDBC reads (plan WP-LDBC-BENCH, PRD §13.6 LDBC-6).

These are not LDBC Benchmark Results. The runner does not drive the LDBC
workload (no schedule, no updates, no throughput); it times each read on its
own, which is what a transpiler needs to see.

For every query text that translates on a layout, and every non-empty
binding:

* **translation** — ``translate()`` with a freshly parsed mapping (cold) and
  with a cached one (warm), as PRD §9.4 defines them;
* **execution** — ``WARMUP`` discarded runs, then ``REPEATS`` timed runs;
  the server-side ``executionTime`` from the cursor statistics (no client or
  network time), plus the client wall time and the row count;
* **plan** — an ``EXPLAIN`` digest (node types, collections, indexes,
  optimizer rules, estimated cost), so plan regressions are diffable.

The outcome columns of ``conformance_ledger.tsv`` travel with each entry: a
fast wrong answer is not a result. Output is JSON, written to the cache
directory (it is a measurement of one machine, not a repository artefact);
``report.py`` renders it.

Usage::

    python -m tests.ldbc.bench --sf 0.1 --layout rpt --layout pg
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import platform
import statistics
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arango.exceptions import AQLQueryExplainError

from arango_sparql.api import translate
from tests.ldbc import ledger, mappings, params
from tests.ldbc.loader import LAYOUTS, LOADER_VERSION, Layout

logger = logging.getLogger(__name__)

WARMUP = int(os.getenv("LDBC_BENCH_WARMUP", "1"))
REPEATS = int(os.getenv("LDBC_BENCH_REPEATS", "3"))
TRANSLATE_REPEATS = 20


@dataclass
class Timing:
    """Seconds, over every timed run of every binding."""

    samples: list[float] = field(default_factory=list)

    def summary(self) -> dict[str, float | int]:
        if not self.samples:
            return {"n": 0}
        ordered = sorted(self.samples)

        def pct(p: float) -> float:
            return ordered[min(len(ordered) - 1, round(p * (len(ordered) - 1)))]

        return {
            "n": len(ordered),
            "p50": pct(0.50),
            "p95": pct(0.95),
            "p99": pct(0.99),
            "mean": statistics.fmean(ordered),
            "max": ordered[-1],
        }


@dataclass
class Entry:
    op: str
    form: str
    layout: str
    ledger_translation: str
    ledger_execution: str
    ledger_oracle: str
    status: str = "ok"  # ok | timeout | resource_limit | execution_error
    detail: str = ""
    translate_cold: dict[str, Any] = field(default_factory=dict)
    translate_warm: dict[str, Any] = field(default_factory=dict)
    server: dict[str, Any] = field(default_factory=dict)
    client: dict[str, Any] = field(default_factory=dict)
    rows: list[int] = field(default_factory=list)
    plan: dict[str, Any] = field(default_factory=dict)
    oracle_seconds: list[float] = field(default_factory=list)


def _node_indexes(node: dict[str, Any]) -> list[dict[str, Any]]:
    """A plan node's indexes: a list, except a TraversalNode's
    ``{"base": [...], "levels": {depth: [...]}}``."""
    indexes = node.get("indexes", [])
    if isinstance(indexes, dict):
        levels = indexes.get("levels") or {}
        return [*indexes.get("base", []), *(i for level in levels.values() for i in level)]
    return list(indexes)


def plan_digest(db: Any, aql: str, bind_vars: dict[str, Any]) -> dict[str, Any]:
    plan = db.aql.explain(aql, bind_vars=bind_vars)
    if isinstance(plan, list):
        plan = plan[0]
    nodes = []
    for node in plan.get("nodes", []):
        item = node.get("type", "?")
        if node.get("collection"):
            item += f":{node['collection']}"
        indexes = [
            i.get("type", "?") + "[" + ",".join(i.get("fields", [])) + "]" for i in _node_indexes(node)
        ]
        if indexes:
            item += "@" + "+".join(indexes)
        nodes.append(item)
    return {
        "nodes": nodes,
        "rules": plan.get("rules", []),
        "estimated_cost": plan.get("estimatedCost"),
        "full_scans": sum(1 for n in plan.get("nodes", []) if n.get("type") == "EnumerateCollectionNode"),
    }


def _time_translation(
    qtext: ledger.QueryText, layout: Layout, binding: dict[str, str]
) -> tuple[float, float]:
    sparql = params.splice(qtext.text, binding)
    cold_started = time.perf_counter()
    resolver = mappings.resolver(layout)
    translate(sparql, resolver=resolver)
    cold = time.perf_counter() - cold_started
    warm = []
    for _ in range(TRANSLATE_REPEATS):
        started = time.perf_counter()
        translate(sparql, resolver=resolver)
        warm.append(time.perf_counter() - started)
    return cold, statistics.median(warm)


def bench_text(
    db: Any, qtext: ledger.QueryText, layout: Layout, bindings: list[dict[str, str]], cell: ledger.Cell | None
) -> Entry:
    from tests.ldbc import oracle

    entry = Entry(
        qtext.op,
        qtext.form,
        layout,
        cell.translation if cell else "",
        cell.execution if cell else "",
        cell.oracle if cell else "",
    )
    cold, warm, server, client = Timing(), Timing(), Timing(), Timing()
    # The last binding matches nothing by construction (params.py); it is a
    # correctness case, not a workload.
    for index, binding in enumerate(bindings[:-1]):
        translation = ledger.translate_text(qtext, layout, binding)
        if translation.result is None:
            entry.status, entry.detail = translation.status, translation.detail
            return entry
        c, w = _time_translation(qtext, layout, binding)
        cold.samples.append(c)
        warm.samples.append(w)
        if index == 0:
            try:
                entry.plan = plan_digest(db, translation.result.aql, translation.result.bind_vars)
            except AQLQueryExplainError as exc:  # ArangoDB refused the AQL; recorded, the run moves on
                entry.status = ledger.execution_status(exc)
                entry.detail = f"EXPLAIN: {exc}"[:300]
                break
        for run in range(WARMUP + REPEATS):
            started = time.perf_counter()
            try:
                rows, server_seconds = ledger.execute_aql(db, translation.result)
            except Exception as exc:  # noqa: BLE001 — recorded, the run moves on
                entry.status = ledger.execution_status(exc)
                entry.detail = f"binding {index}: {exc}"[:300]
                break
            if run >= WARMUP:
                server.samples.append(server_seconds)
                client.samples.append(time.perf_counter() - started)
        else:
            entry.rows.append(len(rows))
        cached = _cached_oracle_seconds(oracle, qtext, binding)
        if cached is not None:
            entry.oracle_seconds.append(cached)
        if entry.status != "ok":
            break
    entry.translate_cold = cold.summary()
    entry.translate_warm = warm.summary()
    entry.server = server.summary()
    entry.client = client.summary()
    return entry


def _cached_oracle_seconds(oracle: Any, qtext: ledger.QueryText, binding: dict[str, str]) -> float | None:
    """pyoxigraph's time for the same text, if the ledger run cached it (indicative only)."""
    from tests.ldbc import datasets

    sparql = params.splice(qtext.text, binding)
    for summary_path in datasets.cache_root().glob("sf*-normalized/normalized.json"):
        sha = json.loads(summary_path.read_text()).get("sha256", "")
        seconds = oracle.cached_seconds(summary_path.parent / "normalized.nt", sha, sparql)
        if seconds is not None:
            return float(seconds)
    return None


def _git_sha() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True)
        return out.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def environment(db: Any, sf: str, data_sha256: str) -> dict[str, Any]:
    import pyoxigraph

    mapping_digest = hashlib.sha256(
        b"".join(mappings.path(layout).read_bytes() for layout in LAYOUTS)  # type: ignore[arg-type]
    ).hexdigest()
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "scale_factor": sf,
        "data_sha256": data_sha256,
        "loader_version": LOADER_VERSION,
        "mapping_sha256": mapping_digest,
        "git_sha": _git_sha(),
        "arangodb_version": db.version(),
        "arangodb_engine": db.engine().get("name"),
        "pyoxigraph_version": pyoxigraph.__version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "warmup": WARMUP,
        "repeats": REPEATS,
        "aql_max_runtime_s": ledger.AQL_MAX_RUNTIME_S,
        "aql_memory_limit": ledger.AQL_MEMORY_LIMIT,
    }


def run(sf: str, layouts: tuple[Layout, ...]) -> dict[str, Any]:
    from tests.ldbc import databases, datasets, loader, normalize

    nt_path, summary = normalize.normalize(
        datasets.turtle_files(sf), datasets.cache_root() / f"sf{sf}-normalized"
    )
    committed = ledger.read_ledger()
    bindings = params.load(sf)
    entries: list[Entry] = []
    env: dict[str, Any] = {}
    loads: dict[str, Any] = {}
    for layout in layouts:
        db = databases.open_database(sf, layout)
        report = loader.load_layout(
            db, layout, nt_path, data_sha256=summary.sha256, triple_count=summary.triples_out
        )
        loads[layout] = {"documents": sum(report.counts.values()), "collections": len(report.counts)}
        env = env or environment(db, sf, summary.sha256)
        for qtext in ledger.corpus():
            cell = committed.get((qtext.op, qtext.form, layout))
            if ledger.translate_text(qtext, layout, bindings[qtext.op][0]).status != "translates":
                continue
            logger.info("bench %s %s %s", layout, qtext.op, qtext.form)
            entries.append(bench_text(db, qtext, layout, bindings[qtext.op], cell))
    return {
        "disclaimer": "These are not LDBC Benchmark Results.",
        "environment": env,
        "loads": loads,
        "ledger": [asdict(c) for c in committed.values()],
        "entries": [asdict(e) for e in entries],
    }


def results_dir() -> Path:
    from tests.ldbc import datasets

    return datasets.cache_root() / "results"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--sf", default="0.1")
    parser.add_argument("--layout", action="append", choices=LAYOUTS)
    parser.add_argument("--out", type=Path, help="output JSON (default: the cache's results/ directory)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    data = run(args.sf, tuple(args.layout or LAYOUTS))
    out = args.out or results_dir() / (
        f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-sf{args.sf}-{platform.node().split('.')[0]}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2, default=str))
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
