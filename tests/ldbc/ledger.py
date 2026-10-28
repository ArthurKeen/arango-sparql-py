"""The LDBC outcome ledger (plan §5.2): one row per query text × layout.

Columns, in the order a query gets through them:

* ``translation`` — ``translates``, ``fails_closed`` (a deliberate
  ``SparqlError`` with a stable code: the acceptable failure),
  ``parse_error``, ``out_of_subset`` (the verbatim text uses a vendor
  extension; see ``manifest.yml``) or ``crash`` (any other exception: a
  defect).
* ``execution`` — ``executes``, ``execution_error`` (ArangoDB rejected the
  AQL: a defect), ``timeout`` (over ``LDBC_AQL_MAX_RUNTIME_S``),
  ``resource_limit`` (over ``LDBC_AQL_MEMORY_LIMIT``), or empty when
  translation failed.
* ``oracle`` — ``matches_oracle``, ``translates_wrong`` (a different answer
  from pyoxigraph: a defect, and the worst one), ``oracle_unavailable``
  (pyoxigraph could not answer), or empty.

The translation columns are recomputed without a database by the per-PR
ratchet (``test_ldbc_ratchet.py``); the live columns come from
``python -m tests.ldbc.ledger --live --write``.

Usage::

    python -m tests.ldbc.ledger --write          # translation tier
    python -m tests.ldbc.ledger --live --write   # + execution and oracle (needs data + ArangoDB)
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import sys
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from arango_sparql.api import TranslateResult, translate
from arango_sparql.errors import SparqlError, SparqlParseError
from tests.ldbc import mappings, params
from tests.ldbc.compare import Ordering, compare
from tests.ldbc.loader import LAYOUTS, Layout

logger = logging.getLogger(__name__)

HERE = Path(__file__).parent
QUERIES = HERE / "queries"
MANIFEST = HERE / "manifest.yml"
LEDGER = HERE / "conformance_ledger.tsv"

VERBATIM = "verbatim"

#: ArangoDB's own time limit for one query, in seconds (``maxRuntime``).
AQL_MAX_RUNTIME_S = float(os.getenv("LDBC_AQL_MAX_RUNTIME_S", "120"))

#: Per-query memory limit in bytes (``memoryLimit``). Without it ArangoDB's
#: automatic limit is ~60 % of the machine, and on a shared Docker VM one
#: runaway query can get the whole server OOM-killed (seen: IC6 on RPT).
AQL_MEMORY_LIMIT = int(os.getenv("LDBC_AQL_MEMORY_LIMIT", str(1 << 30)))

#: ArangoDB error numbers the ledger records as their own outcomes.
_ERR_QUERY_KILLED = 1500  # includes maxRuntime exceeded
_ERR_RESOURCE_LIMIT = 32  # memoryLimit exceeded

#: Detail strings longer than this are cut, so the ledger stays readable.
_DETAIL_MAX = 160


@dataclass(frozen=True)
class QuerySpec:
    op: str
    ordering: Ordering
    order_keys: tuple[str, ...] = ()
    concat_columns: tuple[str, ...] = ()
    variants: tuple[str, ...] = ()
    out_of_subset: str | None = None
    #: The top level is a GROUP BY: see ``compare.compare(empty_group_row=…)``.
    grouped: bool = False


@dataclass(frozen=True)
class QueryText:
    spec: QuerySpec
    form: str  # ``verbatim`` or a variant name
    path: Path

    @property
    def op(self) -> str:
        return self.spec.op

    @property
    def text(self) -> str:
        return self.path.read_text(encoding="utf-8")


def _op_sort_key(op: str) -> tuple[int, int]:
    kind, number = re.fullmatch(r"interactive-(short|complex)-(\d+)", op).groups()  # type: ignore[union-attr]
    return (0 if kind == "short" else 1, int(number))


def load_manifest() -> list[QuerySpec]:
    raw = yaml.safe_load(MANIFEST.read_text())
    specs = [
        QuerySpec(
            op=op,
            ordering=entry["ordering"],
            order_keys=tuple(entry.get("order_keys", ())),
            concat_columns=tuple(entry.get("concat_columns", ())),
            variants=tuple(entry.get("variants", ())),
            out_of_subset=entry.get("out_of_subset"),
            grouped=bool(entry.get("grouped", False)),
        )
        for op, entry in raw.items()
    ]
    return sorted(specs, key=lambda s: _op_sort_key(s.op))


def corpus() -> list[QueryText]:
    """Every query text: each verbatim file, then its variants."""
    texts: list[QueryText] = []
    for spec in load_manifest():
        texts.append(QueryText(spec, VERBATIM, QUERIES / "interactive_v1" / f"{spec.op}.rq"))
        for variant in spec.variants:
            texts.append(QueryText(spec, variant, QUERIES / "variants" / f"{spec.op}.{variant}.rq"))
    return texts


@dataclass
class Cell:
    op: str
    form: str
    layout: str
    translation: str = ""
    translation_detail: str = ""
    execution: str = ""
    oracle: str = ""
    live_detail: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.op, self.form, self.layout)


def _short(exc: BaseException) -> str:
    message = " ".join(str(exc).split())
    text = f"{type(exc).__name__}: {message}"
    return text if len(text) <= _DETAIL_MAX else text[: _DETAIL_MAX - 1] + "…"


@dataclass
class Translation:
    status: str
    detail: str = ""
    result: TranslateResult | None = None
    seconds: float = 0.0


def translate_text(qtext: QueryText, layout: Layout, binding: dict[str, str]) -> Translation:
    sparql = params.splice(qtext.text, binding)
    resolver = mappings.resolver(layout)
    started = time.perf_counter()
    try:
        result = translate(sparql, resolver=resolver)
        return Translation("translates", "", result, time.perf_counter() - started)
    except SparqlParseError as exc:
        status, detail = "parse_error", _short(exc)
    except SparqlError as exc:
        status, detail = "fails_closed", f"{exc.code} {_short(exc)}"
    except Exception as exc:  # noqa: BLE001 — any other exception is a crash, recorded as a defect
        status, detail = "crash", _short(exc)
    seconds = time.perf_counter() - started
    if qtext.form == VERBATIM and qtext.spec.out_of_subset:
        return Translation("out_of_subset", f"{qtext.spec.out_of_subset} ({detail})", None, seconds)
    return Translation(status, detail, None, seconds)


def translation_cells() -> list[Cell]:
    """The translation tier for every text × layout, with each query's first binding."""
    bindings = params.load("0.1")
    cells: list[Cell] = []
    for qtext in corpus():
        for layout in LAYOUTS:
            outcome = translate_text(qtext, layout, bindings[qtext.op][0])
            cells.append(Cell(qtext.op, qtext.form, layout, outcome.status, outcome.detail))
    return cells


