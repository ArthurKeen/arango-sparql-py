"""Unit tests for :mod:`tests.ldbc.normalize` (no network, no database)."""

from __future__ import annotations

import json
from pathlib import Path

import pyoxigraph as ox

from tests.ldbc import normalize, vocab
from tests.ldbc.fixtures import MINI_DUPLICATES, MINI_TRIPLES_IN, write_mini

SN = vocab.SN


def _run(tmp_path: Path) -> tuple[list[ox.Triple], normalize.NormalizeSummary]:
    nt_path, summary = normalize.normalize(write_mini(tmp_path), tmp_path / "out")
    return list(ox.parse(path=str(nt_path), format=ox.RdfFormat.N_TRIPLES)), summary


def test_blank_nodes_become_genid_iris_that_keep_their_label(tmp_path: Path) -> None:
    triples, summary = _run(tmp_path)
    assert not any(isinstance(t.subject, ox.BlankNode) or isinstance(t.object, ox.BlankNode) for t in triples)
    subjects = {t.subject.value for t in triples}
    assert vocab.GENID + "knows1" in subjects
    assert vocab.GENID + "tagclass2" in subjects
    # knows1, likes1, likes2, study1, member1, tagclass1, tagclass2
    assert summary.blank_nodes_skolemised == 7


def test_direct_knows_is_materialised_both_ways_and_only_for_friendships(tmp_path: Path) -> None:
    triples, summary = _run(tmp_path)
    direct = {(t.subject.value, t.object.value) for t in triples if t.predicate.value == vocab.DIRECT_KNOWS}
    # forum membership also uses hasPerson; it must not become a friendship.
    assert direct == {(SN + "pers1", SN + "pers2"), (SN + "pers2", SN + "pers1")}
    assert summary.direct_knows_added == 2


def test_exact_duplicates_are_dropped(tmp_path: Path) -> None:
    triples, summary = _run(tmp_path)
    assert summary.triples_in == MINI_TRIPLES_IN
    assert summary.duplicates_dropped == MINI_DUPLICATES
    assert len(triples) == len({str(t) for t in triples})
    assert summary.triples_out == len(triples) == MINI_TRIPLES_IN - MINI_DUPLICATES + 2


def test_class_counts_count_tags_under_the_synthetic_tag_class(tmp_path: Path) -> None:
    _triples, summary = _run(tmp_path)
    assert summary.class_counts[vocab.CLASS_BY_NAME["Tag"].iri] == 1
    assert summary.class_counts[vocab.CLASS_BY_NAME["TagClass"].iri] == 2
    assert not any(iri.startswith(vocab.GENID) for iri in summary.class_counts)


def test_output_is_reused_when_unchanged_and_rebuilt_when_tampered(tmp_path: Path) -> None:
    files = write_mini(tmp_path)
    out = tmp_path / "out"
    nt_path, first = normalize.normalize(files, out)
    mtime = nt_path.stat().st_mtime_ns
    _, second = normalize.normalize(files, out)
    assert nt_path.stat().st_mtime_ns == mtime
    assert second.sha256 == first.sha256

    nt_path.write_text("tampered\n")
    _, third = normalize.normalize(files, out)
    assert third.sha256 == first.sha256
    assert json.loads((out / "normalized.json").read_text())["sha256"] == first.sha256
