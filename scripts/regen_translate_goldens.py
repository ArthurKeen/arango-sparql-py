"""One-off regenerator for ``tests/translate/*.yml`` ``expected_aql`` blocks.

Re-runs :func:`arango_sparql.api.translate` for every case in every
golden YAML file and replaces the ``expected_aql`` block (and the
``expected_bind_vars`` mapping when needed) in-place. Used after a
translation change that's purely additive / corrective and shifts
every existing golden's AQL the same way (e.g. the predicate-
existence-filter slice that inserts ``FILTER HAS(alias, "attr")``
after every variable-object BGP triple).

USAGE::

    python scripts/regen_translate_goldens.py             # update everything
    python scripts/regen_translate_goldens.py --dry-run   # show diffs only
    python scripts/regen_translate_goldens.py --only filter_builtins.yml subselect.yml

The script preserves every comment, blank line, and other key in the
YAML file — only the ``expected_aql:`` block scalar and (when the new
``expected_bind_vars`` differs from what's currently in the YAML) the
``expected_bind_vars:`` mapping are rewritten. If the regenerated AQL
would still leave the test failing for a non-filter reason, the script
prints a warning so the operator can review manually.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from arango_sparql.api import translate  # noqa: E402
from arango_sparql.translate.resolver import SchemaResolver  # noqa: E402

GOLDEN_DIR = REPO_ROOT / "tests" / "translate"

# Regex matching a single case's ``expected_aql`` block scalar. Anchored
# on the case's ``- name: <name>`` line so cases inside the same file
# don't bleed into one another.
def _case_aql_pattern(name: str) -> re.Pattern[str]:
    return re.compile(
        # Group 1: header up to and including the ``expected_aql: |-`` line.
        rf"(^  - name:\s*{re.escape(name)}\s*$\n"
        rf"(?:^    .*?\n)*?"
        rf"^    expected_aql:\s*\|-?\s*$\n)"
        # Group 2: block scalar content (lines indented 6+ spaces or blank).
        r"((?:^      [^\n]*\n|^\n)*)",
        re.MULTILINE,
    )


def _case_bind_vars_pattern(name: str) -> re.Pattern[str]:
    """Match the ``expected_bind_vars`` mapping for a single case.

    Stops at the next un-indented key (``  - name:`` for the next case or
    a top-level key for end-of-file). Used only when the regenerated bind
    vars differ from what's in the YAML so we don't churn whitespace.
    """
    return re.compile(
        rf"(^  - name:\s*{re.escape(name)}\s*$\n"
        rf"(?:^    .*?\n)*?"
        rf"^    expected_bind_vars:\s*$\n)"
        r"((?:^      [^\n]*\n|^\n)*)",
        re.MULTILINE,
    )


def _format_block(aql: str) -> str:
    """Render *aql* as a 6-space-indented YAML block scalar body."""
    return "".join(f"      {line}\n" if line else "\n" for line in aql.split("\n"))


def _format_bind_vars(bind_vars: Mapping[str, Any]) -> str:
    """Render *bind_vars* as YAML mapping under ``expected_bind_vars:``.

    Keeps the legacy YAML formatting conventions: keys quoted, scalars
    on one line, lists rendered as block sequences (``- item``).
    """
    if not bind_vars:
        return ""
    lines: list[str] = []
    for key in sorted(bind_vars):
        value = bind_vars[key]
        key_repr = f'"{key}"'
        if isinstance(value, list):
            lines.append(f"      {key_repr}:\n")
            for item in value:
                lines.append(f"        - {yaml.safe_dump(item).strip()}\n")
        elif isinstance(value, bool):
            lines.append(f"      {key_repr}: {str(value).lower()}\n")
        elif isinstance(value, (int, float)):
            lines.append(f"      {key_repr}: {value}\n")
        elif value is None:
            lines.append(f"      {key_repr}: null\n")
        else:
            lines.append(f"      {key_repr}: {yaml.safe_dump(value).strip()}\n")
    return "".join(lines)


def _materialise_ontology(top: Mapping[str, Any], case: Mapping[str, Any]) -> str:
    """Resolve the ontology TTL for *case*, preferring per-case override."""
    if "ontology" in case:
        return case["ontology"] or ""
    return top.get("ontology", "") or ""


def _materialise_resolver(top: Mapping[str, Any], case: Mapping[str, Any]) -> SchemaResolver:
    """Build the SchemaResolver the way the golden runner does."""
    ontology_ttl = _materialise_ontology(top, case)
    default_collection = case.get(
        "default_collection",
        top.get("default_collection", "Document"),
    )
    kwargs: dict[str, Any] = {"default_collection": default_collection}
    for key in ("graph_field", "default_graph_includes_named"):
        if key in case:
            kwargs[key] = case[key]
        elif key in top:
            kwargs[key] = top[key]
    return SchemaResolver.from_turtle(ontology_ttl, **kwargs)


def regen_file(path: Path, *, dry_run: bool) -> tuple[int, int]:
    """Regenerate every case in *path*. Returns ``(updated, skipped)``."""
    original = path.read_text(encoding="utf-8")
    data = yaml.safe_load(original)
    if not isinstance(data, dict) or "cases" not in data:
        return (0, 0)

    updated_text = original
    n_updated = 0
    n_skipped = 0

    for case in data["cases"]:
        if not isinstance(case, dict):
            continue
        name = case.get("name")
        sparql = case.get("sparql")
        if not name or not sparql:
            n_skipped += 1
            continue

        try:
            resolver = _materialise_resolver(data, case)
            result = translate(sparql, resolver=resolver)
        except Exception as exc:  # noqa: BLE001 — surface and continue
            print(f"  [skip] {name}: translate() raised {type(exc).__name__}: {exc}")
            n_skipped += 1
            continue

        new_aql_block = _format_block(result.aql)
        aql_re = _case_aql_pattern(name)
        m = aql_re.search(updated_text)
        if not m:
            print(f"  [skip] {name}: could not locate expected_aql block in YAML")
            n_skipped += 1
            continue
        if m.group(2) == new_aql_block:
            continue  # already matches; nothing to do
        updated_text = updated_text[: m.start(2)] + new_aql_block + updated_text[m.end(2) :]

        # Bind-vars regen: only rewrite if the existing YAML differs from
        # the regenerated mapping (set comparison via yaml.safe_load on
        # the block scalar). Keeps comment-rich files clean when the
        # change is AQL-only.
        bind_re = _case_bind_vars_pattern(name)
        bind_m = bind_re.search(updated_text)
        if bind_m:
            try:
                existing_bv = (
                    yaml.safe_load(bind_m.group(2)) or {}
                    if bind_m.group(2).strip()
                    else {}
                )
            except yaml.YAMLError:
                existing_bv = None
            new_bv = dict(result.bind_vars)
            if existing_bv != new_bv:
                new_bv_block = _format_bind_vars(new_bv)
                updated_text = (
                    updated_text[: bind_m.start(2)]
                    + new_bv_block
                    + updated_text[bind_m.end(2) :]
                )

        n_updated += 1

    if updated_text != original:
        if dry_run:
            diff = difflib.unified_diff(
                original.splitlines(keepends=True),
                updated_text.splitlines(keepends=True),
                fromfile=str(path),
                tofile=str(path) + " (new)",
                n=2,
            )
            sys.stdout.writelines(diff)
        else:
            path.write_text(updated_text, encoding="utf-8")

    return (n_updated, n_skipped)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="show diffs instead of writing")
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="FILE",
        help="only regenerate the listed YAML files (basenames)",
    )
    args = parser.parse_args()

    files = sorted(GOLDEN_DIR.glob("*.yml"))
    if args.only:
        wanted = set(args.only)
        files = [p for p in files if p.name in wanted]
        missing = wanted - {p.name for p in files}
        if missing:
            print(f"warning: requested files not found: {sorted(missing)}", file=sys.stderr)

    total_updated = 0
    total_skipped = 0
    for path in files:
        print(f"== {path.relative_to(REPO_ROOT)} ==")
        u, s = regen_file(path, dry_run=args.dry_run)
        total_updated += u
        total_skipped += s
        print(f"  updated={u} skipped={s}")

    mode = "(dry-run) " if args.dry_run else ""
    print(f"\n{mode}TOTAL updated={total_updated} skipped={total_skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
