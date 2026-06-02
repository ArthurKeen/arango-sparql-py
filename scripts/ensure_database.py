"""Create the configured ArangoDB database if it does not exist.

Reads ``ARANGO_URL`` / ``ARANGO_DB`` / ``ARANGO_USER`` / ``ARANGO_PASSWORD``
from the environment (and ``.env`` if ``python-dotenv`` is installed),
then ensures the ``ARANGO_DB`` database exists — creating it via the
``_system`` catalogue when absent. ArangoDB never auto-creates databases,
so this is the one-shot provisioning step before pointing the service /
UI at a fresh database such as ``sparql-to-aql``.

The same logic runs automatically at service startup in non-public mode
(see ``arango_sparql/service/app.py``); this script is the explicit,
out-of-band equivalent for operators who want to provision before
booting the service or who run a public-mode deployment.

USAGE::

    python scripts/ensure_database.py            # use .env / environment
    ARANGO_DB=demo python scripts/ensure_database.py   # override per-run

Exit codes: ``0`` on success (created or already-exists), ``1`` on a
connection / permission failure (with the reason printed to stderr).
"""

from __future__ import annotations

import logging
import sys

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is a dev/runtime convenience
    pass

from arango_sparql._env import read_arango_database, read_arango_url
from arango_sparql.arango_admin import ensure_configured_database


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    name = read_arango_database(caller="ensure_database_script")
    url = read_arango_url(default="http://localhost:8529", caller="ensure_database_script")

    if not name or name == "_system":
        print(
            f"ARANGO_DB={name!r}: nothing to provision "
            "(_system always exists; set ARANGO_DB to a dedicated database).",
            file=sys.stderr,
        )
        return 0

    status = ensure_configured_database()
    if status == "created":
        print(f"Created database {name!r} at {url}.")
        return 0
    if status == "exists":
        print(f"Database {name!r} already exists at {url}.")
        return 0
    # ``None`` → ensure_configured_database already logged the reason at
    # WARNING; surface a non-zero exit so CI / shell callers can branch.
    print(
        f"Failed to ensure database {name!r} at {url}; see the warning above.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
