"""Vendored-dataset provenance + no-secrets static guard (T-07.1-01 / D-08).

Default-path guard: NOT ``@pytest.mark.eval``, NOT gated behind an
environment-variable skip — it never touches a provider or the network, so
it belongs on the same always-on, fast path as ``test_judge.py``. It
enforces two independent invariants over every immediate subdirectory of
``tests/nl2sparql/eval/vendored/``:

* **Test A (provenance):** each vendored dataset directory carries a
  ``NOTICE.md`` that names its CC-BY-4.0 license, an ``https://`` source
  URL, and a commit/version token — so an un-attributed or
  provenance-stripped dataset directory fails CI (T-07.1-02, D-08).
* **Test B (no secrets):** no file anywhere under ``vendored/**`` matches a
  known secret pattern (API key, AWS access key ID, PEM private key header,
  bearer token) — so a contributor's leaked credential embedded in
  third-party example data can never land in this repo (T-07.1-01).

Written BEFORE any dataset is vendored (test-first, per this plan's
objective): today ``vendored/`` doesn't exist, so both tests pass trivially.
They become load-bearing the moment QALD-9-plus (Plan 04) or CK25 (Plan 05)
land their first ``vendored/<name>/`` directory. The guard is intentionally
generic — it does not hard-code any dataset name — so it protects every
future vendored set without modification.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.nl2sparql.eval.runner import EVAL_DIR

VENDORED = EVAL_DIR / "vendored"

# A dataset directory's NOTICE.md must show its CC-BY-4.0 license...
_CC_BY_RE = re.compile(r"CC[- ]BY[- ]4\.0", re.IGNORECASE)
# ...a source URL...
_SOURCE_URL_RE = re.compile(r"https://\S+")
# ...and a commit SHA (7-40 hex chars) or a semver-ish version token, so the
# vendored snapshot is pinned to a specific upstream revision.
_COMMIT_OR_VERSION_RE = re.compile(r"\b(?:[0-9a-f]{7,40}|v?\d+\.\d+(?:\.\d+)?)\b", re.IGNORECASE)

# Secret patterns scanned over every vendored file (Information Disclosure,
# T-07.1-01). Kept intentionally small/high-signal — this is a static grep,
# not a full-fidelity secret scanner.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("OpenAI-style API key", re.compile(r"sk-[A-Za-z0-9]{16,}")),
    ("AWS access key ID", re.compile(r"AKIA[0-9A-Z]{12,}")),
    ("PEM private key header", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Bearer token", re.compile(r"Bearer\s+[A-Za-z0-9._-]{16,}")),
)

# Skip anything larger than this when scanning for secrets — vendored raw
# dataset dumps can be large JSON blobs; a byte-length cap keeps the guard
# fast without weakening the check on files a secret could plausibly hide in.
_MAX_SCAN_BYTES = 5 * 1024 * 1024


def _vendored_dataset_dirs() -> list[Path]:
    """Every immediate, real dataset subdirectory of ``vendored/``.

    Excludes ``__pycache__`` (and any other dunder-named directory): once a
    vendored set ships an ``__init__.py`` (so its conversion script is
    importable as ``tests.nl2sparql.eval.vendored.<set>.convert_*``), pytest
    collection byte-compiles it and Python drops a same-level
    ``vendored/__pycache__`` directory alongside the real dataset dirs — a
    filesystem artifact, never a dataset, and must not be mistaken for one
    missing its NOTICE.md.
    """
    if not VENDORED.is_dir():
        return []
    return sorted(p for p in VENDORED.iterdir() if p.is_dir() and not p.name.startswith("__"))


def test_every_vendored_dataset_has_a_cc_by_notice() -> None:
    """Every immediate subdirectory of ``vendored/`` must carry a NOTICE.md.

    Trivially passes today (no ``vendored/`` dir yet) — becomes load-bearing
    once Plans 04/05 vendor QALD-9-plus / CK25.
    """
    dataset_dirs = _vendored_dataset_dirs()
    if not dataset_dirs:
        return  # nothing vendored yet — nothing to enforce

    failures: list[str] = []
    for dataset_dir in dataset_dirs:
        notice_path = dataset_dir / "NOTICE.md"
        if not notice_path.is_file():
            failures.append(f"{dataset_dir.name}: missing NOTICE.md")
            continue
        text = notice_path.read_text(encoding="utf-8", errors="ignore")
        if not _CC_BY_RE.search(text):
            failures.append(f"{dataset_dir.name}/NOTICE.md: missing a CC-BY-4.0 marker")
        if not _SOURCE_URL_RE.search(text):
            failures.append(f"{dataset_dir.name}/NOTICE.md: missing an https:// source URL")
        if not _COMMIT_OR_VERSION_RE.search(text):
            failures.append(f"{dataset_dir.name}/NOTICE.md: missing a commit/version token")

    assert not failures, "vendored-dataset provenance guard failed:\n" + "\n".join(failures)


def test_no_secrets_committed_under_vendored() -> None:
    """No file under ``vendored/**`` may match a known secret pattern.

    Trivially passes today (no ``vendored/`` dir yet). Binary/oversized files
    are skipped defensively — a decode error or a file over
    ``_MAX_SCAN_BYTES`` is not scanned line-by-line (this guard's job is to
    catch committed *text* secrets, not to be a general-purpose binary
    scanner).
    """
    if not VENDORED.is_dir():
        return

    offenders: list[str] = []
    for path in sorted(VENDORED.rglob("*")):
        if not path.is_file():
            continue
        if path.stat().st_size > _MAX_SCAN_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable — not a text-secret carrier

        for line_no, line in enumerate(text.splitlines(), start=1):
            for pattern_name, pattern in _SECRET_PATTERNS:
                if pattern.search(line):
                    rel = path.relative_to(EVAL_DIR)
                    offenders.append(f"{rel}:{line_no}: matched {pattern_name!r} pattern")

    assert not offenders, "secret-pattern scan found committed secret(s):\n" + "\n".join(offenders)
