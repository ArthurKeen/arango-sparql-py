"""Execution feedback versus an independent retry, at an equal budget (lab/nl-loop, W1b).

W1a (``sample_vote.py``) showed that drawing another, independent translation
when one errors or returns no rows lifts CK25 from about 28.8 to 33.2 of 49 at
up to five candidates. This asks whether *telling* the model what went wrong —
"your query returned no rows" or "it failed with this error" — does better
than starting afresh, in particular on the questions that were empty on every
W1a sample.

How the feedback arm is built, so the comparison isolates the lever:

* **The same first candidate.** Every W1a sample whose candidate errored or
  returned nothing is the start of one feedback chain. The chain replays that
  recorded candidate as its first model reply (no call, no cost), so both arms
  share candidate 1 exactly and differ only in what follows it.
* **The engine's own seam carries the feedback.** :class:`ExecutionFeedback` is
  a postcondition on the shared engine: it executes each valid candidate
  against the instance graph (never the gold) and, when the result is empty or
  the execution fails, returns a violation whose reason and hint the engine
  puts into the next attempt's prompt. Its ``prompt_section`` is empty, so the
  system prompt — and so candidate 1's distribution — is W1a's.
* **An equal budget.** Up to five candidates in all (candidate 1 included) and
  three model calls per candidate after the first (W1a's one call plus two
  repairs), so at most twelve real calls follow the replayed candidate.
* **Selection outside the engine.** The engine fails closed when a
  postcondition still objects after the budget; an empty answer is sometimes
  right, so the chain takes the first candidate that returned rows, else the
  first that ran (W1a's ``first-nonempty`` rule).

Two steps, so the paid part runs once:

``sample``  One chain per W1a sample that came back empty or failed. Appends to
            a JSONL file and resumes; ``--budget-usd`` stops starting chains
            once that much has been spent.
``score``   No model calls. Per question, from all 25 W1a samples: the share
            that returned rows and passed (``p_np``), the share that came back
            empty or failed (``p_e``). The resample arm continues with up to
            four independent draws (probability ``p_np * (1 - p_e**4) /
            (1 - p_e)`` of passing); the feedback arm continues with the chain
            (its observed pass rate). Both arms' expected scores, the paired-
            by-question difference and its t, and the five disjoint runs.

Usage::

    .venv/bin/python tests/nl2sparql/eval/feedback_retry.py sample \\
        --samples tests/nl2sparql/eval/reports/w1a-samples-gpt5mini-gf.jsonl \\
        --out tests/nl2sparql/eval/reports/w1b-chains-gpt5mini-gf.jsonl --budget-usd 9
    .venv/bin/python tests/nl2sparql/eval/feedback_retry.py score \\
        --samples tests/nl2sparql/eval/reports/w1a-samples-gpt5mini-gf.jsonl \\
        --chains tests/nl2sparql/eval/reports/w1b-chains-gpt5mini-gf.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tests.nl2sparql.eval.sample_vote import (  # noqa: E402
    SIG_EMPTY,
    SIG_ERROR,
    _read_jsonl,
    executed_signature,
)

# The equal budget: W1a's k=5 drew up to five candidates, each one pipeline run
# of up to three calls (one translation plus ``max_repairs`` = 2).
CANDIDATE_BUDGET = 5
CALLS_PER_CANDIDATE = 3

# The feedback, domain-free: it names what the execution showed, never what the
# data contains.
EMPTY_REASON = "The query is valid but returned no rows when executed against the data."
EMPTY_HINT = (
    "Check that every literal value, label and language tag matches how the data actually "
    "stores it, that each property is used in the direction the schema declares, and that "
    "no filter or join is stricter than the question asks. If the question genuinely has "
    "no answer, return the same query."
)
ERROR_REASON = "The query is valid but failed when executed: {error}"
ERROR_HINT = "Rewrite it so it executes, keeping its meaning."


def _bad(signature: str) -> bool:
    return signature in (SIG_ERROR, SIG_EMPTY)


@dataclass
class ExecutionFeedback:
    """Postcondition: a candidate must execute and return something.

    Records every candidate it is shown (``executed``) so the caller can select
    among them. After ``cap`` executed candidates it accepts whatever it sees, so
    the engine stops drawing; the caller still selects by :func:`select`.
    """

    store: Any
    label_map: dict[str, str]
    cap: int
    code: str = "execution_feedback"
    executed: list[dict[str, Any]] = field(default_factory=list)

    def check(self, query: str, *, context: Any) -> Any:
        from arango_query_core.nl.postconditions import PostconditionViolation

        signature, error = executed_signature(self.store, query, self.label_map)
        self.executed.append(
            {"sparql": query, "signature": signature, "error": error, "attempt": context.attempt}
        )
        if not _bad(signature) or len(self.executed) >= self.cap:
            return None
        if signature == SIG_EMPTY:
            return PostconditionViolation(
                reason=EMPTY_REASON, suggested_hint=EMPTY_HINT, code="execution_empty"
            )
        return PostconditionViolation(
            reason=ERROR_REASON.format(error=error), suggested_hint=ERROR_HINT, code="execution_error"
        )

    def prompt_section(self) -> str:
        # Empty on purpose: the system prompt stays W1a's, so the replayed first
        # candidate is a draw from the same distribution as the resample arm's.
        return ""


class ReplayFirstClient:
    """An ``LLMClient`` whose first reply is a recorded query (no call, no cost);
    every later call goes to ``inner``."""

    def __init__(self, inner: Any, first_sparql: str) -> None:
        self.inner = inner
        self.provider = getattr(inner, "provider", "unknown")
        self.model = getattr(inner, "model", "unknown")
        self._first: str | None = first_sparql

    def generate(self, messages: list[dict[str, str]]) -> Any:
        from arango_sparql.nl2sparql import LLMResponse

        if self._first is not None:
            sparql, self._first = self._first, None
            return LLMResponse(
                content=f"```sparql\n{sparql.strip()}\n```",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
            )
        return self.inner.generate(messages)


def chain_budget(replayed: bool) -> tuple[int, int]:
    """``(max_repairs, executed-candidate cap)`` for one chain.

    A replayed base occupies attempt 0 and is one of the chain's executed
    candidates. A base that never produced a query (nothing to replay) still
    counts against the five candidates, so its chain may execute only four.
    """
    real_calls = (CANDIDATE_BUDGET - 1) * CALLS_PER_CANDIDATE
    if replayed:
        return real_calls, CANDIDATE_BUDGET
    return real_calls - 1, CANDIDATE_BUDGET - 1


def apply_budget(pipeline: Any, max_repairs: int) -> Any:
    """Give ``pipeline`` exactly ``max_repairs`` retries.

    ``RepairLoop`` clamps a requested budget at the product ceiling
    (``MAX_REPAIRS_CEILING`` = 5), which would hold a chain to five real calls
    against the resample arm's twelve. The experiment needs the equal budget,
    so it sets the loop's public attribute after construction.
    """
    pipeline.repair_loop.max_repairs = max_repairs
    return pipeline


def select(executed: Sequence[dict[str, Any]]) -> int | None:
    """Index of the candidate to answer with: the first that returned something,
    else the first that ran (an empty answer), else ``None``."""
    ran = [i for i, c in enumerate(executed) if c["signature"] != SIG_ERROR]
    answered = [i for i in ran if executed[i]["signature"] != SIG_EMPTY]
    if answered:
        return answered[0]
    return ran[0] if ran else None


# ---------------------------------------------------------------------------
# sample — the paid step
# ---------------------------------------------------------------------------


def _sample(args: argparse.Namespace) -> int:
    from arango_sparql.api import translate
    from arango_sparql.errors import SparqlError
    from arango_sparql.nl2sparql.models import PipelineOutcome
    from tests.helpers.oxi import load_store_from_string
    from tests.nl2sparql.eval.runner import _build_label_map, _client_for, _judge, build_arm

    if not os.getenv("NL2SPARQL_API_KEY") and os.getenv("OPENAI_API_KEY"):
        os.environ["NL2SPARQL_API_KEY"] = os.environ["OPENAI_API_KEY"]
    os.environ.setdefault("NL2SPARQL_TIMEOUT", str(args.timeout))
    os.chdir(REPO)

    bases = [s for s in _read_jsonl(Path(args.samples)) if _bad(s["signature"])]
    if args.cases:
        bases = [s for s in bases if s["case"] in args.cases]
    configs = {s["config"] for s in bases}
    if len(configs) != 1:
        raise SystemExit(f"expected one config in {args.samples}, found {sorted(configs)}")
    arm = build_arm(configs.pop())
    if arm.data_ttl is None:
        raise SystemExit("the arm has no instance graph; feedback needs execution")
    cases = {c["name"]: c for c in arm.cases}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {(r["case"], r["sample"]) for r in _read_jsonl(out)}
    todo = [b for b in bases if (b["case"], b["sample"]) not in done]
    print(f"{arm.name}: {len(bases)} empty-or-failed bases; {len(done)} chains on file, {len(todo)} to run")

    local = threading.local()
    lock = threading.Lock()
    spent = [0.0]

    def _store() -> tuple[Any, dict[str, str]]:
        if not hasattr(local, "store"):
            local.store = load_store_from_string(arm.data_ttl)
            local.label_map = _build_label_map(local.store)
        return local.store, local.label_map

    def one(base: dict[str, Any]) -> dict[str, Any] | None:
        with lock:
            if spent[0] >= args.budget_usd:
                return None
        case = cases[base["case"]]
        store, label_map = _store()
        replayed = bool(base["transpiled"] and base["sparql"].strip())
        max_repairs, cap = chain_budget(replayed)
        check = ExecutionFeedback(store=store, label_map=label_map, cap=cap)
        client = _client_for(arm.config, case)
        if replayed:
            client = ReplayFirstClient(client, base["sparql"])
        pipeline = apply_budget(arm.pipeline(case, client, postconditions=[check]), max_repairs)
        t0 = time.perf_counter()
        outcome = pipeline.run(case["nl"], params=case.get("params"))
        elapsed_ms = (time.perf_counter() - t0) * 1000
        chosen = select(check.executed)
        # upstream: drafts/query-core-execution-check.md (lab ticket draft, not filed) — remove when the engine returns the first candidate that ran instead of failing closed
        if chosen is None:
            passed, note, chosen_sparql = False, "no candidate executed", ""
        else:
            chosen_sparql = check.executed[chosen]["sparql"]
            if outcome.aql and outcome.sparql == chosen_sparql:
                judged = outcome
            else:
                try:
                    tr = translate(chosen_sparql, resolver=pipeline.resolver, params=case.get("params"))
                    aql, bind_vars = tr.aql, dict(tr.bind_vars)
                except (SparqlError, ValueError):
                    aql, bind_vars = "", {}
                judged = PipelineOutcome(
                    nl=case["nl"],
                    sparql=chosen_sparql,
                    aql=aql,
                    bind_vars=bind_vars,
                    warnings=[],
                    latency_ms=0,
                    repaired=False,
                )
            passed, note = _judge(arm.judge_name, case, judged, arm.data_ttl)
        real_calls = outcome.llm_calls - (1 if replayed else 0)
        cost = outcome.cost_usd
        with lock:
            spent[0] += cost
        return {
            "config": arm.name,
            "case": case["name"],
            "sample": base["sample"],
            "replayed": replayed,
            "engine_ok": bool(outcome.aql),
            # Why the engine stopped when it did (budget, transport, guardrail).
            "warnings": [f"{w.get('code')}: {str(w.get('message', ''))[:300]}" for w in outcome.warnings],
            "candidates": [
                {"signature": c["signature"][:200], "error": c["error"], "attempt": c["attempt"]}
                for c in check.executed
            ],
            "chosen": chosen,
            "chosen_signature": None if chosen is None else check.executed[chosen]["signature"][:200],
            "passed": bool(passed),
            "judge_note": note,
            "real_calls": real_calls,
            "cost_usd": cost,
            "base_calls": base["llm_calls"],
            "base_cost_usd": base["cost_usd"],
            "elapsed_ms": round(elapsed_ms),
            "chosen_sparql": chosen_sparql,
            "sparqls": [c["sparql"] for c in check.executed],
        }

    errors = skipped = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(one, b): (b["case"], b["sample"]) for b in todo}
        for n_done, fut in enumerate(as_completed(futures), 1):
            name, j = futures[fut]
            try:
                rec = fut.result()
            except Exception as exc:  # a failed chain is retried on the next resume
                errors += 1
                print(f"  {name}#{j} raised {type(exc).__name__}: {exc}", file=sys.stderr)
                continue
            if rec is None:
                skipped += 1
                continue
            with lock, out.open("a") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if n_done % 25 == 0 or n_done == len(todo):
                print(f"  {n_done}/{len(todo)} done, ${spent[0]:.3f} so far, {errors} raised")
    stopped = f" (spend stop ${args.budget_usd:.2f} reached)" if skipped else ""
    print(
        f"wrote {out}; this invocation ${spent[0]:.3f}, {errors} chains raised, "
        f"{skipped} not started{stopped}"
    )
    return 1 if errors or skipped else 0


# ---------------------------------------------------------------------------
# score — no model calls
# ---------------------------------------------------------------------------


def resample_continuation(p_np: float, p_e: float, draws: int) -> float:
    """Probability that up to ``draws`` independent candidates, drawn after a
    failed first one, find a passing answer under ``first-nonempty``: the first
    draw that returns something is taken, so it is ``p_np`` times the chance that
    every earlier draw came back empty or failed."""
    if not 0.0 <= p_e <= 1.0 or not 0.0 <= p_np <= 1.0 - p_e + 1e-12:
        raise ValueError(f"need 0 <= p_np <= 1 - p_e <= 1; got p_np={p_np}, p_e={p_e}")
    return p_np * sum(p_e**i for i in range(draws))


def per_question(
    samples: Sequence[dict[str, Any]], chains: Sequence[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Per question: shares from all samples, both arms' pass probabilities.

    A question with empty-or-failed samples must have a chain for every one of
    them; otherwise its feedback rate would be estimated from a subset chosen by
    which chains happened to finish, so this raises.
    """
    by_case: dict[str, list[dict[str, Any]]] = {}
    for s in samples:
        by_case.setdefault(s["case"], []).append(s)
    chains_by: dict[tuple[str, int], dict[str, Any]] = {(c["case"], c["sample"]): c for c in chains}
    rows: dict[str, dict[str, Any]] = {}
    for case, mine in by_case.items():
        n = len(mine)
        bad = [s for s in mine if _bad(s["signature"])]
        if any(s["passed"] for s in bad):
            raise ValueError(
                f"{case}: an empty or failed sample passed; the continuation model assumes none do"
            )
        p_e = len(bad) / n
        p_np = sum(s["passed"] for s in mine if not _bad(s["signature"])) / n
        missing = [s["sample"] for s in bad if (case, s["sample"]) not in chains_by]
        if missing:
            raise ValueError(f"{case}: no chain for empty-or-failed samples {missing}")
        mine_chains = [chains_by[(case, s["sample"])] for s in bad]
        c_fb = statistics.fmean(c["passed"] for c in mine_chains) if mine_chains else 0.0
        c_rs = resample_continuation(p_np, p_e, CANDIDATE_BUDGET - 1)
        rows[case] = {
            "samples": n,
            "p_e": round(p_e, 4),
            "p_np": round(p_np, 4),
            "chains": len(mine_chains),
            "chain_pass": round(c_fb, 4),
            "resample_continuation_pass": round(c_rs, 4),
            "resample": p_np + p_e * c_rs,
            "feedback": p_np + p_e * c_fb,
        }
    return rows


