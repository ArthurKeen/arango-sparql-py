"""Fetch the LDBC SNB Interactive v1 Turtle data sets on demand.

Nothing here is vendored (AGENTS.md *Off-limits*; plan decision D6): each
archive is downloaded once, checked against a pinned SHA-256, unpacked
into a cache directory and reused. ``scripts/fetch_ldbc.sh`` is the
command-line entry point; CI caches the directory with ``actions/cache``.

Usage::

    python -m tests.ldbc.datasets --sf 0.1           # data set
    python -m tests.ldbc.datasets --sf 0.1 --params  # + substitution parameters
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_URL = "https://datasets.ldbcouncil.org"

#: Read timeout for one network read, in seconds. Large archives stream in
#: chunks, so this bounds a stall, not the whole download.
DOWNLOAD_TIMEOUT_S = 120.0


@dataclass(frozen=True)
class Archive:
    """One pinned download: where it comes from and what it must hash to."""

    url: str
    sha256: str
    size_bytes: int
    #: Directory name the archive unpacks to, relative to the cache root.
    unpacks_to: str


#: Turtle data sets, keyed by scale factor. Only pinned scale factors can
#: be fetched; add a row (with its digest, measured by downloading it) to
#: support another.
TURTLE: dict[str, Archive] = {
    "0.1": Archive(
        url=f"{BASE_URL}/snb-interactive-v1/social_network-sf0.1-Turtle.tar.zst",
        sha256="7bc10912d0844be4240de3ca648cc4282b4a29a0be6dc3872f3ce52cdf05485e",
        size_bytes=27_275_571,
        unpacks_to="social_network-sf0.1-Turtle",
    ),
}

#: Official substitution parameters (query bindings), keyed by scale factor.
#: They cover the complex reads only; the LDBC driver derives the short
#: reads' bindings at run time (see :mod:`tests.ldbc.params`).
SUBSTITUTION_PARAMETERS: dict[str, Archive] = {
    "0.1": Archive(
        url=f"{BASE_URL}/snb-interactive-v1-parameters/substitution_parameters-sf0.1.tar.zst",
        sha256="c1ac2db1c1b8166dd2d466341e9682fb92e12d856f0b40e1be65da9cb9d008ff",
        size_bytes=203_161,
        unpacks_to="substitution_parameters-sf0.1",
    ),
}


class DatasetError(RuntimeError):
    """A data set could not be fetched, verified or unpacked."""


def cache_root() -> Path:
    """The cache directory: ``$LDBC_CACHE_DIR``, else ``~/.cache/arango-sparql/ldbc``."""
    configured = os.getenv("LDBC_CACHE_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cache" / "arango-sparql" / "ldbc"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(archive: Archive, dest: Path) -> None:
    """Download *archive* to *dest* atomically, verifying its digest."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dest.parent, suffix=".part", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        try:
            logger.info("downloading %s (%d bytes)", archive.url, archive.size_bytes)
            with urllib.request.urlopen(archive.url, timeout=DOWNLOAD_TIMEOUT_S) as response:  # noqa: S310 — pinned https URL
                shutil.copyfileobj(response, tmp, length=1 << 20)
        except OSError as exc:
            tmp_path.unlink(missing_ok=True)
            raise DatasetError(f"download of {archive.url} failed: {exc}") from exc
    actual = sha256_of(tmp_path)
    if actual != archive.sha256:
        tmp_path.unlink(missing_ok=True)
        raise DatasetError(
            f"SHA-256 mismatch for {archive.url}: expected {archive.sha256}, got {actual}. "
            "The upstream file changed; re-pin it only after checking why."
        )
    tmp_path.replace(dest)


def _unpack(archive_path: Path, into: Path) -> None:
    """Unpack a ``.tar.zst`` with the system ``tar`` and ``zstd``."""
    if shutil.which("zstd") is None:
        raise DatasetError("the zstd binary is required to unpack LDBC archives (brew/apt install zstd)")
    into.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["tar", "--use-compress-program=zstd -d", "-xf", str(archive_path), "-C", str(into)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise DatasetError(f"unpacking {archive_path} failed: {result.stderr.strip()}")


def fetch(archive: Archive, *, root: Path | None = None) -> Path:
    """Return the unpacked directory for *archive*, fetching it if needed."""
    root = root or cache_root()
    target = root / archive.unpacks_to
    if target.is_dir() and any(target.iterdir()):
        return target
    archive_path = root / "archives" / Path(archive.url).name
    if not archive_path.is_file() or sha256_of(archive_path) != archive.sha256:
        _download(archive, archive_path)
    _unpack(archive_path, root)
    if not target.is_dir():
        raise DatasetError(f"{archive_path} did not unpack to {target}")
    return target


def turtle_files(sf: str, *, root: Path | None = None) -> list[Path]:
    """The Turtle files of scale factor *sf*, fetching the data set if needed."""
    if sf not in TURTLE:
        raise DatasetError(f"scale factor {sf!r} is not pinned; known: {sorted(TURTLE)}")
    directory = fetch(TURTLE[sf], root=root)
    files = sorted(directory.glob("*.ttl"))
    if not files:
        raise DatasetError(f"no .ttl files in {directory}")
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--sf", default="0.1", help="scale factor (default: 0.1)")
    parser.add_argument("--params", action="store_true", help="also fetch the substitution parameters")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        for path in turtle_files(args.sf):
            print(path)
        if args.params:
            if args.sf not in SUBSTITUTION_PARAMETERS:
                raise DatasetError(f"no pinned substitution parameters for scale factor {args.sf!r}")
            print(fetch(SUBSTITUTION_PARAMETERS[args.sf]))
    except DatasetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
