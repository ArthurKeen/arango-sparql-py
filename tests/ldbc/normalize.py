"""Turn the published LDBC Turtle into the one dataset every target loads.

Plan decision D4. The normaliser does exactly two things, and drops exact duplicate
triples (an RDF graph is a set; the SF0.1 Turtle states two of
``dbpedia:Australia``'s triples twice, which would duplicate rows in the
RPT ``_triples`` table):

1. **Skolemise blank nodes** into ``vocab.GENID`` IRIs that keep the
   Turtle's own label (``_:knows000…`` → ``…/genid/knows000…``). PG and
   LPG documents need stable keys, and results only compare across stores
   when every node has an IRI. Labels are kept, so the output is
   reproducible run to run.
2. **Materialise ``snvoc:directKnows``** in both directions, which is what
   GraphDB's ``rule_snb_knows_bidirectional`` infers: from
   ``?p snvoc:knows ?k . ?k snvoc:hasPerson ?f`` (``?p != ?f``) it derives
   ``?p directKnows ?f`` and ``?f directKnows ?p``. It is the only
   inference the reference queries depend on.

Nothing else changes. The output is N-Triples plus a JSON summary whose
``sha256`` pins the normalised data in benchmark reports.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pyoxigraph as ox

from tests.ldbc import vocab

#: Bump when the transformation changes, so a cached output is rebuilt.
NORMALIZER_VERSION = 2


@dataclass
class NormalizeSummary:
    """What the normaliser read and wrote; stored beside the output."""

    normalizer_version: int
    inputs: list[str]
    triples_in: int = 0
    triples_out: int = 0
    duplicates_dropped: int = 0
    blank_nodes_skolemised: int = 0
    direct_knows_added: int = 0
    class_counts: dict[str, int] = field(default_factory=dict)
    predicate_counts: dict[str, int] = field(default_factory=dict)
    sha256: str = ""


def skolemise(term: Any) -> Any:
    """Replace a blank node with its ``GENID`` IRI; leave other terms alone."""
    if isinstance(term, ox.BlankNode):
        return ox.NamedNode(vocab.GENID + term.value)
    return term


def iter_normalized(triples: Iterable[ox.Triple], summary: NormalizeSummary) -> Iterator[ox.Triple]:
    """Yield the skolemised input, then the materialised ``directKnows``.

    Counts land in *summary* as the stream is consumed.
    """
    blank_labels: set[str] = set()
    knows_source: dict[str, str] = {}  # relationship node → person who knows
    knows_target: dict[str, str] = {}  # relationship node → person known
    classes: Counter[str] = Counter()
    predicates: Counter[str] = Counter()
    # 128-bit digests of the N-Triples form: small enough to hold millions,
    # and a collision (which would silently drop a real triple) is not a
    # practical risk at this size.
    seen: set[bytes] = set()

    for triple in triples:
        summary.triples_in += 1
        for term in (triple.subject, triple.object):
            if isinstance(term, ox.BlankNode):
                blank_labels.add(term.value)
        out = ox.Triple(skolemise(triple.subject), triple.predicate, skolemise(triple.object))
        key = hashlib.blake2b(str(out).encode("utf-8"), digest_size=16).digest()
        if key in seen:
            summary.duplicates_dropped += 1
            continue
        seen.add(key)
        predicate = out.predicate.value
        predicates[predicate] += 1
        if predicate == vocab.RDF_TYPE and isinstance(out.object, ox.NamedNode):
            # A tag is typed by its tag-class node, not by a class IRI.
            type_iri = out.object.value
            classes[vocab.CLASS_BY_NAME["Tag"].iri if type_iri.startswith(vocab.GENID) else type_iri] += 1
        elif predicate == vocab.KNOWS:
            knows_source[out.object.value] = out.subject.value
        elif predicate == vocab.HAS_PERSON:
            knows_target[out.subject.value] = out.object.value
        summary.triples_out += 1
        yield out

    # ``hasPerson`` also links forum memberships to people; only nodes that
    # a ``knows`` statement points at are friendships.
    pairs: set[tuple[str, str]] = set()
    for node, person in knows_source.items():
        friend = knows_target.get(node)
        if friend is None or friend == person:
            continue
        pairs.add((person, friend))
        pairs.add((friend, person))
    direct_knows = ox.NamedNode(vocab.DIRECT_KNOWS)
    for person, friend in sorted(pairs):
        predicates[vocab.DIRECT_KNOWS] += 1
        summary.triples_out += 1
        summary.direct_knows_added += 1
        yield ox.Triple(ox.NamedNode(person), direct_knows, ox.NamedNode(friend))

    summary.blank_nodes_skolemised = len(blank_labels)
    summary.class_counts = dict(sorted(classes.items()))
    summary.predicate_counts = dict(sorted(predicates.items()))


def _parse_all(paths: list[Path]) -> Iterator[ox.Triple]:
    for path in paths:
        yield from ox.parse(path=str(path), format=ox.RdfFormat.TURTLE)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize(turtle_files: list[Path], out_dir: Path) -> tuple[Path, NormalizeSummary]:
    """Write ``normalized.nt`` and ``normalized.json`` into *out_dir*.

    Reuses an existing output when its summary names the same inputs and
    normaliser version and the file still matches its recorded digest.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    nt_path = out_dir / "normalized.nt"
    summary_path = out_dir / "normalized.json"
    inputs = [p.name for p in turtle_files]
    if nt_path.is_file() and summary_path.is_file():
        cached = json.loads(summary_path.read_text())
        if (
            cached.get("normalizer_version") == NORMALIZER_VERSION
            and cached.get("inputs") == inputs
            and cached.get("sha256") == _sha256(nt_path)
        ):
            return nt_path, NormalizeSummary(**cached)

    summary = NormalizeSummary(normalizer_version=NORMALIZER_VERSION, inputs=inputs)
    tmp_path = nt_path.with_suffix(".nt.part")
    ox.serialize(iter_normalized(_parse_all(turtle_files), summary), tmp_path, ox.RdfFormat.N_TRIPLES)
    tmp_path.replace(nt_path)
    summary.sha256 = _sha256(nt_path)
    summary_path.write_text(json.dumps(asdict(summary), indent=2, sort_keys=True))
    return nt_path, summary
