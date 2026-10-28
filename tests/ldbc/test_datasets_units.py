"""Unit tests for fetching the pinned LDBC archives: sources, mirror, digest.

Every source here is real, so nothing is mocked: ``file://`` URLs, which
``urllib`` reads the same way it reads LDBC's https one, and a local HTTP
server for a body cut short.
"""

from __future__ import annotations

import hashlib
import http.server
import threading
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.ldbc import datasets

pytestmark = pytest.mark.ldbc

_PATH = "/snb-interactive-v1/archive.tar.zst"
_CONTENT = b"pinned archive bytes"


def _archive(upstream: Path, content: bytes = _CONTENT) -> datasets.Archive:
    return datasets.Archive(
        url=upstream.as_uri() + _PATH,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        unpacks_to="archive",
    )


def _serve(root: Path, archive: datasets.Archive, content: bytes) -> None:
    """Put *content* where a source rooted at *root* serves *archive*: the
    archive URL's path below *root* (for LDBC's own ``file://`` URL, *root*
    is the filesystem root)."""
    target = root / urlsplit(archive.url).path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


@pytest.fixture(autouse=True)
def _no_mirror(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(datasets.MIRROR_ENV, raising=False)


def test_without_a_mirror_only_ldbc_is_tried(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "ldbc")
    assert datasets.source_urls(archive) == [archive.url]


@pytest.mark.parametrize("mirror", ["https://mirror.example/ldbc", "https://mirror.example/ldbc/"])
def test_a_mirror_is_tried_first_at_the_same_path(monkeypatch: pytest.MonkeyPatch, mirror: str) -> None:
    monkeypatch.setenv(datasets.MIRROR_ENV, mirror)
    archive = datasets.TURTLE["0.1"]
    assert datasets.source_urls(archive) == [
        "https://mirror.example/ldbc/snb-interactive-v1/social_network-sf0.1-Turtle.tar.zst",
        archive.url,
    ]


def test_a_blank_mirror_counts_as_unset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The workflow passes ``${{ vars.LDBC_DATASET_MIRROR }}``, empty when unset.
    monkeypatch.setenv(datasets.MIRROR_ENV, "  ")
    archive = _archive(tmp_path / "ldbc")
    assert datasets.source_urls(archive) == [archive.url]


def test_the_mirror_serves_the_archive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    archive = _archive(tmp_path / "ldbc")  # nothing at LDBC's URL
    _serve(tmp_path / "mirror", archive, _CONTENT)
    monkeypatch.setenv(datasets.MIRROR_ENV, (tmp_path / "mirror").as_uri())
    dest = tmp_path / "cache" / "archive.tar.zst"
    datasets._download(archive, dest)
    assert dest.read_bytes() == _CONTENT


def test_an_unreachable_mirror_falls_back_to_ldbc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    archive = _archive(tmp_path / "ldbc")
    _serve(Path("/"), archive, _CONTENT)
    monkeypatch.setenv(datasets.MIRROR_ENV, (tmp_path / "empty-mirror").as_uri())
    dest = tmp_path / "cache" / "archive.tar.zst"
    datasets._download(archive, dest)
    assert dest.read_bytes() == _CONTENT


def test_a_mirror_serving_other_bytes_is_refused_not_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # LDBC's copy is fine, but a mirror that changed the data is a fault to
    # fix, so the fetch stops there instead of quietly using the next source.
    archive = _archive(tmp_path / "ldbc")
    # Same size as the pin, so it is other data, not a transfer cut short.
    _serve(tmp_path / "mirror", archive, b"X" * len(_CONTENT))
    _serve(Path("/"), archive, _CONTENT)
    monkeypatch.setenv(datasets.MIRROR_ENV, (tmp_path / "mirror").as_uri())
    dest = tmp_path / "cache" / "archive.tar.zst"
    with pytest.raises(datasets.DatasetError, match="SHA-256 mismatch for file://.*mirror"):
        datasets._download(archive, dest)
    assert not dest.exists()
    assert not list(dest.parent.glob("*.part"))


def test_a_mirror_serving_more_bytes_than_pinned_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive = _archive(tmp_path / "ldbc")
    _serve(tmp_path / "mirror", archive, _CONTENT + b"extra")
    _serve(Path("/"), archive, _CONTENT)
    monkeypatch.setenv(datasets.MIRROR_ENV, (tmp_path / "mirror").as_uri())
    with pytest.raises(datasets.DatasetError, match="SHA-256 mismatch"):
        datasets._download(archive, tmp_path / "cache" / "archive.tar.zst")


def test_every_source_failing_names_each_and_suggests_a_mirror(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "ldbc")
    dest = tmp_path / "cache" / "archive.tar.zst"
    with pytest.raises(datasets.DatasetError) as caught:
        datasets._download(archive, dest)
    message = str(caught.value)
    assert archive.url in message
    assert datasets.MIRROR_ENV in message
    assert not list(dest.parent.glob("*.part"))


def test_with_a_mirror_set_the_failure_lists_both_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(datasets.MIRROR_ENV, (tmp_path / "mirror").as_uri())
    archive = _archive(tmp_path / "ldbc")
    with pytest.raises(datasets.DatasetError) as caught:
        datasets._download(archive, tmp_path / "cache" / "archive.tar.zst")
    message = str(caught.value)
    assert (tmp_path / "mirror").as_uri() + urlsplit(archive.url).path in message
    assert archive.url in message
    assert f"Set {datasets.MIRROR_ENV}" not in message


class _TruncatingHandler(http.server.BaseHTTPRequestHandler):
    """Sends the first half of the archive, then closes the connection.

    ``chunked`` picks the framing: a ``Content-Length`` promising the whole
    archive (``http.client`` then returns short without raising), or chunked
    transfer with no closing chunk (``http.client`` raises ``IncompleteRead``,
    which is not an ``OSError``).
    """

    chunked = False

    def do_GET(self) -> None:
        half = _CONTENT[: len(_CONTENT) // 2]
        self.send_response(200)
        if self.chunked:
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(f"{len(half):x}\r\n".encode() + half + b"\r\n")
        else:
            self.send_header("Content-Length", str(len(_CONTENT)))
            self.end_headers()
            self.wfile.write(half)
        self.close_connection = True

    def log_message(self, *args: object) -> None:  # keep test output quiet
        pass


class _ChunkedTruncatingHandler(_TruncatingHandler):
    protocol_version = "HTTP/1.1"  # chunked framing needs HTTP/1.1
    chunked = True


@pytest.fixture(params=[_TruncatingHandler, _ChunkedTruncatingHandler], ids=["content-length", "chunked"])
def truncating_server(request: pytest.FixtureRequest) -> Iterator[str]:
    server = http.server.HTTPServer(("127.0.0.1", 0), request.param)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_a_body_cut_short_falls_back_to_the_next_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, truncating_server: str
) -> None:
    # A transfer that ends early is a failed source, not one serving other
    # bytes: the fetch moves on and leaves no .part file behind.
    archive = _archive(tmp_path / "ldbc")
    _serve(Path("/"), archive, _CONTENT)
    monkeypatch.setenv(datasets.MIRROR_ENV, truncating_server)
    dest = tmp_path / "cache" / "archive.tar.zst"
    datasets._download(archive, dest)
    assert dest.read_bytes() == _CONTENT
    assert not list(dest.parent.glob("*.part"))


def test_a_body_cut_short_everywhere_is_a_dataset_error(tmp_path: Path, truncating_server: str) -> None:
    archive = datasets.Archive(
        url=truncating_server + _PATH,
        sha256=hashlib.sha256(_CONTENT).hexdigest(),
        size_bytes=len(_CONTENT),
        unpacks_to="archive",
    )
    dest = tmp_path / "cache" / "archive.tar.zst"
    with pytest.raises(datasets.DatasetError, match="failed from every source"):
        datasets._download(archive, dest)
    assert not list(dest.parent.glob("*.part"))
