"""The LDBC SNB vocabulary and how each term maps to the three layouts.

This is the one place that says what an LDBC class or predicate becomes in
the RDF (``RPT``), LPG and PG layouts. The normaliser, the loader and the
mapping fixtures all read it, so the layouts cannot drift apart.

The tables were derived from the SF0.1 Turtle (counted 2026-10-23, see
``docs/architecture/ldbc_benchmark_plan.md`` §2.3), not from the LDBC
specification: the harness loads what the Turtle actually contains.

PG and LPG collection names follow the sister project's plan
(``arango-cypher-py/docs/ldbc_benchmark_plan.md`` §5.4) so one loaded
database can serve both transpilers (plan WP-LDBC-XTRANS).
"""

from __future__ import annotations

from dataclasses import dataclass

SNVOC = "http://www.ldbc.eu/ldbc_socialnet/1.0/vocabulary/"
SN = "http://www.ldbc.eu/ldbc_socialnet/1.0/data/"
DBO = "http://dbpedia.org/ontology/"
FOAF = "http://xmlns.com/foaf/0.1/"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
XSD = "http://www.w3.org/2001/XMLSchema#"

RDF_TYPE = RDF + "type"

#: Skolem IRI prefix for the Turtle's blank nodes (RDF 1.1 §3.5). Blank
#: nodes are the reified relationships (``_:knows…``) and the tag classes
#: (``_:tagclass…``); PG and LPG documents need stable keys, and results
#: can only be compared across stores when every node has an IRI.
GENID = "http://www.ldbc.eu/ldbc_socialnet/1.0/.well-known/genid/"

#: Materialised by the normaliser. GraphDB derives it with the custom rule
#: ``rule_snb_knows_bidirectional`` (``graphdb/config/rdfsPlus-snb-bidir.pie``
#: in the reference repository); the data contains none.
DIRECT_KNOWS = SNVOC + "directKnows"
KNOWS = SNVOC + "knows"
HAS_PERSON = SNVOC + "hasPerson"


@dataclass(frozen=True)
class NodeClass:
    """A concrete LDBC node class.

    ``iri`` is the class the data types its instances with, except for
    ``Tag``: tags are typed by their tag-class *node*
    (``sntag:Rumi rdf:type _:tagclass000211``), so ``Tag`` has a synthetic
    IRI that only the mapping fixtures use.
    """

    name: str
    iri: str
    pg_collection: str
    supertype: str | None = None


NODE_CLASSES: tuple[NodeClass, ...] = (
    NodeClass("Person", SNVOC + "Person", "person"),
    NodeClass("Forum", SNVOC + "Forum", "forum"),
    NodeClass("Post", SNVOC + "Post", "post", supertype="Message"),
    NodeClass("Comment", SNVOC + "Comment", "comment", supertype="Message"),
    NodeClass("Tag", SNVOC + "Tag", "tag"),
    NodeClass("TagClass", SNVOC + "TagClass", "tagclass"),
    NodeClass("City", DBO + "City", "city", supertype="Place"),
    NodeClass("Country", DBO + "Country", "country", supertype="Place"),
    NodeClass("Continent", DBO + "Continent", "continent", supertype="Place"),
    NodeClass("University", DBO + "University", "university", supertype="Organisation"),
    NodeClass("Company", DBO + "Company", "company", supertype="Organisation"),
)
CLASS_BY_IRI = {c.iri: c for c in NODE_CLASSES}
CLASS_BY_NAME = {c.name: c for c in NODE_CLASSES}

#: Supertype classes the data asserts alongside a concrete class. They add
#: an LPG label but never choose a PG collection.
SUPERTYPE_IRIS = frozenset({DBO + "Place"})

#: When a node carries two concrete classes, PG keeps it in the first one
#: listed here. Only ``dbpedia:Australia`` does in SF0.1: the Turtle
#: serialiser gives the country and the continent the same IRI, where the
#: CSV export keeps two entities. LPG keeps both labels; PG cannot.
PG_CLASS_PRIORITY: tuple[str, ...] = tuple(c.name for c in NODE_CLASSES)