# ---------------------------------------------------------------------------
# Live tier
# ---------------------------------------------------------------------------


@dataclass
class LiveRun:
    """One query text on one layout over all its bindings."""

    execution: str
    oracle: str
    detail: str
    translate_seconds: list[float] = field(default_factory=list)
    aql_seconds: list[float] = field(default_factory=list)
    oracle_seconds: list[float] = field(default_factory=list)
    rows: list[int] = field(default_factory=list)


def execute_aql(db: Any, result: TranslateResult) -> tuple[list[dict[str, Any]], float]:
    """Run translated AQL; return its rows and the server-side execution time."""
    cursor = db.aql.execute(
        result.aql,
        bind_vars=result.bind_vars,
        max_runtime=AQL_MAX_RUNTIME_S,
        memory_limit=AQL_MEMORY_LIMIT,
        batch_size=1000,
        count=False,
    )
    rows = [row if isinstance(row, dict) else {"_value": row} for row in cursor]
    stats = cursor.statistics() or {}
    return rows, float(stats.get("execution_time", 0.0))


def execution_status(exc: BaseException) -> str:
    """The ledger outcome for an execution failure, from ArangoDB's error number.

    A lost connection is not an outcome of the query: it is re-raised so a
    run against a dead server fails loudly instead of recording every cell
    as broken. Any other failure — ArangoDB rejecting the AQL, or the driver
    unable to send it (a bind value with no JSON form) — is the cell's
    ``execution_error``: a translator defect.
    """
    import requests

    if isinstance(exc, ConnectionError | requests.exceptions.ConnectionError):
        raise exc
    code = getattr(exc, "error_code", None)
    if code == _ERR_QUERY_KILLED:
        return "timeout"
    if code == _ERR_RESOURCE_LIMIT:
        return "resource_limit"
    return "execution_error"


def run_live(
    db: Any,
    qtext: QueryText,
    layout: Layout,
    bindings: list[dict[str, str]],
    nt_path: Path,
    data_sha256: str,
) -> LiveRun:
    from tests.ldbc import oracle

    run = LiveRun(execution="executes", oracle="matches_oracle", detail="")
    for index, binding in enumerate(bindings):
        translation = translate_text(qtext, layout, binding)
        if translation.result is None:
            return LiveRun("", "", f"binding {index}: {translation.status} {translation.detail}")
        run.translate_seconds.append(translation.seconds)
        try:
            actual, server_seconds = execute_aql(db, translation.result)
        except Exception as exc:  # noqa: BLE001 — recorded as the cell's outcome
            return LiveRun(execution_status(exc), "", f"binding {index}: {_short(exc)}")
        run.aql_seconds.append(server_seconds)
        run.rows.append(len(actual))
        try:
            expected = oracle.cached_run(
                nt_path, data_sha256, params.splice(qtext.text, binding), timeout_s=oracle.timeout_s()
            )
        except Exception as exc:  # noqa: BLE001 — the oracle could not answer; not a translator verdict
            run.oracle = "oracle_unavailable"
            run.detail = f"binding {index}: {_short(exc)}"
            continue
        run.oracle_seconds.append(expected.seconds)
        verdict = compare(
            expected.rows,
            actual,
            ordering=qtext.spec.ordering,
            order_keys=qtext.spec.order_keys,
            concat_columns=qtext.spec.concat_columns,
            empty_group_row=qtext.spec.grouped,
        )
        if not verdict.ok and run.oracle != "translates_wrong":
            run.oracle = "translates_wrong"
            run.detail = f"binding {index}: {verdict.message}"[:_DETAIL_MAX]
    return run