def _expected_calls(samples: Sequence[dict[str, Any]], chains: Sequence[dict[str, Any]]) -> dict[str, float]:
    """Mean model calls and dollars per question for each arm, over every base.

    The resample arm's continuation is costed at the mean sample's calls and
    cost, times the expected number of further draws (one, plus one more for
    each draw that also comes back empty or failed, up to four).
    """
    by_case: dict[str, list[dict[str, Any]]] = {}
    for s in samples:
        by_case.setdefault(s["case"], []).append(s)
    chains_by = {(c["case"], c["sample"]): c for c in chains}
    rs_calls, rs_cost, fb_calls, fb_cost = [], [], [], []
    for case, mine in by_case.items():
        p_e = sum(_bad(s["signature"]) for s in mine) / len(mine)
        mean_calls = statistics.fmean(s["llm_calls"] for s in mine)
        mean_cost = statistics.fmean(s["cost_usd"] for s in mine)
        further = sum(p_e**i for i in range(CANDIDATE_BUDGET - 1))
        for s in mine:
            extra_rs = (further * mean_calls, further * mean_cost) if _bad(s["signature"]) else (0.0, 0.0)
            rs_calls.append(s["llm_calls"] + extra_rs[0])
            rs_cost.append(s["cost_usd"] + extra_rs[1])
            chain = chains_by.get((case, s["sample"])) if _bad(s["signature"]) else None
            fb_calls.append(s["llm_calls"] + (chain["real_calls"] if chain else 0))
            fb_cost.append(s["cost_usd"] + (chain["cost_usd"] if chain else 0.0))
    return {
        "resample_calls_per_question": round(statistics.fmean(rs_calls), 3),
        "resample_usd_per_question": round(statistics.fmean(rs_cost), 5),
        "feedback_calls_per_question": round(statistics.fmean(fb_calls), 3),
        "feedback_usd_per_question": round(statistics.fmean(fb_cost), 5),
    }


