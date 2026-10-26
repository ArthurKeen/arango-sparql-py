"""pyoxigraph as the translator's oracle for the LDBC queries (plan D5).

The store holds the same normalised triples every ArangoDB layout was
loaded from, so ``pyoxigraph(query) == ArangoDB(translate(query))`` is a
statement about the translator alone. The store lives on disk beside the
normalised data and is rebuilt only when the data digest changes, so the
load is paid once per data set.

The oracle works around one pyoxigraph defect: 0.5.9 returns *unbound* for
many valid ``xsd:dateTime + xsd:duration`` sums (``2012-01-01T00:00:00Z +
P1D`` is unbound while ``+ P30D`` is not; every sum from 2012-02-01 is),
and LDBC's 2012 dates hit it, which made the oracle wrong for IC3 and IC4.
:func:`fold_date_arithmetic` evaluates the one form those queries use — a
constant dateTime plus a constant whole-day duration — in Python before the
query reaches pyoxigraph. The SPARQL meaning is unchanged.

The oracle never touches the network. pyoxigraph executes ``SERVICE`` by
sending the pattern to the named endpoint over HTTP, and the GraphDB
reference queries name ``http://www.ontotext.com/path#search``; :func:`run`
refuses any query that mentions ``SERVICE`` before pyoxigraph sees it.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import queue as queue_module
import re
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pyoxigraph as ox

from tests.helpers.oxi import literal_to_python

_MARKER = "loaded.sha256"

#: Conservative on purpose: the word anywhere in the text (even inside a
#: string literal) is refused. A refusal is safe; a network call is not.
#: Whole-line ``#`` comments are ignored (they cannot execute), so a
#: variant's header may say what it replaced.
_SERVICE = re.compile(r"\bSERVICE\b", re.IGNORECASE)
_COMMENT_LINE = re.compile(r"^\s*#.*$", re.MULTILINE)


#: ``"<dateTime>"^^xsd:dateTime + "P<n>D"^^xsd:duration`` with both sides constant.
_DATE_PLUS_DAYS = re.compile(
    r'"(?P<dt>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)"\^\^xsd:dateTime\s*\+\s*'
    r'"P(?P<days>\d+)D"\^\^xsd:(?:dayTime)?[dD]uration'
)

#: Part of every cache key: bump when :func:`run`'s rewriting changes, so
#: answers computed under the old behaviour are recomputed.
_CACHE_VERSION = 2


def fold_date_arithmetic(sparql: str) -> str:
    """Replace constant ``dateTime + P<n>D`` sums with the resulting dateTime.

    Works around the pyoxigraph 0.5.9 defect described in the module
    docstring. Exact: whole days on a UTC instant, as XPath
    ``op:add-dayTimeDuration-to-dateTime`` defines them.
    """

    def fold(match: re.Match[str]) -> str:
        start = datetime.fromisoformat(match["dt"].replace("Z", "+00:00"))
        end = (start + timedelta(days=int(match["days"]))).astimezone(UTC)
        lexical = end.strftime("%Y-%m-%dT%H:%M:%S.") + f"{end.microsecond // 1000:03d}Z"
        return f'"{lexical}"^^xsd:dateTime'

    return _DATE_PLUS_DAYS.sub(fold, sparql)


class OracleRefused(RuntimeError):
    """The oracle will not run this query (it would make a network call)."""


@dataclass
class OracleResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    seconds: float


def term_to_python(term: Any) -> Any:
    """The plain value ArangoDB returns for the same RDF term."""
    if isinstance(term, ox.NamedNode):
        return term.value
    if isinstance(term, ox.BlankNode):
        return f"_:{term.value}"
    if isinstance(term, ox.Literal):
        return literal_to_python(term)
    raise TypeError(f"unexpected term {term!r}")


def open_store(nt_path: Path, data_sha256: str) -> ox.Store:
    """Open (building if needed) the on-disk store for the normalised data."""
    store_dir = nt_path.parent / "oxigraph"
    marker = store_dir / _MARKER
    if marker.is_file() and marker.read_text().strip() == data_sha256:
        return ox.Store.read_only(str(store_dir))
    if store_dir.exists():
        shutil.rmtree(store_dir)
    store = ox.Store(str(store_dir))
    store.bulk_load(path=str(nt_path), format=ox.RdfFormat.N_TRIPLES)
    store.optimize()
    store.flush()
    del store
    marker.write_text(data_sha256)
    return ox.Store.read_only(str(store_dir))


def run(store: ox.Store, sparql: str) -> OracleResult:
    """Run a SELECT and return its rows as ``{variable: value}`` (unbound omitted)."""
    if _SERVICE.search(_COMMENT_LINE.sub("", sparql)):
        raise OracleRefused("the query uses SERVICE; the oracle never makes network calls")
    started = time.perf_counter()
    solutions = store.query(fold_date_arithmetic(sparql))
    columns = [v.value for v in solutions.variables]
    rows: list[dict[str, Any]] = []
    for solution in solutions:
        row: dict[str, Any] = {}
        for name in columns:
            term = solution[name]
            if term is not None:
                row[name] = term_to_python(term)
        rows.append(row)
    return OracleResult(columns, rows, time.perf_counter() - started)


class OracleTimeout(RuntimeError):
    """pyoxigraph did not answer within the time limit."""


#: Default time limit for one oracle query. pyoxigraph is not a fast engine
#: for every LDBC shape (IS6, IC5, IC9, IC10, IC12 take minutes at SF0.1);
#: answers are cached, so a longer limit in a nightly run fills the cache
#: for later runs. Override with ``LDBC_ORACLE_TIMEOUT``.
DEFAULT_TIMEOUT_S = 300.0


def timeout_s() -> float:
    return float(os.getenv("LDBC_ORACLE_TIMEOUT", str(DEFAULT_TIMEOUT_S)))


def _child(nt_path: str, data_sha256: str, sparql: str, queue: Any) -> None:
    try:
        result = run(open_store(Path(nt_path), data_sha256), sparql)
        queue.put(("ok", result.columns, result.rows, result.seconds))
    except Exception as exc:  # noqa: BLE001 — sent back to the parent, which re-raises
        queue.put(("error", type(exc).__name__, str(exc), 0.0))


def run_isolated(
    nt_path: Path, data_sha256: str, sparql: str, timeout_s: float = DEFAULT_TIMEOUT_S
) -> OracleResult:
    """:func:`run` in a forked child that is killed after *timeout_s*.

    pyoxigraph has no query time limit of its own. The store must already be
    built (:func:`open_store`), so the child only opens it read-only.
    """
    if _SERVICE.search(_COMMENT_LINE.sub("", sparql)):
        raise OracleRefused("the query uses SERVICE; the oracle never makes network calls")
    ctx = multiprocessing.get_context("fork")
    queue = ctx.Queue()
    proc = ctx.Process(target=_child, args=(str(nt_path), data_sha256, sparql, queue))
    proc.start()
    try:
        status, a, b, seconds = queue.get(timeout=timeout_s)
    except queue_module.Empty:
        proc.kill()
        proc.join()
        raise OracleTimeout(f"no answer within {timeout_s:.0f}s") from None
    proc.join()
    if status == "error":
        raise RuntimeError(f"{a}: {b}")
    return OracleResult(a, b, seconds)


def _cache_file(nt_path: Path, data_sha256: str, sparql: str) -> Path:
    key = hashlib.sha256(f"{_CACHE_VERSION}\n{data_sha256}\n{sparql}".encode()).hexdigest()
    return nt_path.parent / "oracle-cache" / f"{key}.json"


def cached_seconds(nt_path: Path, data_sha256: str, sparql: str) -> float | None:
    """pyoxigraph's time for *sparql*, if an answer is cached (not a timeout)."""
    cache_file = _cache_file(nt_path, data_sha256, sparql)
    if not cache_file.is_file():
        return None
    cached = json.loads(cache_file.read_text())
    return None if "timeout_s" in cached else float(cached["seconds"])


