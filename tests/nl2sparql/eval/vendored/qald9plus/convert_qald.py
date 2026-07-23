"""QALD-9-plus (English, DBpedia) -> ``corpus.yml`` conversion (D-06 filter).

Reads the two pruned English-only extracts under ``raw/`` (Plan 04 Task 1),
combines the train+test pool (D-02 — the 150-question test split alone lands
at ~11-13pt MDE; only the combined ~558-question pool reaches the ~6-8pt
range), applies the D-06 filter (parse + judgeable + transpilable + declared
schema terms), authors a minimal ``phys:``-annotated DBpedia ontology subset
covering exactly the survivors' classes/properties, and writes:

* ``dbpedia_subset.ttl`` — the authored ontology subset
* ``corpus.yml`` — ``ontology:`` + ``cases:`` in the existing corpus shape
* ``filter_log.md`` — kept/dropped counts + per-drop-reason audit trail

Run directly to (re)generate these three checked-in output files::

    python -m tests.nl2sparql.eval.vendored.qald9plus.convert_qald

The D-06 filter runs in two passes:

1. **Provisional pass** (``permissive_class_resolution=True``, empty
   ontology) — discovers which raw golds are transpilable *at all* (i.e. hit
   no genuine transpiler feature gap such as ``UnsupportedSparqlError``),
   without yet requiring any schema term to be declared. This pass exists
   only to bound the term-extraction step below to golds that could ever
   possibly pass, before any ontology is authored.
2. **Final pass** (harness-default ``permissive_class_resolution=False``,
   authored ``dbpedia_subset.ttl``) — re-transpiles every provisional
   survivor against the just-authored subset. Because the subset is built
   directly from the provisional survivors' own referenced terms, every
   provisional survivor is expected to also pass this final pass; any that
   don't (an edge case the term-extraction walk missed) are dropped with
   reason ``term-not-in-subset`` rather than silently kept.

Property classification (object vs datatype): for each predicate IRI
observed across the provisional survivors, evidence is gathered over every
triple that uses it — literal-valued objects mark it a candidate
``owl:DatatypeProperty``; IRI-valued objects, or a variable object that is
itself typed via ``rdf:type`` elsewhere in the same query, mark it a
candidate ``owl:ObjectProperty``. A predicate with NO evidence either way
(only ever seen with an untyped variable object — e.g. a bare `?x :p ?v`
whose `?v` is only ever bound, not typed) defaults to
``owl:DatatypeProperty``: this is the functionally SAFE default (a bare
``owl:DatatypeProperty`` resolves via local-name attribute lookup exactly
like an undeclared property's graceful-degradation fallback — no
``phys:edgeCollectionName`` is required, so this can never trigger the
object-property-without-edge-collection hard-fail (RESEARCH Pattern 4)).
Per RESEARCH Pitfall 2, ``rdfs:domain``/``rdfs:range`` axioms are
LLM-context documentation only (never functionally load-bearing) — the
object/datatype split is authored for LLM-prompt faithfulness, not because
an imprecise classification would itself break transpilation.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from rdflib import RDF
from rdflib.plugins.sparql.parserutils import CompValue
from rdflib.term import Literal as RdfLiteral
from rdflib.term import URIRef, Variable

from arango_sparql.api import translate
from arango_sparql.errors import SparqlError, SparqlParseError
from arango_sparql.translate.parser import parse_sparql
from arango_sparql.translate.resolver import SchemaResolver, local_name

HERE = Path(__file__).parent
RAW_DIR = HERE / "raw"
TRAIN_PATH = RAW_DIR / "qald_9_plus_train_dbpedia_en.json"
TEST_PATH = RAW_DIR / "qald_9_plus_test_dbpedia_en.json"
ONTOLOGY_PATH = HERE / "dbpedia_subset.ttl"
CORPUS_PATH = HERE / "corpus.yml"
FILTER_LOG_PATH = HERE / "filter_log.md"

# Source files feeding the combined D-02 train+test pool, tagged with a
# short source label used both for the qualified case name (train/test ids
# collide — see module docstring note below) and for the filter log's
# per-source breakdown.
_SOURCES: tuple[tuple[Path, str], ...] = ((TRAIN_PATH, "train"), (TEST_PATH, "test"))

# QALD-9-plus's train and test splits number their questions independently
# (both starting near 1) — verified empirically: all 150 test ids collide
# with train ids that reference entirely DIFFERENT questions. Every case
# name is therefore qualified by source (``qald9plus-{source}-{id}``) to
# guarantee uniqueness across the combined pool.
_NAME_TEMPLATE = "qald9plus-{source}-{id}"

# Namespace prefixes observed across the kept survivors' terms (verified via
# the provisional-pass term extraction — see module docstring). ``:`` is
# reserved for the synthetic corpus namespace convention used by the
# existing hand-authored corpus.yml/bgp_select.yml fixtures; every DBpedia
# term below is addressed by its OWN native prefix so the authored ontology
# reads as a genuine (subset of) DBpedia, not a re-namespaced copy.
_TTL_PREFIXES = """\
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix phys: <https://arango.solutions/phys#> .
@prefix dbo: <http://dbpedia.org/ontology/> .
@prefix dbp: <http://dbpedia.org/property/> .
@prefix dbyago: <http://dbpedia.org/class/yago/> .
@prefix dct: <http://purl.org/dc/terms/> .
@prefix foaf: <http://xmlns.com/foaf/0.1/> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
"""

_PREFIX_FOR_NS: tuple[tuple[str, str], ...] = (
    ("http://dbpedia.org/ontology/", "dbo"),
    ("http://dbpedia.org/property/", "dbp"),
    ("http://dbpedia.org/class/yago/", "dbyago"),
    ("http://purl.org/dc/terms/", "dct"),
    ("http://xmlns.com/foaf/0.1/", "foaf"),
    ("http://www.w3.org/2004/02/skos/core#", "skos"),
    ("http://www.w3.org/2002/07/owl#", "owl"),
)


def _qname(iri: str) -> str:
    """Render *iri* as ``prefix:LocalName`` using :data:`_PREFIX_FOR_NS`.

    Falls back to a full ``<...>`` IRIREF if no known prefix matches (should
    not happen for any term this converter actually declares — every
    namespace observed across the survivors is enumerated above).
    """
    for ns, prefix in _PREFIX_FOR_NS:
        if iri.startswith(ns):
            return f"{prefix}:{iri[len(ns) :]}"
    return f"<{iri}>"


# ---------------------------------------------------------------------------
# Step 1 — load the combined pool, FIRST-en selection (Pitfall 5)
# ---------------------------------------------------------------------------


def _first_english(question_variants: list[dict[str, Any]]) -> tuple[str | None, int]:
    """Return ``(first en string, count of en entries)`` for one question.

    Deterministic (order-preserving JSON parse): always the FIRST
    ``language == "en"`` entry, never re-selected across runs. The raw
    vendored files (Task 1) are already pruned to a single ``en`` entry
    each, so ``count`` is expected to be 1 for every real question — this
    function stays general (and is unit-tested against a >1 fixture) so it
    would still behave correctly against an un-pruned QALD-JSON source.
    """
    en_entries = [v for v in question_variants if v.get("language") == "en"]
    if not en_entries:
        return None, 0
    return en_entries[0]["string"], len(en_entries)


def load_pool() -> list[dict[str, Any]]:
    """Load + combine both raw English extracts into one tagged pool.

    Each returned dict carries the original QALD-JSON question fields plus
    a synthetic ``_source`` key (``"train"`` or ``"test"``).
    """
    pool: list[dict[str, Any]] = []
    for path, source in _SOURCES:
        raw = json.loads(path.read_text(encoding="utf-8"))
        for q in raw["questions"]:
            tagged = dict(q)
            tagged["_source"] = source
            pool.append(tagged)
    return pool


# ---------------------------------------------------------------------------
# Step 2 — BGP-triple extraction (shared by the term-extraction step)
# ---------------------------------------------------------------------------


def _walk_triples(node: Any, out: list[tuple[Any, Any, Any]]) -> None:
    """Recursively collect every ``BGP`` node's ``triples`` list from an
    rdflib translated-algebra tree, wherever it occurs (a query may nest
    BGPs inside ``LeftJoin``/``Filter``/``Union``/... branches)."""
    if isinstance(node, CompValue):
        if node.name == "BGP" and "triples" in node:
            out.extend(node["triples"])
        for value in node.values():
            _walk_triples(value, out)
    elif isinstance(node, (list, tuple, set, frozenset)):
        for item in node:
            _walk_triples(item, out)


# ---------------------------------------------------------------------------
# Step 3 — the D-06 filter
# ---------------------------------------------------------------------------


@dataclass
class FilterOutcome:
    kept: list[dict[str, Any]] = field(default_factory=list)
    # reason -> list of qualified case names dropped for that reason
    dropped: dict[str, list[str]] = field(default_factory=dict)
    multiple_en_paraphrases: list[str] = field(default_factory=list)

    def drop(self, reason: str, name: str) -> None:
        self.dropped.setdefault(reason, []).append(name)

    @property
    def total_dropped(self) -> int:
        return sum(len(v) for v in self.dropped.values())


_BOOTSTRAP_RESOLVER = SchemaResolver.from_turtle("", permissive_class_resolution=True)


def _provisional_survivors(pool: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], FilterOutcome]:
    """Pass 1+2+3 of the D-06 filter (parse / judgeable / feature-transpilable).

    Runs against the bootstrap (empty-ontology, permissive-class) resolver —
    this is deliberately BEFORE any ontology is authored, so it only screens
    out golds that can never transpile regardless of schema (malformed
    gold SPARQL, or a genuine transpiler feature gap such as
    ``UnsupportedSparqlError``). The returned survivors feed term extraction
    (Step 4) for the final, real ontology subset.
    """
    outcome = FilterOutcome()
    survivors: list[dict[str, Any]] = []
    for q in pool:
        name = _NAME_TEMPLATE.format(source=q["_source"], id=q["id"])
        nl, en_count = _first_english(q["question"])
        if en_count > 1:
            outcome.multiple_en_paraphrases.append(name)
        if nl is None:
            outcome.drop("no-english-string", name)
            continue
        gold = q["query"]["sparql"]

        try:
            parsed = parse_sparql(gold)
        except SparqlParseError:
            outcome.drop("parse-fail", name)
            continue

        try:
            result = translate(gold, resolver=_BOOTSTRAP_RESOLVER)
        except SparqlError:
            outcome.drop("non-transpilable", name)
            continue
        if not result.aql:
            outcome.drop("non-transpilable", name)
            continue

        survivors.append(
            {
                "name": name,
                "nl": nl,
                "expected": gold,
                "algebra": parsed.algebra,
            }
        )
    return survivors, outcome


# ---------------------------------------------------------------------------
# Step 4 — schema-term extraction + ontology-subset authoring
# ---------------------------------------------------------------------------


def extract_terms(
    survivors: list[dict[str, Any]],
) -> tuple[set[str], dict[str, dict[str, bool]]]:
    """Walk every survivor's BGP triples, collecting:

    * ``classes`` — every IRI ever used as the object of an ``rdf:type``
      triple (i.e. every class referenced by a ``?x a :Class`` pattern).
    * ``prop_evidence`` — per-predicate-IRI evidence dict
      (``literal``/``uriref``/``typed_var`` booleans) used by
      :func:`classify_properties` below.
    """
    classes: set[str] = set()
    prop_evidence: dict[str, dict[str, bool]] = {}

    for survivor in survivors:
        triples: list[tuple[Any, Any, Any]] = []
        _walk_triples(survivor["algebra"], triples)

        typed_vars: set[Variable] = set()
        for s, p, o in triples:
            if p == RDF.type and isinstance(o, URIRef):
                classes.add(str(o))
                if isinstance(s, Variable):
                    typed_vars.add(s)

        for _s, p, o in triples:
            if p == RDF.type or not isinstance(p, URIRef):
                continue
            evidence = prop_evidence.setdefault(
                str(p), {"literal": False, "uriref": False, "typed_var": False}
            )
            if isinstance(o, RdfLiteral):
                evidence["literal"] = True
            elif isinstance(o, URIRef):
                evidence["uriref"] = True
            elif isinstance(o, Variable) and o in typed_vars:
                evidence["typed_var"] = True

    return classes, prop_evidence


def classify_properties(prop_evidence: dict[str, dict[str, bool]]) -> dict[str, str]:
    """Classify each predicate IRI as ``"object"`` or ``"datatype"``.

    See the module docstring's "Property classification" section for the
    evidence rules and the (functionally safe) datatype default for
    no-evidence predicates.
    """
    classification: dict[str, str] = {}
    for prop, evidence in prop_evidence.items():
        if evidence["uriref"] or evidence["typed_var"]:
            classification[prop] = "object"
        else:
            classification[prop] = "datatype"
    return classification


def _disambiguated_names(iris: set[str]) -> dict[str, str]:
    """Map each IRI to a ``phys:*`` string value, disambiguating any
    local-name collision (e.g. ``dbo:starring`` vs ``dbp:starring``) by
    prefixing the colliding value with its namespace prefix.

    This disambiguates only the STRING VALUE stored in
    ``phys:collectionName``/``phys:edgeCollectionName`` — the RDF term
    itself is always addressed by its full (distinct) IRI, so this is a
    cosmetic-but-clarity-preserving step, not a correctness requirement
    (duplicate physical names would not themselves break translation).
    """
    counts = Counter(local_name(iri) for iri in iris)
    names: dict[str, str] = {}
    for iri in sorted(iris):
        base = local_name(iri)
        if counts[base] > 1:
            for ns, prefix in _PREFIX_FOR_NS:
                if iri.startswith(ns):
                    names[iri] = f"{prefix}_{base}"
                    break
            else:
                names[iri] = base
        else:
            names[iri] = base
    return names


def build_ontology_ttl(classes: set[str], prop_classification: dict[str, str]) -> str:
    """Render the authored DBpedia ontology subset as Turtle text."""
    class_names = _disambiguated_names(classes)
    prop_iris = set(prop_classification)
    prop_names = _disambiguated_names(prop_iris)

    lines: list[str] = [_TTL_PREFIXES, ""]
    lines.append(f"# DBpedia ontology subset — {len(classes)} classes, {len(prop_iris)} properties.")
    lines.append("# Authored by convert_qald.py from the D-06 provisional survivors'")
    lines.append("# own referenced terms (see module docstring) — never the full DBpedia")
    lines.append("# ontology (RESEARCH D-05 / Anti-Patterns).")
    lines.append("")

    lines.append("# --- Classes ---")
    for iri in sorted(classes):
        qname = _qname(iri)
        collection = class_names[iri]
        lines.append(f'{qname} a owl:Class ; phys:collectionName "{collection}" .')
    lines.append("")

    lines.append("# --- Object properties (IRI-valued objects; edge-collection mapped) ---")
    for iri in sorted(prop_iris):
        if prop_classification[iri] != "object":
            continue
        qname = _qname(iri)
        edge = prop_names[iri]
        lines.append(f'{qname} a owl:ObjectProperty ; phys:edgeCollectionName "{edge}" .')
    lines.append("")

    lines.append("# --- Datatype properties (literal-valued objects; attribute lookup) ---")
    for iri in sorted(prop_iris):
        if prop_classification[iri] != "datatype":
            continue
        qname = _qname(iri)
        lines.append(f"{qname} a owl:DatatypeProperty .")
    lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Step 5 — final D-06 pass against the authored ontology (harness default)
# ---------------------------------------------------------------------------


def _final_survivors(
    survivors: list[dict[str, Any]], ontology_ttl: str, outcome: FilterOutcome
) -> list[dict[str, Any]]:
    """Re-transpile every provisional survivor against the just-authored
    ``dbpedia_subset.ttl`` using the harness-DEFAULT resolver settings
    (``permissive_class_resolution=False``). Expected to keep 100% of the
    provisional survivors (the ontology is built from their own terms) —
    any that fail this final pass are dropped with reason
    ``term-not-in-subset`` rather than silently kept.
    """
    final_resolver = SchemaResolver.from_turtle(ontology_ttl)
    kept: list[dict[str, Any]] = []
    for survivor in survivors:
        try:
            result = translate(survivor["expected"], resolver=final_resolver)
        except SparqlError:
            outcome.drop("term-not-in-subset", survivor["name"])
            continue
        if not result.aql:
            outcome.drop("term-not-in-subset", survivor["name"])
            continue
        kept.append({"name": survivor["name"], "nl": survivor["nl"], "expected": survivor["expected"]})
    return kept


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass
class ConvertResult:
    ontology_ttl: str
    cases: list[dict[str, str]]
    outcome: FilterOutcome
    total_input: int


def convert(pool: list[dict[str, Any]] | None = None) -> ConvertResult:
    """Run the full D-06 pipeline over *pool* (defaults to the real vendored
    train+test pool) and return the authored ontology, kept cases, and the
    filter outcome — pure/no disk writes, so both tests and :func:`main`
    reuse this single code path.
    """
    if pool is None:
        pool = load_pool()

    survivors, outcome = _provisional_survivors(pool)
    classes, prop_evidence = extract_terms(survivors)
    prop_classification = classify_properties(prop_evidence)
    ontology_ttl = build_ontology_ttl(classes, prop_classification)
    kept = _final_survivors(survivors, ontology_ttl, outcome)

    return ConvertResult(
        ontology_ttl=ontology_ttl,
        cases=kept,
        outcome=outcome,
        total_input=len(pool),
    )


def _render_filter_log(result: ConvertResult, pool: list[dict[str, Any]]) -> str:
    per_source_total = Counter(q["_source"] for q in pool)
    per_source_kept = Counter(name.split("-")[1] for name in (c["name"] for c in result.cases))

    lines = [
        "# QALD-9-plus D-06 filter log",
        "",
        f"**Total input questions (train+test combined):** {result.total_input}",
        f"- train: {per_source_total['train']}",
        f"- test: {per_source_total['test']}",
        "",
        f"**Kept:** {len(result.cases)}",
        f"- train: {per_source_kept.get('train', 0)}",
        f"- test: {per_source_kept.get('test', 0)}",
        "",
        f"**Dropped:** {result.outcome.total_dropped}",
        "",
        "| Reason | Count |",
        "|--------|-------|",
    ]
    for reason, names in sorted(result.outcome.dropped.items()):
        lines.append(f"| {reason} | {len(names)} |")
    lines.append("")
    lines.append(
        f"**Reconciliation:** kept ({len(result.cases)}) + dropped "
        f"({result.outcome.total_dropped}) = {len(result.cases) + result.outcome.total_dropped} "
        f"== total input ({result.total_input})."
    )
    lines.append("")

    if result.outcome.multiple_en_paraphrases:
        lines.append(
            f"**Multiple-English-paraphrase questions (FIRST en taken, logged not dropped):** "
            f"{len(result.outcome.multiple_en_paraphrases)}"
        )
    else:
        lines.append(
            "**Multiple-English-paraphrase questions:** 0 (Task 1's raw pruning already "
            "reduced every question to a single `en` entry)."
        )
    lines.append("")

    lines.append("## Per-reason detail")
    for reason, names in sorted(result.outcome.dropped.items()):
        lines.append(f"\n### {reason} ({len(names)})\n")
        for name in names:
            lines.append(f"- {name}")
    lines.append("")

    try:
        from tests.nl2sparql.eval.power import achieved_mde

        n = len(result.cases)
        lines.append("## Statistical power (D-07)")
        lines.append("")
        lines.append(f"At the kept survivor count (N={n}), achieved MDE (alpha=0.05, power=0.80):")
        lines.append("")
        lines.append("| Assumed discordant rate (pi) | achieved_mde |")
        lines.append("|---|---|")
        for pi in (0.20, 0.25):
            lines.append(f"| {pi} | {achieved_mde(n, pi):.4f} |")
        lines.append("")
    except Exception:  # pragma: no cover - defensive; power module is optional here
        pass

    return "\n".join(lines) + "\n"


def main() -> None:
    result = convert()
    pool = load_pool()

    ONTOLOGY_PATH.write_text(result.ontology_ttl, encoding="utf-8")

    corpus = {"ontology": result.ontology_ttl, "cases": result.cases}
    CORPUS_PATH.write_text(yaml.safe_dump(corpus, sort_keys=False, allow_unicode=True), encoding="utf-8")

    FILTER_LOG_PATH.write_text(_render_filter_log(result, pool), encoding="utf-8")

    print(f"kept={len(result.cases)} dropped={result.outcome.total_dropped} total={result.total_input}")


if __name__ == "__main__":
    main()