def disjoint_runs(
    samples: Sequence[dict[str, Any]], chains: Sequence[dict[str, Any]], k: int = CANDIDATE_BUDGET
) -> list[dict[str, int]]:
    """The five literal runs, paired: run ``g`` starts every question at sample
    ``g*k``; the resample arm continues with samples ``g*k+1 .. g*k+k-1``
    (``first-nonempty``), the feedback arm with that base's chain."""
    from tests.nl2sparql.eval.sample_vote import Candidate, run_groups, vote

    chains_by = {(c["case"], c["sample"]): c for c in chains}
    runs = []
    for groups in run_groups(samples, k):
        rs = fb = 0
        for case, group in groups.items():
            rs += bool(group[vote([Candidate(c["signature"]) for c in group], "first-nonempty")]["passed"])
            base = group[0]
            if _bad(base["signature"]):
                fb += bool(chains_by[(case, base["sample"])]["passed"])
            else:
                fb += bool(base["passed"])
        runs.append({"resample": rs, "feedback": fb})
    return runs


def compare(rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Both arms' expected scores, a fresh run's SD for each (independent
    questions), and the paired-by-question difference with its t."""
    rs = [r["resample"] for r in rows.values()]
    fb = [r["feedback"] for r in rows.values()]
    diffs = [f - r for f, r in zip(fb, rs, strict=True)]
    n = len(diffs)
    sd_d = statistics.stdev(diffs) if n > 1 else 0.0
    t = (statistics.fmean(diffs) / (sd_d / math.sqrt(n))) if sd_d > 0 else None
    return {
        "questions": n,
        "resample_expected": round(sum(rs), 2),
        "resample_fresh_run_sd": round(math.sqrt(sum(p * (1 - p) for p in rs)), 2),
        "feedback_expected": round(sum(fb), 2),
        "feedback_fresh_run_sd": round(math.sqrt(sum(p * (1 - p) for p in fb)), 2),
        "difference": round(sum(diffs), 2),
        "paired_t": None if t is None else round(t, 2),
        "questions_up": sum(d > 1e-9 for d in diffs),
        "questions_down": sum(d < -1e-9 for d in diffs),
        "questions_level": sum(abs(d) <= 1e-9 for d in diffs),
    }


def _score(args: argparse.Namespace) -> int:
    samples = _read_jsonl(Path(args.samples))
    chains = _read_jsonl(Path(args.chains))
    report: dict[str, Any] = {"samples": args.samples, "chains": args.chains, "views": {}}
    for label, exclude in (("all", frozenset()), ("no-overlap", frozenset(args.exclude or ()))):
        if label == "no-overlap" and not exclude:
            continue
        kept_s = [s for s in samples if s["case"] not in exclude]
        kept_c = [c for c in chains if c["case"] not in exclude]
        rows = per_question(kept_s, kept_c)
        view = compare(rows)
        view["exclude"] = sorted(exclude)
        view["disjoint_runs"] = disjoint_runs(kept_s, kept_c)
        view.update(_expected_calls(kept_s, kept_c))
        report["views"][label] = view
        if label == "all":
            report["per_case"] = {
                case: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()}
                for case, row in sorted(rows.items(), key=lambda kv: (len(kv[0]), kv[0]))
                if row["p_e"] > 0
            }
    report["chain_count"] = len(chains)
    report["chain_cost_usd"] = round(sum(c["cost_usd"] for c in chains), 4)
    report["chain_real_calls"] = sum(c["real_calls"] for c in chains)
    report["chains_engine_ok"] = sum(c["engine_ok"] for c in chains)
    print(json.dumps({k: v for k, v in report.items() if k != "per_case"}, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sample", help="one feedback chain per empty-or-failed W1a sample (model calls)")
    p.add_argument("--samples", required=True, help="W1a's sample_vote.py JSONL")
    p.add_argument("--out", required=True)
    p.add_argument("--budget-usd", type=float, required=True, help="stop starting chains past this spend")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--timeout", type=float, default=120.0, help="seconds per model call")
    p.add_argument("--cases", nargs="*", help="only these case names (smoke runs)")
    p.set_defaults(func=_sample)
    s = sub.add_parser("score", help="both arms from W1a's samples and the chains (no model calls)")
    s.add_argument("--samples", required=True)
    s.add_argument("--chains", required=True)
    s.add_argument("--exclude", nargs="*", help="case names for a second view without them (bank overlap)")
    s.add_argument("--out")
    s.set_defaults(func=_score)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
