"""Convert vendored CK25 `raw/questions.yml` + `raw/prod-inst-schema.ttl`
into this directory's `corpus.yml` (07.1-05 Task 2, NL-BENCH-02).

Pipeline (see 07.1-RESEARCH.md "Pattern 3" and the plan's Task 2 `<action>`):

1. Load `raw/questions.yml` (`yaml.safe_load` only -- never `yaml.load`).
2. Build `ontology.ttl`: start from the vendored `raw/prod-inst-schema.ttl`
   schema-only extract and add the ONLY genuinely new authoring this set
   needs -- `phys:collectionName` on every `pv:`-namespaced class referenced
   by ANY question's `classes:` manifest, and `phys:edgeCollectionName` on
   every `pv:`-namespaced object property referenced by ANY question's
   `properties:` manifest. Datatype properties are left bare (they resolve
   to a local-name attribute automatically). The `classes:`/`properties:`
   manifest fields are the audit checklist (Assumption A3) -- used directly
   rather than re-deriving usage from the SPARQL text.
3. Apply the D-06 filter: keep a question ONLY IF (a) every one of its
   manifest terms is actually declared in `ontology.ttl`, (b) its gold
   parses, and (c) it `translate()`s to non-empty AQL against `ontology.ttl`.
   Every input question ends up in exactly one bucket (kept or dropped with
   a reason) -- no silent truncation.
4. Emit `corpus.yml` (`ontology:` + `cases:` survivors, via `yaml.safe_dump`)
   and `filter_log.md` (kept/dropped audit trail).

Each case's `expected` field is the CK25 gold `query.sparql` VERBATIM --
CK25 golds characteristically use a constant instance IRI as the query's
subject (a SUPPORTED code path; see 07.1-RESEARCH.md Pattern 3), so no
rewriting is needed.

Run directly (`python -m tests.nl2sparql.eval.vendored.ck25.convert_ck25`)
to regenerate `ontology.ttl` / `corpus.yml` / `filter_log.md` from the
vendored raw files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from rdflib import RDF, Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL

from arango_sparql.api import translate
from arango_sparql.errors import SparqlError, SparqlParseError
from arango_sparql.translate.parser import parse_sparql
from arango_sparql.translate.resolver import SchemaResolver

HERE = Path(__file__).parent
CONVERTER_PATH = Path(__file__)
RAW_DIR = HERE / "raw"
RAW_QUESTIONS_PATH = RAW_DIR / "questions.yml"
RAW_SCHEMA_PATH = RAW_DIR / "prod-inst-schema.ttl"
ONTOLOGY_PATH = HERE / "ontology.ttl"
CORPUS_PATH = HERE / "corpus.yml"
FILTER_LOG_PATH = HERE / "filter_log.md"

# CK25's default namespace (questions.yml `dataset.defaultNamespace`;
# confirmed against `raw/prod-inst-schema.ttl`'s `pv:` prefix binding).
PV_NS = "http://ld.company.org/prod-vocab/"
# Canonical phys: physical-mapping namespace (resolver.py's
# DEFAULT_OWL_PHYSICAL_IRI -- the resolver normalizes several spellings, but
# this is the first/canonical one).
PHYS_NS = "http://arangodb.com/schema/physical#"


# ---------------------------------------------------------------------------
# Loaders -- trusted, checked-in vendored YAML, always via safe_load only.
# ---------------------------------------------------------------------------


def load_questions(path: Path = RAW_QUESTIONS_PATH) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text())
    return data["questions"]


# ---------------------------------------------------------------------------
# Ontology construction -- phys: annotation, the only new authoring (Pattern 3)
# ---------------------------------------------------------------------------


def _referenced_terms(questions: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    """Return `(referenced class local-names, referenced property
    local-names)` -- the union, across every question, of its `classes:`/
    `properties:` manifest (the audit checklist driving which schema terms
    need `phys:` annotation). Only `:`-prefixed (i.e. `pv:`-namespaced)
    terms are considered -- a manifest entry like `rdfs:subClassOf` is not a
    `pv:` schema term this converter annotates.
    """
    classes: set[str] = set()
    props: set[str] = set()
    for question in questions:
        for class_ref in question.get("classes", []):
            if class_ref.startswith(":"):
                classes.add(class_ref[1:])
        for prop_ref in question.get("properties", []):
            if prop_ref.startswith(":"):
                props.add(prop_ref[1:])
    return classes, props


def build_ontology(
    questions: list[dict[str, Any]],
    schema_path: Path = RAW_SCHEMA_PATH,
) -> str:
    """Extend the vendored schema-only Turtle extract with `phys:`
    annotations -- see module docstring step 2. Terms declared in the schema
    but never referenced by any question (e.g. `dbo:Country`,
    `pv:hasDirectReport`) are left un-annotated: unused schema surface, not
    exercised by any gold this converter needs to make judgeable.
    """
    graph = Graph()
    graph.parse(str(schema_path), format="turtle")
    phys = Namespace(PHYS_NS)
    graph.bind("phys", phys)

    referenced_classes, referenced_props = _referenced_terms(questions)

    for local_name in sorted(referenced_classes):
        term = URIRef(PV_NS + local_name)
        if (term, RDF.type, OWL.Class) not in graph:
            continue  # not a declared pv: class in this schema extract
        collection_name = local_name if local_name.endswith("s") else f"{local_name}s"
        graph.set((term, phys.collectionName, Literal(collection_name)))

    for local_name in sorted(referenced_props):
        term = URIRef(PV_NS + local_name)
        if (term, RDF.type, OWL.ObjectProperty) not in graph:
            continue  # a datatype property (or non-pv: term) -- no edge annotation
        graph.set((term, phys.edgeCollectionName, Literal(local_name)))

    return graph.serialize(format="turtle")


# ---------------------------------------------------------------------------
# D-06 filter -- reuse the deterministic parser/resolver/translate stack,
# never a hand-rolled validator (RESEARCH "Don't Hand-Roll").
# ---------------------------------------------------------------------------


def _manifest_terms_undeclared_reason(question: dict[str, Any], ontology_graph: Graph) -> str | None:
    """Return a drop-reason if any of *question*'s `classes:`/`properties:`
    manifest terms is not declared AT ALL in *ontology_graph* (as
    `owl:Class` / `owl:ObjectProperty` / `owl:DatatypeProperty`) -- else
    `None`. This is the D-06 criterion (c) "manifest term is declared" gate,
    independent of whether the term is `phys:`-annotated.
    """
    for class_ref in question.get("classes", []):
        if not class_ref.startswith(":"):
            continue
        term = URIRef(PV_NS + class_ref[1:])
        if (term, RDF.type, OWL.Class) not in ontology_graph:
            return f"manifest class {class_ref!r} is not declared owl:Class in ontology.ttl"
    for prop_ref in question.get("properties", []):
        if not prop_ref.startswith(":"):
            continue
        term = URIRef(PV_NS + prop_ref[1:])
        is_object = (term, RDF.type, OWL.ObjectProperty) in ontology_graph
        is_datatype = (term, RDF.type, OWL.DatatypeProperty) in ontology_graph
        if not (is_object or is_datatype):
            return f"manifest property {prop_ref!r} is not declared in ontology.ttl"
    return None


def convert_case(question: dict[str, Any]) -> dict[str, Any]:
    """One CK25 question -> one `corpus.yml` case dict (pre-filter shape)."""
    return {
        "name": f"ck25-{question['id']}",
        "nl": question["question"]["en"],
        "expected": question["query"]["sparql"],
    }


def filter_cases(
    questions: list[dict[str, Any]],
    ontology_ttl: str,
) -> tuple[list[dict[str, Any]], list[tuple[int, str]]]:
    """Apply the D-06 filter. Returns `(kept_cases, dropped)` where `dropped`
    is a list of `(question_id, reason)` pairs. Every input question ends up
    in exactly one of the two return values -- no silent truncation.
    """
    ontology_graph = Graph()
    ontology_graph.parse(data=ontology_ttl, format="turtle")

    kept: list[dict[str, Any]] = []
    dropped: list[tuple[int, str]] = []

    for question in questions:
        qid = question["id"]
        sparql = question["query"]["sparql"]

        undeclared_reason = _manifest_terms_undeclared_reason(question, ontology_graph)
        if undeclared_reason:
            dropped.append((qid, undeclared_reason))
            continue

        try:
            parse_sparql(sparql)
        except SparqlParseError as exc:
            dropped.append((qid, f"gold does not parse: {exc}"))
            continue

        # Fresh resolver per question -- resolvers cache resolution results,
        # and translate() may mutate resolver.warnings; a fresh instance per
        # question keeps the filter's per-question outcome independent.
        resolver = SchemaResolver.from_turtle(ontology_ttl)
        try:
            result = translate(sparql, resolver=resolver)
        except SparqlError as exc:
            dropped.append((qid, f"{type(exc).__name__}: {exc}"))
            continue

        if not result.aql:
            dropped.append((qid, "translate() produced empty AQL"))
            continue

        kept.append(convert_case(question))

    return kept, dropped


def build_corpus(
    questions_path: Path = RAW_QUESTIONS_PATH,
    schema_path: Path = RAW_SCHEMA_PATH,
) -> tuple[str, list[dict[str, Any]], list[tuple[int, str]]]:
    """Run the full pipeline (steps 1-3). Returns `(ontology_ttl,
    kept_cases, dropped)`."""
    questions = load_questions(questions_path)
    ontology_ttl = build_ontology(questions, schema_path)
    kept, dropped = filter_cases(questions, ontology_ttl)
    return ontology_ttl, kept, dropped


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------


def _str_presenter(dumper: yaml.Dumper, data: str) -> yaml.ScalarNode:
    """Render multi-line strings as YAML block-literal scalars (`|`) rather
    than escaped single-line strings, matching the existing hand-authored
    `corpus.yml`'s style -- purely cosmetic, does not affect `safe_load`."""
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


