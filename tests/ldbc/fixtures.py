"""A hand-written dataset in LDBC's Turtle shape, for the unit tests.

Written for this repository (not copied from LDBC). It has one of each
shape the real data has: a reified ``knows`` and ``hasMember`` (both reach a
person through ``hasPerson``, only ``knows`` is a friendship), ``likes`` to a
Post and to a Comment, a ``studyAt`` with a class year, a node typed both
Country and Continent, a tag typed by a blank-node tag class with a
superclass, an IRI-valued ``snvoc:url``, a multi-valued ``speaks``, and one
triple stated twice.
"""

from __future__ import annotations

from pathlib import Path

MINI_TTL = """\
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix snvoc: <http://www.ldbc.eu/ldbc_socialnet/1.0/vocabulary/> .
@prefix sntag: <http://www.ldbc.eu/ldbc_socialnet/1.0/tag/> .
@prefix sn: <http://www.ldbc.eu/ldbc_socialnet/1.0/data/> .
@prefix dbpedia: <http://dbpedia.org/resource/> .
@prefix dbpedia-owl: <http://dbpedia.org/ontology/> .
@prefix foaf: <http://xmlns.com/foaf/0.1/> .

dbpedia:Land rdf:type dbpedia-owl:Place, dbpedia-owl:Country, dbpedia-owl:Continent ;
    foaf:name "Land" ; snvoc:id "11"^^xsd:int .
dbpedia:Town rdf:type dbpedia-owl:Place, dbpedia-owl:City ;
    foaf:name "Town" ; snvoc:id "10"^^xsd:int ; snvoc:isPartOf dbpedia:Land .
dbpedia:Town foaf:name "Town" .
dbpedia:Uni rdf:type dbpedia-owl:University ; foaf:name "Uni" ; snvoc:id "40"^^xsd:int ;
    snvoc:url <http://dbpedia.org/resource/Uni_url> ; snvoc:isLocatedIn dbpedia:Town .

_:tagclass1 rdf:type snvoc:TagClass ; rdfs:label "Thing" ; snvoc:id "20"^^xsd:int .
_:tagclass2 rdf:type snvoc:TagClass ; rdfs:label "Writer" ; snvoc:id "21"^^xsd:int ;
    rdfs:subClassOf _:tagclass1 .
sntag:Rumi rdf:type _:tagclass2 ; foaf:name "Rumi" ; snvoc:id "30"^^xsd:int .

sn:pers1 rdf:type snvoc:Person ; snvoc:id "1"^^xsd:long ; snvoc:firstName "Ann" ;
    snvoc:speaks "fr", "en" ; snvoc:isLocatedIn dbpedia:Town ; snvoc:hasInterest sntag:Rumi ;
    snvoc:knows _:knows1 ; snvoc:likes _:likes1, _:likes2 ; snvoc:studyAt _:study1 .
sn:pers2 rdf:type snvoc:Person ; snvoc:id "2"^^xsd:long ; snvoc:firstName "Bob" ;
    snvoc:isLocatedIn dbpedia:Town .
_:knows1 snvoc:hasPerson sn:pers2 ;
    snvoc:creationDate "2010-01-02T03:04:05.006Z"^^xsd:dateTime .
_:study1 snvoc:hasOrganisation dbpedia:Uni ; snvoc:classYear "2005"^^xsd:integer .

sn:forum1 rdf:type snvoc:Forum ; snvoc:id "50"^^xsd:long ; snvoc:title "Wall" ;
    snvoc:hasModerator sn:pers1 ; snvoc:hasMember _:member1 ; snvoc:containerOf sn:post1 .
_:member1 snvoc:hasPerson sn:pers2 ;
    snvoc:joinDate "2010-02-01T00:00:00.000Z"^^xsd:dateTime .

sn:post1 rdf:type snvoc:Post ; snvoc:id "100"^^xsd:long ; snvoc:hasCreator sn:pers2 ;
    snvoc:hasTag sntag:Rumi ; snvoc:isLocatedIn dbpedia:Land ;
    snvoc:creationDate "2010-03-01T00:00:00.000Z"^^xsd:dateTime .
sn:comm1 rdf:type snvoc:Comment ; snvoc:id "101"^^xsd:long ; snvoc:hasCreator sn:pers1 ;
    snvoc:replyOf sn:post1 ; snvoc:length "3"^^xsd:int ;
    snvoc:creationDate "2010-03-02T00:00:00.000Z"^^xsd:dateTime .
_:likes1 snvoc:hasPost sn:post1 ;
    snvoc:creationDate "2010-03-03T00:00:00.000Z"^^xsd:dateTime .
_:likes2 snvoc:hasComment sn:comm1 ;
    snvoc:creationDate "2010-03-04T00:00:00.000Z"^^xsd:dateTime .
"""

#: Counts the snippet must produce; the tests assert them exactly.
MINI_TRIPLES_IN = 69
MINI_DUPLICATES = 1


def write_mini(tmp_path: Path) -> list[Path]:
    path = tmp_path / "mini.ttl"
    path.write_text(MINI_TTL, encoding="utf-8")
    return [path]