@dataclass(frozen=True)
class Relationship:
    """An object property stored as an edge in PG and LPG.

    ``iri`` is the predicate; the edge runs subject → object. For
    ``rdf:type`` only the tag → tag-class statements become edges
    (``has_type``): every other ``rdf:type`` is a class assertion.
    """

    name: str
    iri: str
    pg_collection: str
    #: The one class every target belongs to (from the SF0.1 data), or None
    #: when targets span classes (``replyOf`` reaches posts and comments).
    #: Declared as ``rdfs:range`` so a constant object can be looked up.
    range: str | None = None


RELATIONSHIPS: tuple[Relationship, ...] = (
    Relationship("HAS_CREATOR", SNVOC + "hasCreator", "has_creator", "Person"),
    Relationship("REPLY_OF", SNVOC + "replyOf", "reply_of"),
    Relationship("CONTAINER_OF", SNVOC + "containerOf", "container_of", "Post"),
    Relationship("HAS_MODERATOR", SNVOC + "hasModerator", "has_moderator", "Person"),
    Relationship("HAS_TAG", SNVOC + "hasTag", "has_tag", "Tag"),
    Relationship("HAS_INTEREST", SNVOC + "hasInterest", "has_interest", "Tag"),
    Relationship("IS_LOCATED_IN", SNVOC + "isLocatedIn", "is_located_in"),
    Relationship("IS_PART_OF", SNVOC + "isPartOf", "is_part_of"),
    Relationship("HAS_TYPE", RDF_TYPE, "has_type", "TagClass"),
    Relationship("IS_SUBCLASS_OF", RDFS + "subClassOf", "is_subclass_of", "TagClass"),
)

#: ``snvoc:directKnows`` in PG and LPG: the ``knows`` edges, read in both
#: directions (``owl:SymmetricProperty``; plan decision D10). Each friendship
#: is stored once, so ``ANY`` returns every friend exactly once.
DIRECT_KNOWS_EDGE = Relationship("KNOWS", DIRECT_KNOWS, "knows", "Person")
RELATIONSHIP_BY_IRI = {r.iri: r for r in RELATIONSHIPS}


@dataclass(frozen=True)
class ReifiedRelationship:
    """A relationship the Turtle stores as an intermediate blank node.

    ``?source <entry> ?node . ?node <exit> ?target . ?node <attr> ?value``
    in RDF is one edge ``source → target`` carrying ``attr`` in PG and LPG.
    ``exits`` lists every predicate that leads from the node to the target
    (``likes`` reaches a Post through ``hasPost`` and a Comment through
    ``hasComment``).
    """

    name: str
    entry: str
    exits: tuple[str, ...]
    attributes: tuple[str, ...]
    pg_collection: str


REIFIED: tuple[ReifiedRelationship, ...] = (
    ReifiedRelationship("KNOWS", KNOWS, (HAS_PERSON,), (SNVOC + "creationDate",), "knows"),
    ReifiedRelationship(
        "HAS_MEMBER", SNVOC + "hasMember", (HAS_PERSON,), (SNVOC + "joinDate",), "has_member"
    ),
    ReifiedRelationship(
        "LIKES",
        SNVOC + "likes",
        (SNVOC + "hasPost", SNVOC + "hasComment"),
        (SNVOC + "creationDate",),
        "likes",
    ),
    ReifiedRelationship(
        "STUDY_AT", SNVOC + "studyAt", (SNVOC + "hasOrganisation",), (SNVOC + "classYear",), "study_at"
    ),
    ReifiedRelationship(
        "WORK_AT", SNVOC + "workAt", (SNVOC + "hasOrganisation",), (SNVOC + "workFrom",), "work_at"
    ),
)
REIFIED_BY_ENTRY = {r.entry: r for r in REIFIED}

#: The class every target of an exit predicate belongs to, where there is
#: one (plan D8). ``hasOrganisation`` reaches a University from ``studyAt``
#: and a Company from ``workAt``, so it declares none: the edge it follows
#: already ends at the right kind of node.
EXIT_RANGES: dict[str, str] = {
    HAS_PERSON: "Person",
    SNVOC + "hasPost": "Post",
    SNVOC + "hasComment": "Comment",
}

