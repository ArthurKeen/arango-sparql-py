"""Query bindings: official parameters in, spliced SPARQL text out (WP-LDBC-PARAMS).

The reference splices parameters into the query text (``%personId%``), and
so do we, with the reference's own formatting (``GraphDBConverter``): person
ids are zero-padded to 20 digits inside IRIs, strings are inserted as-is.
One deliberate difference: dates are written as UTC with an explicit ``Z``
(``2012-11-28T00:00:00.000Z``). The reference formats them in the JVM's
default time zone with no zone marker; on a UTC machine that is the same
instant, and the explicit zone keeps comparisons with the data (all UTC)
well defined.

Bindings per query are committed in ``params/sf<SF>.yml`` and regenerated
with ``python -m tests.ldbc.params --sf 0.1 --write``:

* complex reads: the first :data:`BINDINGS_PER_QUERY` rows of LDBC's
  official substitution parameters;
* short reads (LDBC derives these at run time, from earlier results): the
  first persons of the IC1 parameters, and each one's newest message;
* every read: one binding that matches nothing (a person or message id
  absent from the data), because the empty case is an edge case too.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

BINDINGS_PER_QUERY = 3
PARAMS_DIR = Path(__file__).parent / "params"

#: An id no LDBC entity has; :func:`generate` checks it against the data.
#: (Small ids are taken by places and tags; generated person and message ids
#: stay below 10**15 at the scale factors pinned here.)
ABSENT_ID = "99999999999999999"

_PERSON_IDS = {"personId", "person1Id", "person2Id"}
_DATES = {"maxDate", "startDate", "minDate"}
_INTEGERS = {"durationDays", "month", "workFromYear", "messageId"}
_PLACEHOLDER = re.compile(r"%(\w+)%")


class ParamsError(RuntimeError):
    """A binding does not fit the query it is for."""


def format_value(name: str, raw: str) -> str:
    """Format one raw parameter the way the reference splices it."""
    if name in _PERSON_IDS:
        return f"{int(raw):020d}"
    if name in _DATES:
        instant = datetime.fromtimestamp(int(raw) / 1000, tz=UTC)
        return instant.strftime("%Y-%m-%dT%H:%M:%S.") + f"{instant.microsecond // 1000:03d}Z"
    if name in _INTEGERS:
        return str(int(raw))
    if any(c in raw for c in '"\\\n<>'):
        raise ParamsError(f"parameter {name}={raw!r} would break out of its quotes")
    return raw


def placeholders(sparql: str) -> set[str]:
    return set(_PLACEHOLDER.findall(sparql))


def splice(sparql: str, binding: dict[str, str]) -> str:
    """Replace every ``%name%`` with the formatted binding; all must be bound."""
    missing = placeholders(sparql) - binding.keys()
    if missing:
        raise ParamsError(f"no value for {sorted(missing)}")
    return _PLACEHOLDER.sub(lambda m: format_value(m.group(1), binding[m.group(1)]), sparql)


def load(sf: str) -> dict[str, list[dict[str, str]]]:
    """Committed bindings: ``{op: [binding, …]}``; the last one matches nothing."""
    path = PARAMS_DIR / f"sf{sf}.yml"
    data = yaml.safe_load(path.read_text())
    return {op: [dict(b) for b in bindings] for op, bindings in data["bindings"].items()}


# ---------------------------------------------------------------------------
# Generation (needs the data set and the oracle store)
# ---------------------------------------------------------------------------


def _official(params_dir: Path, number: int) -> list[dict[str, str]]:
    with (params_dir / f"interactive_{number}_param.txt").open(newline="") as handle:
        return list(csv.DictReader(handle, delimiter="|"))


def _newest_message(store: Any, person_id: str) -> str:
    from tests.ldbc import oracle

    result = oracle.run(
        store,
        f"""PREFIX snvoc: <http://www.ldbc.eu/ldbc_socialnet/1.0/vocabulary/>
        PREFIX sn: <http://www.ldbc.eu/ldbc_socialnet/1.0/data/>
        SELECT ?id WHERE {{
            ?m snvoc:hasCreator sn:pers{int(person_id):020d} ; snvoc:id ?id ; snvoc:creationDate ?d .
        }} ORDER BY DESC(?d) ?id LIMIT 1""",
    )
    if not result.rows:
        raise ParamsError(f"person {person_id} created no messages")
    return str(result.rows[0]["id"])


def generate(sf: str, params_dir: Path, store: Any) -> dict[str, Any]:
    from tests.ldbc import oracle

    absent = oracle.run(
        store,
        "PREFIX snvoc: <http://www.ldbc.eu/ldbc_socialnet/1.0/vocabulary/> "
        f"SELECT ?x WHERE {{ ?x snvoc:id ?i FILTER(?i = {ABSENT_ID}) }} LIMIT 1",
    )
    if absent.rows:
        raise ParamsError(f"id {ABSENT_ID} exists in the data; pick another ABSENT_ID")

    bindings: dict[str, list[dict[str, str]]] = {}
    for number in range(1, 15):
        rows = _official(params_dir, number)[:BINDINGS_PER_QUERY]
        empty = dict(rows[0])
        for key in empty.keys() & _PERSON_IDS:
            empty[key] = ABSENT_ID
        bindings[f"interactive-complex-{number}"] = [*rows, empty]

    persons = [row["personId"] for row in _official(params_dir, 1)[:BINDINGS_PER_QUERY]]
    messages = [_newest_message(store, p) for p in persons]
    for number in (1, 2, 3):
        bindings[f"interactive-short-{number}"] = [{"personId": p} for p in persons] + [
            {"personId": ABSENT_ID}
        ]
    for number in (4, 5, 6, 7):
        bindings[f"interactive-short-{number}"] = [{"messageId": m} for m in messages] + [
            {"messageId": ABSENT_ID}
        ]
    return {
        "scale_factor": sf,
        "source": "LDBC substitution_parameters-sf" + sf + " (complex reads); derived (short reads)",
        "bindings": dict(sorted(bindings.items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--sf", default="0.1")
    parser.add_argument("--write", action="store_true", help="write params/sf<SF>.yml")
    args = parser.parse_args(argv)
    from tests.ldbc import datasets, normalize, oracle

    nt_path, summary = normalize.normalize(
        datasets.turtle_files(args.sf), datasets.cache_root() / f"sf{args.sf}-normalized"
    )
    store = oracle.open_store(nt_path, summary.sha256)
    params_dir = datasets.fetch(datasets.SUBSTITUTION_PARAMETERS[args.sf])
    data = generate(args.sf, params_dir, store)
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    if args.write:
        PARAMS_DIR.mkdir(exist_ok=True)
        (PARAMS_DIR / f"sf{args.sf}.yml").write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
