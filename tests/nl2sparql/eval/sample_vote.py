"""Candidate sampling and execution-result voting for one eval arm (lab/nl-loop, W1).

The question this answers: does choosing among several independent
translations by what they *return* — a majority vote over executed answer sets,
optionally after discarding candidates that fail or return nothing — beat a
single translation by more than run-to-run noise? (Execution-grounded selection,
CHASE-SQL ICLR 2025; self-consistency, Wang et al. 2023.)

Two steps, so the paid part runs once:

``sample``  Run every case of one arm ``--n`` times (independent pipeline runs,
            each with its own repair loop), in parallel threads. For each sample,
            record the final SPARQL, whether it transpiled, the execution judge's
            verdict, the candidate's own answer signature (executed against the
            arm's instance graph, never against the gold), tokens and cost.
            Appends to a JSONL file and resumes: a (case, sample) already in the
            file is not re-run.

``score``   No model calls. Sample ``j`` of every case is run ``j`` of the
            single-candidate baseline. A ``k``-candidate vote run takes ``k``
            disjoint samples per case (samples ``g*k .. g*k+k-1`` form run ``g``),
            so runs share no samples and their spread is honest. Reports the mean
            and standard deviation of the pass count over runs, per arm.

The vote itself (:func:`vote`) sees only the candidates — never the gold or the
verdicts — so it is the selection a deployed system could make.

Usage::

    .venv/bin/python tests/nl2sparql/eval/sample_vote.py sample \
        --config openai-gpt5-mini-ck25-grounded-generated-fewshot --n 10 \
        --out tests/nl2sparql/eval/reports/samples-gpt5mini-gf.jsonl
    .venv/bin/python tests/nl2sparql/eval/sample_vote.py score \
        --samples tests/nl2sparql/eval/reports/samples-gpt5mini-gf.jsonl --k 1 3 5

The API key is read from ``NL2SPARQL_API_KEY``, falling back to
``OPENAI_API_KEY`` (the runner's client reads only the former).
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# A candidate that failed to transpile, or that the engine rejected.
SIG_ERROR = "ERROR"
# A SELECT that returned no rows.
SIG_EMPTY = "EMPTY"

VOTE_RULES = ("first", "first-nonempty", "majority", "majority-nonempty")


@dataclass(frozen=True)
class Candidate:
    """What the vote may see about one sample: its answer signature, nothing else."""

    signature: str


def answer_signature(store: Any, sparql: str, aql: str, label_map: dict[str, str]) -> str:
    """The candidate's executed answer, normalized the way the execution judge does.

    Row keys go through the judge's own IRI→label normalization and are compared
    as a *set* (the judge's set relaxation), so two candidates that differ only in
    variable names, column order or duplicate rows share a signature. Extra
    projected columns still make a different signature — the judge's projection
    tolerance needs the gold's arity, which a selector does not have.
    """
    from tests.helpers.oxi import oxi_query
    from tests.nl2sparql.eval.runner import _execution_row_key

    if not aql or not sparql.strip():
        return SIG_ERROR
    try:
        result = oxi_query(store, sparql.replace("xsd:int(", "xsd:integer("))
    except (SyntaxError, RuntimeError, ValueError):
        return SIG_ERROR
    if result.kind == "ask":
        return f"ASK:{bool(result.boolean)}"
    keys = sorted({_execution_row_key(r, label_map) for r in result.rows or []})
    if not keys:
        return SIG_EMPTY
    return json.dumps(keys, ensure_ascii=False)


def vote(candidates: Sequence[Candidate], rule: str) -> int:
    """Index of the chosen candidate.

    ``first``              the first candidate (the single-candidate baseline).
    ``first-nonempty``     the first candidate that ran and returned something —
                           execute-and-discard without a vote; run sequentially,
                           it stops at that candidate (see :func:`candidates_drawn`).
                           Falls back to the first that ran, then to 0.
    ``majority``           the most common signature among candidates that ran;
                           ties go to the signature seen first. All errors → 0.
    ``majority-nonempty``  as ``majority``, but empty results are discarded too,
                           unless every candidate that ran was empty.
    The chosen index is the first candidate carrying the winning signature.
    """
    if not candidates:
        raise ValueError("vote needs at least one candidate")
    if rule not in VOTE_RULES:
        raise ValueError(f"unknown vote rule {rule!r}; expected one of {VOTE_RULES}")
    if rule == "first":
        return 0
    ran = [i for i, c in enumerate(candidates) if c.signature != SIG_ERROR]
    if not ran:
        return 0
    pool = ran
    if rule in ("first-nonempty", "majority-nonempty"):
        nonempty = [i for i in ran if candidates[i].signature != SIG_EMPTY]
        pool = nonempty or ran
    if rule == "first-nonempty":
        return pool[0]
    counts = Counter(candidates[i].signature for i in pool)
    best = max(counts.values())
    for i in pool:
        if counts[candidates[i].signature] == best:
            return i
    raise AssertionError("unreachable: pool is non-empty")


def candidates_drawn(candidates: Sequence[Candidate], rule: str) -> int:
    """How many candidates a sequential system would have had to generate.

    ``first`` needs one; the majority rules need all of them; ``first-nonempty``
    stops at the first candidate that returned something.
    """
    if rule == "first":
        return 1
    if rule == "first-nonempty":
        for i, c in enumerate(candidates):
            if c.signature not in (SIG_ERROR, SIG_EMPTY):
                return i + 1
    return len(candidates)


def run_groups(
    samples: Iterable[dict[str, Any]],
    k: int,
    *,
    exclude: frozenset[str] = frozenset(),
) -> list[dict[str, list[dict[str, Any]]]]:
    """Disjoint ``k``-sample groups: one ``{case: group}`` dict per run.

    Run ``g`` takes samples ``g*k .. g*k+k-1`` of every case. Every run covers
    the same cases, and the number of runs is set by the case with the fewest
    gap-free samples, so runs are comparable. Raises if no case has ``k``.
    """
    by_case: dict[str, dict[int, dict[str, Any]]] = {}
    for s in samples:
        if s["case"] in exclude:
            continue
        by_case.setdefault(s["case"], {})[s["sample"]] = s
    if not by_case:
        raise ValueError("no samples to score")
    depth = min(_contiguous(idx) for idx in by_case.values())
    runs = depth // k
    if runs == 0:
        raise ValueError(f"k={k} needs {k} samples of every case; the shallowest case has {depth}")
    return [
        {case: [per_case[g * k + j] for j in range(k)] for case, per_case in by_case.items()}
        for g in range(runs)
    ]


def score(
    samples: Iterable[dict[str, Any]],
    k: int,
    rule: str,
    *,
    exclude: frozenset[str] = frozenset(),
) -> list[int]:
    """Pass count of each disjoint ``k``-candidate run (see :func:`run_groups`)."""
    totals: list[int] = []
    for groups in run_groups(samples, k, exclude=exclude):
        passed = 0
        for group in groups.values():
            chosen = vote([Candidate(c["signature"]) for c in group], rule)
            passed += bool(group[chosen]["passed"])
        totals.append(passed)
    return totals


def _contiguous(indices: dict[int, Any]) -> int:
    """How many samples 0, 1, 2, … a case holds without a gap."""
    n = 0
    while n in indices:
        n += 1
    return n


# ---------------------------------------------------------------------------
# sample — the paid step
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _sample(args: argparse.Namespace) -> int:
    from tests.helpers.oxi import load_store_from_string
    from tests.nl2sparql.eval.runner import (
        _build_label_map,
        _client_for,
        _judge,
        build_arm,
    )

    if not os.getenv("NL2SPARQL_API_KEY") and os.getenv("OPENAI_API_KEY"):
        os.environ["NL2SPARQL_API_KEY"] = os.environ["OPENAI_API_KEY"]
    os.environ.setdefault("NL2SPARQL_TIMEOUT", str(args.timeout))
    os.chdir(REPO)

    arm = build_arm(args.config)
    if arm.data_ttl is None:
        raise SystemExit(f"config {args.config!r} has no instance graph; sampling needs execution")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {(s["case"], s["sample"]) for s in _read_jsonl(out)}
    cases = [c for c in arm.cases if not args.cases or c["name"] in args.cases]
    todo = [(c, j) for j in range(args.n) for c in cases if (c["name"], j) not in done]
    print(f"{args.config}: {len(cases)} cases x {args.n} samples; {len(done)} on file, {len(todo)} to run")

    local = threading.local()
    write_lock = threading.Lock()

    def _store() -> tuple[Any, dict[str, str]]:
        if not hasattr(local, "store"):
            local.store = load_store_from_string(arm.data_ttl)
            local.label_map = _build_label_map(local.store)
        return local.store, local.label_map

    def one(case: dict[str, Any], j: int) -> dict[str, Any]:
        store, label_map = _store()
        pipeline = arm.pipeline(case, _client_for(arm.config, case))
        t0 = time.perf_counter()
        outcome = pipeline.run(case["nl"], params=case.get("params"))
        elapsed_ms = (time.perf_counter() - t0) * 1000
        passed, note = _judge(arm.judge_name, case, outcome, arm.data_ttl)
        return {
            "config": args.config,
            "case": case["name"],
            "sample": j,
            "passed": bool(passed),
            "judge_note": note,
            "signature": answer_signature(store, outcome.sparql, outcome.aql, label_map),
            "transpiled": bool(outcome.aql),
            "repaired": outcome.repaired,
            "llm_calls": outcome.llm_calls,
            "prompt_tokens": sum(r.prompt_tokens for r in outcome.llm_call_records),
            "completion_tokens": sum(r.completion_tokens for r in outcome.llm_call_records),
            "cost_usd": outcome.cost_usd,
            "elapsed_ms": round(elapsed_ms),
            "sparql": outcome.sparql,
        }

    errors = 0
    cost = 0.0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(one, c, j): (c["name"], j) for c, j in todo}
        for n_done, fut in enumerate(as_completed(futures), 1):
            name, j = futures[fut]
            try:
                rec = fut.result()
            except Exception as exc:  # a failed sample is retried on the next resume
                errors += 1
                print(f"  {name}#{j} raised {type(exc).__name__}: {exc}", file=sys.stderr)
                continue
            cost += rec["cost_usd"]
            with write_lock, out.open("a") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if n_done % 25 == 0 or n_done == len(todo):
                print(f"  {n_done}/{len(todo)} done, ${cost:.3f} so far, {errors} raised")
    print(f"wrote {out}; this invocation ${cost:.3f}, {errors} samples raised (re-run to retry them)")
    return 1 if errors else 0


# ---------------------------------------------------------------------------
# score — no model calls
# ---------------------------------------------------------------------------


def _summary(totals: list[int], n_cases: int) -> dict[str, Any]:
    return {
        "runs": len(totals),
        "per_run": totals,
        "mean": round(statistics.fmean(totals), 2),
        "sd": round(statistics.stdev(totals), 2) if len(totals) > 1 else None,
        "cases": n_cases,
    }


def _score(args: argparse.Namespace) -> int:
    samples = _read_jsonl(Path(args.samples))
    exclude = frozenset(args.exclude or ())
    n_cases = len({s["case"] for s in samples} - exclude)
    kept = [s for s in samples if s["case"] not in exclude]
    cost_per_sample = statistics.fmean(s["cost_usd"] for s in kept)
    report: dict[str, Any] = {"samples": args.samples, "exclude": sorted(exclude), "arms": {}}
    per_case: dict[str, dict[str, Any]] = {}
    for k in args.k:
        for rule in ("first",) if k == 1 else ("first-nonempty", "majority", "majority-nonempty"):
            arm = f"k={k} {rule}"
            totals = score(samples, k, rule, exclude=exclude)
            groups = run_groups(samples, k, exclude=exclude)
            drawn = [
                candidates_drawn([Candidate(c["signature"]) for c in group], rule)
                for run in groups
                for group in run.values()
            ]
            summary = _summary(totals, n_cases)
            summary["candidates_per_question"] = round(statistics.fmean(drawn), 2)
            summary["est_cost_per_question_usd"] = round(statistics.fmean(drawn) * cost_per_sample, 5)
            report["arms"][arm] = summary
            for run in groups:
                for case, group in run.items():
                    chosen = vote([Candidate(c["signature"]) for c in group], rule)
                    row = per_case.setdefault(case, {})
                    row.setdefault(arm, []).append(bool(group[chosen]["passed"]))
    for case, row in per_case.items():
        mine = [s for s in kept if s["case"] == case]
        per_case[case] = {arm: round(sum(v) / len(v), 2) for arm, v in row.items()}
        per_case[case]["empty_share"] = round(sum(s["signature"] == SIG_EMPTY for s in mine) / len(mine), 2)
        per_case[case]["error_share"] = round(sum(s["signature"] == SIG_ERROR for s in mine) / len(mine), 2)
    report["per_case"] = dict(sorted(per_case.items(), key=lambda kv: (len(kv[0]), kv[0])))
    spend = sum(s["cost_usd"] for s in samples)
    report["sample_count"] = len(samples)
    report["cost_usd_total"] = round(spend, 4)
    report["llm_calls_total"] = sum(s["llm_calls"] for s in samples)
    text = json.dumps(report, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k != "per_case"}, indent=1))
    if args.out:
        Path(args.out).write_text(text + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sample", help="run each case N times and record every candidate (model calls)")
    p.add_argument("--config", required=True)
    p.add_argument("--n", type=int, required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--timeout", type=float, default=120.0, help="seconds per model call")
    p.add_argument("--cases", nargs="*", help="only these case names (smoke runs)")
    p.set_defaults(func=_sample)
    s = sub.add_parser("score", help="baseline and vote arms from recorded samples (no model calls)")
    s.add_argument("--samples", required=True)
    s.add_argument("--k", type=int, nargs="+", default=[1, 3, 5])
    s.add_argument("--exclude", nargs="*", help="case names to leave out (e.g. bank-overlap cases)")
    s.add_argument("--out")
    s.set_defaults(func=_score)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