def _write_atomically(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(f".{multiprocessing.current_process().pid}.part")
    tmp.write_text(json.dumps(payload))
    tmp.replace(path)


def cached_run(
    nt_path: Path, data_sha256: str, sparql: str, *, timeout_s: float = DEFAULT_TIMEOUT_S
) -> OracleResult:
    """:func:`run_isolated`, memoised on disk by query text and data digest.

    Answers are cached, and so are timeouts: a query that already ran out of
    time at a limit at least as long as *timeout_s* raises
    :class:`OracleTimeout` at once instead of running again (a slow answer is
    otherwise paid once per binding *and* per layout). A longer limit retries
    it. Errors are not cached.
    """
    cache_file = _cache_file(nt_path, data_sha256, sparql)
    if cache_file.is_file():
        cached = json.loads(cache_file.read_text())
        if "timeout_s" not in cached:
            return OracleResult(cached["columns"], cached["rows"], cached["seconds"])
        if cached["timeout_s"] >= timeout_s:
            raise OracleTimeout(f"no answer within {cached['timeout_s']:.0f}s (cached)")
    try:
        result = run_isolated(nt_path, data_sha256, sparql, timeout_s)
    except OracleTimeout:
        _write_atomically(cache_file, {"timeout_s": timeout_s})
        raise
    _write_atomically(cache_file, {"columns": result.columns, "rows": result.rows, "seconds": result.seconds})
    return result


def warm(sf: str, *, jobs: int, timeout_s: float) -> dict[str, int]:
    """Compute every oracle answer the ledger will ask for, *jobs* at a time.

    Each answer runs in its own forked child (:func:`run_isolated`), so the
    threads here only wait. Returns how many were answered, timed out,
    refused (SERVICE) or failed.
    """
    from concurrent.futures import ThreadPoolExecutor

    from tests.ldbc import datasets, ledger, normalize, params

    nt_path, summary = normalize.normalize(
        datasets.turtle_files(sf), datasets.cache_root() / f"sf{sf}-normalized"
    )
    open_store(nt_path, summary.sha256)
    bindings = params.load(sf)
    texts = {params.splice(q.text, b) for q in ledger.corpus() for b in bindings[q.op]}
    counts = {"answered": 0, "timeout": 0, "refused": 0, "failed": 0}

    def one(sparql: str) -> str:
        try:
            cached_run(nt_path, summary.sha256, sparql, timeout_s=timeout_s)
            return "answered"
        except OracleTimeout:
            return "timeout"
        except OracleRefused:
            return "refused"
        except Exception:  # noqa: BLE001 — counted; the ledger run reports each one
            return "failed"

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for outcome in pool.map(one, sorted(texts)):
            counts[outcome] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Fill the pyoxigraph answer cache for the LDBC corpus.")
    parser.add_argument("--warm", action="store_true", required=True)
    parser.add_argument("--sf", default="0.1")
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    parser.add_argument("--timeout", type=float, default=timeout_s())
    args = parser.parse_args(argv)
    print(warm(args.sf, jobs=args.jobs, timeout_s=args.timeout))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
