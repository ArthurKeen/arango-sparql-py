"""Run the shared editor core (``arango_sparql/editor/sparql_editor_core.js``)
under node, so its rules are tested in CI like any other code here.

Without node the tests skip locally; in CI (``CI`` set) a missing node is a
failure, so the core never goes untested there.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import arango_sparql.editor as editor

NODE = shutil.which("node")
CORE = Path(editor.__file__).with_name(editor.CORE_JS_RESOURCE)

# Reads [[fn, args], ...] on stdin, writes the JSON list of results.
_DRIVER = (
    "const core = require(process.argv[1]);"
    "let d = '';"
    "process.stdin.on('data', (c) => (d += c));"
    "process.stdin.on('end', () => {"
    "  const out = JSON.parse(d).map(([fn, args]) => core[fn](...args));"
    "  process.stdout.write(JSON.stringify(out));"
    "});"
)


def require_node() -> str:
    if NODE is None:
        if os.environ.get("CI"):
            pytest.fail("node is required to test the shared editor core in CI")
        pytest.skip("node is not installed")
    return NODE


def run_node(script: str, *args: str, stdin: str = "") -> str:
    """Run *script* with ``node -e``; *args* land in ``process.argv[1:]``."""
    node = require_node()
    result = subprocess.run(
        [node, "-e", script, *args], input=stdin, capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, f"node failed ({result.returncode}): {result.stderr}"
    return result.stdout


def call_many(calls: list[tuple[str, list[Any]]]) -> list[Any]:
    return json.loads(run_node(_DRIVER, str(CORE), stdin=json.dumps([[fn, args] for fn, args in calls])))


def call(fn: str, *args: Any) -> Any:
    return call_many([(fn, list(args))])[0]