# ---------------------------------------------------------------------------
# The committed TSV
# ---------------------------------------------------------------------------

_COLUMNS = [f.name for f in fields(Cell)]


def read_ledger(path: Path = LEDGER) -> dict[tuple[str, str, str], Cell]:
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        rows = [line for line in handle if not line.startswith("#")]
    reader = csv.DictReader(rows, delimiter="\t")
    return {(r["op"], r["form"], r["layout"]): Cell(**{c: r.get(c, "") for c in _COLUMNS}) for r in reader}


def write_ledger(cells: list[Cell], path: Path = LEDGER) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        handle.write(
            "# LDBC SNB Interactive v1 outcome ledger (PRD §13.6). These are not LDBC Benchmark Results.\n"
            "# Regenerate: python -m tests.ldbc.ledger --write  (add --live for execution and oracle)\n"
        )
        writer = csv.DictWriter(handle, fieldnames=_COLUMNS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for cell in cells:
            writer.writerow({c: getattr(cell, c) for c in _COLUMNS})


def iter_live_cells(
    sf: str, layouts: tuple[Layout, ...], ops: tuple[str, ...] = ()
) -> Iterator[tuple[Cell, LiveRun]]:
    from tests.ldbc import databases, datasets, loader, normalize, oracle

    nt_path, summary = normalize.normalize(
        datasets.turtle_files(sf), datasets.cache_root() / f"sf{sf}-normalized"
    )
    oracle.open_store(nt_path, summary.sha256)
    bindings = params.load(sf)
    for layout in layouts:
        db = databases.open_database(sf, layout)
        loader.load_layout(db, layout, nt_path, data_sha256=summary.sha256, triple_count=summary.triples_out)
        for qtext in corpus():
            if ops and qtext.op not in ops:
                continue
            cell = Cell(qtext.op, qtext.form, layout)
            first = translate_text(qtext, layout, bindings[qtext.op][0])
            cell.translation, cell.translation_detail = first.status, first.detail
            if first.status != "translates":
                yield cell, LiveRun("", "", "")
                continue
            logger.info("live %s %s %s", layout, qtext.op, qtext.form)
            live = run_live(db, qtext, layout, bindings[qtext.op], nt_path, summary.sha256)
            cell.execution, cell.oracle, cell.live_detail = live.execution, live.oracle, live.detail
            yield cell, live


def write_live_timings(timings: list[dict[str, Any]], sf: str, label: str) -> Path:
    """Save a live run's per-binding timings beside the benchmark results.

    One run per binding, no warm-up: coarser than ``bench.py``, but it comes
    free with the correctness run (and is how a change is measured before and
    after it lands).
    """
    import json
    import platform
    from datetime import UTC, datetime

    from tests.ldbc import bench

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    name = f"live-{stamp}-sf{sf}-{platform.node().split('.')[0]}{'-' + label if label else ''}.json"
    out = bench.results_dir() / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"disclaimer": "These are not LDBC Benchmark Results.", "cells": timings}, indent=2)
    )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--live", action="store_true", help="also execute on ArangoDB and compare with the oracle"
    )
    parser.add_argument("--sf", default="0.1")
    parser.add_argument("--layout", action="append", choices=LAYOUTS, help="limit --live to these layouts")
    parser.add_argument("--write", action="store_true", help="write conformance_ledger.tsv")
    parser.add_argument(
        "--label", default="", help="--live: a name for the timings file (e.g. before-join-order)"
    )
    parser.add_argument(
        "--op", action="append", help="--live: only these operations (others keep their recorded cells)"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.live:
        previous = read_ledger()
        live_cells: dict[tuple[str, str, str], Cell] = {}
        timings: list[dict[str, Any]] = []
        for cell, live in iter_live_cells(args.sf, tuple(args.layout or LAYOUTS), tuple(args.op or ())):
            live_cells[cell.key] = cell
            timings.append({**asdict(cell), **asdict(live)})
        cells = [live_cells.get(c.key) or previous.get(c.key) or c for c in translation_cells()]
        print(write_live_timings(timings, args.sf, args.label))
    else:
        previous = read_ledger()
        cells = []
        for cell in translation_cells():
            old = previous.get(cell.key)
            if old is not None and (old.translation, old.translation_detail) == (
                cell.translation,
                cell.translation_detail,
            ):
                cell.execution, cell.oracle, cell.live_detail = old.execution, old.oracle, old.live_detail
            cells.append(cell)

    if args.write:
        write_ledger(cells)
    else:
        writer = csv.writer(sys.stdout, delimiter="\t", lineterminator="\n")
        writer.writerow(_COLUMNS)
        for cell in cells:
            writer.writerow([getattr(cell, c) for c in _COLUMNS])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