#: Datatype properties of nodes, and the document attribute each one is
#: stored under in PG and LPG (the IRI's local name). ``snvoc:url`` points
#: at untyped IRIs (``http://dbpedia.org/resource/…``); it is a value, not
#: a link to a node, so it is stored as the IRI string.
DATATYPE_PROPERTIES: tuple[str, ...] = (
    SNVOC + "id",
    SNVOC + "firstName",
    SNVOC + "lastName",
    SNVOC + "gender",
    SNVOC + "birthday",
    SNVOC + "creationDate",
    SNVOC + "locationIP",
    SNVOC + "browserUsed",
    SNVOC + "content",
    SNVOC + "length",
    SNVOC + "imageFile",
    SNVOC + "language",
    SNVOC + "title",
    SNVOC + "url",
    SNVOC + "speaks",
    SNVOC + "email",
    FOAF + "name",
    RDFS + "label",
)

#: Datatype properties LDBC's property-graph schema defines as arrays; PG
#: and LPG always store them as a list. Any other property is stored as a
#: scalar, or as a list only when a node really carries several values
#: (``dbpedia:Australia``'s two ``snvoc:id``). The translator reads both
#: shapes with ``fan_out_list_values`` (one RDF triple per element).
ARRAY_PROPERTIES: frozenset[str] = frozenset({SNVOC + "speaks", SNVOC + "email"})


def local_name(iri: str) -> str:
    """The attribute name a predicate IRI is stored under."""
    for sep in ("#", "/"):
        if sep in iri:
            iri = iri.rsplit(sep, 1)[1]
    return iri


#: The classes each predicate's subjects belong to, counted on the SF0.1
#: Turtle (2026-10-24) and checked against the loaded data by
#: ``test_ldbc_load_live.py``. Declared as ``rdfs:domain`` (a class, or an
#: ``owl:unionOf``) so the translator can route a subject no type pattern
#: names. A reified relationship's entry predicate declares the domain of its
#: source (plan D8).
_ALL_NODES = tuple(c.name for c in NODE_CLASSES)
DOMAINS: dict[str, tuple[str, ...]] = {
    SNVOC + "id": _ALL_NODES,
    SNVOC + "firstName": ("Person",),
    SNVOC + "lastName": ("Person",),
    SNVOC + "gender": ("Person",),
    SNVOC + "birthday": ("Person",),
    SNVOC + "email": ("Person",),
    SNVOC + "speaks": ("Person",),
    SNVOC + "creationDate": ("Person", "Forum", "Post", "Comment"),
    SNVOC + "locationIP": ("Person", "Post", "Comment"),
    SNVOC + "browserUsed": ("Person", "Post", "Comment"),
    SNVOC + "content": ("Post", "Comment"),
    SNVOC + "length": ("Post", "Comment"),
    SNVOC + "imageFile": ("Post",),
    SNVOC + "language": ("Post",),
    SNVOC + "title": ("Forum",),
    SNVOC + "url": ("TagClass", "University", "Company"),
    FOAF + "name": ("Tag", "City", "Country", "Continent", "University", "Company"),
    RDFS + "label": ("TagClass",),
    SNVOC + "hasCreator": ("Post", "Comment"),
    SNVOC + "replyOf": ("Comment",),
    SNVOC + "containerOf": ("Forum",),
    SNVOC + "hasModerator": ("Forum",),
    SNVOC + "hasTag": ("Forum", "Post", "Comment"),
    SNVOC + "hasInterest": ("Person",),
    SNVOC + "isLocatedIn": ("Person", "Post", "Comment", "University", "Company"),
    SNVOC + "isPartOf": ("City", "Country"),
    RDFS + "subClassOf": ("TagClass",),
    DIRECT_KNOWS: ("Person",),
    KNOWS: ("Person",),
    SNVOC + "hasMember": ("Forum",),
    SNVOC + "likes": ("Person",),
    SNVOC + "studyAt": ("Person",),
    SNVOC + "workAt": ("Person",),
}