yaml.representer.SafeRepresenter.add_representer(str, _str_presenter)


def write_corpus(ontology_ttl: str, kept: list[dict[str, Any]], path: Path = CORPUS_PATH) -> None:
    corpus = {"ontology": ontology_ttl, "cases": kept}
    path.write_text(yaml.safe_dump(corpus, sort_keys=False, allow_unicode=True, width=100))


def write_filter_log(
    total: int,
    kept: list[dict[str, Any]],
    dropped: list[tuple[int, str]],
    path: Path = FILTER_LOG_PATH,
) -> None:
    lines = [
        "# CK25 D-06 filter log",
        "",
        f"Total questions: {total}",
        f"Kept: {len(kept)}",
        f"Dropped: {len(dropped)}",
        "",
        "CK25 is a directional corporate-domain relevance anchor (D-03), not a",
        "power gate -- a small surviving N is expected and acceptable.",
        "",
    ]
    if dropped:
        lines.append("## Dropped")
        lines.append("")
        lines.append("| Question ID | Reason |")
        lines.append("|---|---|")
        for qid, reason in sorted(dropped):
            escaped = reason.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {qid} | {escaped} |")
        lines.append("")
    lines.append("## Kept")
    lines.append("")
    lines.append(", ".join(case["name"] for case in kept) or "(none)")
    lines.append("")
    path.write_text("\n".join(lines))


def main() -> None:
    questions = load_questions()
    ontology_ttl, kept, dropped = build_corpus()
    ONTOLOGY_PATH.write_text(ontology_ttl)
    write_corpus(ontology_ttl, kept)
    write_filter_log(len(questions), kept, dropped)
    print(f"CK25 conversion: {len(kept)} kept / {len(questions)} total ({len(dropped)} dropped)")


if __name__ == "__main__":
    main()
