"""Render a :mod:`tests.ldbc.bench` results file as Markdown.

Every number in the output comes from the results JSON; nothing is typed
by hand, so the prose cannot drift from the measurement. Timings are shown
next to the cell's correctness outcome: a fast wrong answer is not a result.

Usage::

    python -m tests.ldbc.report RESULTS.json [--out REPORT.md]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tests.ldbc.loader import LAYOUTS

DISCLAIMER = (
    "These are not LDBC Benchmark Results. This report uses the LDBC Social Network Benchmark "
    "(SNB) Interactive v1 data and the reference SPARQL queries of "
    "`ldbc/ldbc_snb_interactive_v1_impls` (Apache-2.0) as a validation and performance corpus for "
    "arango-sparql-py. It is not an implementation of an LDBC Benchmark and has not been audited."
)

LAYOUT_NAMES = {"rpt": "RDF (triples)", "lpg": "LPG", "pg": "PG"}

#: One symbol per final state, for the grid.
SYMBOL = {
    "matches_oracle": "✅ correct",
    "translates_wrong": "❌ wrong answer",
    "oracle_unavailable": "⚪ runs, unchecked",
    "execution_error": "❌ AQL error",
    "timeout": "⏱ timeout",
    "resource_limit": "⏱ memory limit",
    "fails_closed": "⛔ refused",
    "parse_error": "⛔ parse error",
    "out_of_subset": "— not standard",
    "crash": "❌ crash",
}


def short_op(op: str) -> str:
    kind, number = re.fullmatch(r"interactive-(short|complex)-(\d+)", op).groups()  # type: ignore[union-attr]
    return f"{'IS' if kind == 'short' else 'IC'}{number}"


def final_state(cell: dict[str, Any]) -> str:
    if cell["translation"] != "translates":
        return cell["translation"]
    if cell["execution"] and cell["execution"] != "executes":
        return cell["execution"]
    return cell["oracle"] or "oracle_unavailable"


def _ms(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    ms = seconds * 1000
    return f"{ms:,.0f}" if ms >= 100 else f"{ms:.1f}"


def _reason(detail: str) -> str:
    """A failure detail without the bits that differ per query (names, IRIs)."""
    text = re.sub(r"'[^']*'", "'…'", detail)
    text = re.sub(r"\?\w+", "?…", text)
    return text.split(" (", 1)[0][:140]


def render(data: dict[str, Any]) -> str:
    env = data["environment"]
    cells = {(c["op"], c["form"], c["layout"]): c for c in data["ledger"]}
    entries = {(e["op"], e["form"], e["layout"]): e for e in data["entries"]}
    texts = list(dict.fromkeys((c["op"], c["form"]) for c in data["ledger"]))

    out: list[str] = []
    out.append(f"# LDBC SNB Interactive v1 on arango-sparql-py — SF{env['scale_factor']}")
    out.append("")
    out.append(f"> {DISCLAIMER}")
    out.append("")
    out.append(
        f"Generated {env['generated_at']} from git `{env['git_sha'][:12]}` on ArangoDB "
        f"{env['arangodb_version']} ({env['arangodb_engine']}), {env['platform']}, "
        f"{env['cpu_count']} CPUs. Data digest `{env['data_sha256'][:12]}`; "
        f"{env['warmup']} warm-up and {env['repeats']} timed runs per binding; "
        f"query limits {env['aql_max_runtime_s']:.0f} s and {env['aql_memory_limit'] // (1 << 20)} MiB."
    )
    out.append("")

    # -- Outcome summary ----------------------------------------------------
    out.append("## Outcomes per layout")
    out.append("")
    out.append(f"{len(texts)} query texts (21 verbatim reads + labelled standard-SPARQL variants).")
    out.append("")
    states = [s for s in SYMBOL if any(final_state(c) == s for c in cells.values())]
    out.append("| Layout | " + " | ".join(SYMBOL[s] for s in states) + " |")
    out.append("| --- | " + " | ".join("---:" for _ in states) + " |")
    for layout in LAYOUTS:
        counts = Counter(final_state(c) for c in cells.values() if c["layout"] == layout)
        out.append(f"| {LAYOUT_NAMES[layout]} | " + " | ".join(str(counts.get(s, 0)) for s in states) + " |")
    out.append("")

    # -- Per query ------------------------------------------------------------
    out.append("## Per query")
    out.append("")
    out.append(
        "AQL time is the median server-side execution time (ms) over the real bindings; "
        "pyoxigraph's time for the same text is shown for scale (indicative only)."
    )
    out.append("")
    header = (
        "| Query | Text | "
        + " | ".join(f"{LAYOUT_NAMES[layout]}" for layout in LAYOUTS)
        + " | pyoxigraph ms |"
    )
    out.append(header)
    out.append("| --- | --- | " + " | ".join("---" for _ in LAYOUTS) + " | ---: |")
    for op, form in texts:
        row = [short_op(op), form]
        oracle_times: list[float] = []
        for layout in LAYOUTS:
            cell = cells.get((op, form, layout))
            if cell is None:
                row.append("")
                continue
            state = final_state(cell)
            text = SYMBOL.get(state, state)
            entry = entries.get((op, form, layout))
            if entry and entry["server"].get("n"):
                text += f" · {_ms(entry['server']['p50'])} ms"
                oracle_times.extend(entry.get("oracle_seconds", []))
            row.append(text)
        row.append(_ms(sorted(oracle_times)[len(oracle_times) // 2]) if oracle_times else "—")
        out.append("| " + " | ".join(row) + " |")
    out.append("")

    # -- Performance detail ---------------------------------------------------
    timed = [e for e in data["entries"] if e["server"].get("n")]
    if timed:
        out.append("## Timings")
        out.append("")
        out.append(
            "| Query | Text | Layout | Outcome | Translate warm ms | AQL p50 ms | AQL p95 ms | Rows | Full scans |"
        )
        out.append("| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |")
        for e in sorted(timed, key=lambda e: -e["server"]["p50"]):
            cell = cells.get((e["op"], e["form"], e["layout"]), {})
            out.append(
                f"| {short_op(e['op'])} | {e['form']} | {LAYOUT_NAMES[e['layout']]} | "
                f"{SYMBOL.get(final_state(cell), '') if cell else ''} | "
                f"{_ms(e['translate_warm'].get('p50'))} | {_ms(e['server']['p50'])} | "
                f"{_ms(e['server']['p95'])} | {', '.join(str(r) for r in e['rows'])} | "
                f"{e['plan'].get('full_scans', '—')} |"
            )
        out.append("")

    # -- Why things fail ------------------------------------------------------
    out.append("## Why queries do not run")
    out.append("")
    reasons: dict[str, set[str]] = defaultdict(set)
    for (op, _form, layout), cell in cells.items():
        state = final_state(cell)
        if state in ("matches_oracle", "oracle_unavailable"):
            continue
        detail = cell["translation_detail"] or cell["live_detail"]
        reasons[f"{SYMBOL.get(state, state)}: {_reason(detail)}"].add(
            f"{short_op(op)}/{LAYOUT_NAMES[layout]}"
        )
    out.append("| Reason | Cells |")
    out.append("| --- | --- |")
    for reason, where in sorted(reasons.items(), key=lambda kv: -len(kv[1])):
        out.append(
            f"| {reason} | {len(where)}: {', '.join(sorted(where)[:8])}{' …' if len(where) > 8 else ''} |"
        )
    out.append("")
    return "\n".join(out)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def render_comparison(before: dict[str, Any], after: dict[str, Any], *, title: str) -> str:
    """A before/after timing table from two ``ledger --live`` timing files.

    One run per binding, no warm-up (what the live run measures): good for
    an order-of-magnitude change, not for small differences. Only cells
    present in both runs are listed. The outcome shown is the *after* run's:
    a before run's correctness verdict reflects the comparator of its day
    (several early "wrong answer" verdicts were comparator defects, plan
    §3.3), so it is not repeated here.
    """

    def cells(data: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
        return {(c["op"], c["form"], c["layout"]): c for c in data["cells"]}

    old, new = cells(before), cells(after)
    out = [
        f"## {title}",
        "",
        "Median server-side AQL time over the real bindings, one run each. The outcome is the "
        "current run's; the earlier run's correctness verdicts used an older comparator and are "
        "not repeated.",
        "",
    ]
    out.append("| Query | Text | Layout | Before ms | After ms | Speed-up | Outcome now |")
    out.append("| --- | --- | --- | ---: | ---: | ---: | --- |")
    for key in sorted(set(old) & set(new), key=lambda k: (k[2], short_op(k[0]), k[1])):
        a, b = old[key], new[key]
        t_a = _median(a.get("aql_seconds", [])[:-1] or a.get("aql_seconds", []))
        t_b = _median(b.get("aql_seconds", [])[:-1] or b.get("aql_seconds", []))
        if t_a is None and t_b is None:
            continue
        speedup = f"{t_a / t_b:,.0f}×" if t_a and t_b else "—"
        state = final_state(b)
        out.append(
            f"| {short_op(key[0])} | {key[1]} | {LAYOUT_NAMES[key[2]]} | {_ms(t_a)} | {_ms(t_b)} | {speedup} | "
            f"{SYMBOL.get(state, state)} |"
        )
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("results", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--compare",
        nargs=2,
        type=Path,
        metavar=("BEFORE", "AFTER"),
        help="also append a before/after table from two `ledger --live` timing files",
    )
    parser.add_argument("--compare-title", default="Before and after")
    args = parser.parse_args(argv)
    text = render(json.loads(args.results.read_text()))
    if args.compare:
        before, after = (json.loads(path.read_text()) for path in args.compare)
        text += "\n" + render_comparison(before, after, title=args.compare_title)
    out = args.out or args.results.with_suffix(".md")
    out.write_text(text)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
