"""Importing the NL library must not boot the service (issue #66).

``arango_sparql.nl2sparql`` re-exports the service's Pydantic contract models
(rule 300). Done eagerly, that import ran ``arango_sparql/service/__init__``
and the analyzer startup guard (PRD §6.3.4), so a ``[nl]`` install without
``[analyzer]`` could not import ``NlPipeline`` at all. The subprocess tests
reproduce that install: the analyzer is made unimportable and the guard runs
with its production defaults (the suite's own process has the analyzer, so the
failure is only visible in a fresh interpreter).
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import arango_sparql.nl2sparql as nl2sparql
import arango_sparql.nl2sparql.models as nl_models
import arango_sparql.service.models as service_models

_REPO = Path(__file__).resolve().parents[2]

#: The service contract models nl2sparql re-exports (rule 300), listed here
#: rather than read from the module so the test pins the contract itself.
_CONTRACT_MODELS = (
    "NlExecuteRequest",
    "NlExecuteResponse",
    "NlExplainRequest",
    "NlExplainResponse",
    "NlTranslateRequest",
    "NlTranslateResponse",
)

#: Prepended to every subprocess: ``import schema_analyzer`` raises, as it does
#: when the ``[analyzer]`` extra is not installed.
_BLOCK_ANALYZER = textwrap.dedent(
    """
    import sys
    for _name in [m for m in sys.modules if m == "schema_analyzer" or m.startswith("schema_analyzer.")]:
        del sys.modules[_name]
    sys.modules["schema_analyzer"] = None
    """
)


def _run_without_analyzer(code: str) -> subprocess.CompletedProcess[str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("ARANGO_SPARQL_SKIP_STARTUP_GUARD", "SCHEMA_ANALYZER_REQUIRED")
    }
    # The checkout under test first, so a PR worktree is what gets imported.
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(_REPO), env.get("PYTHONPATH", "")) if p)
    return subprocess.run(
        [sys.executable, "-c", _BLOCK_ANALYZER + textwrap.dedent(code)],
        capture_output=True,
        text=True,
        env=env,
        cwd=_REPO,
        timeout=120,
        check=False,
    )


def test_nl_library_imports_without_the_analyzer_and_leaves_the_service_unloaded() -> None:
    proc = _run_without_analyzer(
        """
        import sys
        import arango_sparql.api
        import arango_sparql.translate
        import arango_sparql.nl2sparql
        from arango_sparql.nl2sparql import NlPipeline, nl_to_sparql
        from arango_sparql.nl2sparql.pipeline import NlPipeline as P2
        from arango_sparql.nl2sparql.models import LLMResponse, PipelineOutcome
        from arango_sparql.nl2sparql.engine_adapter import EngineProviderBridge
        assert NlPipeline is P2
        loaded = sorted(m for m in sys.modules if m == "arango_sparql.service" or m.startswith("arango_sparql.service."))
        print("service modules:", loaded)
        """
    )
    assert proc.returncode == 0, proc.stderr
    assert "service modules: []" in proc.stdout


def test_star_imports_without_the_analyzer_leave_the_service_unloaded() -> None:
    """A star import evaluates every ``__all__`` name, so the lazy service
    models must stay out of ``__all__`` or it boots the service again."""
    proc = _run_without_analyzer(
        """
        import sys
        ns = {}
        exec("from arango_sparql.nl2sparql import *", ns)
        exec("from arango_sparql.nl2sparql.models import *", ns)
        assert "NlPipeline" in ns and "PipelineOutcome" in ns
        assert "NlTranslateRequest" not in ns
        loaded = sorted(m for m in sys.modules if m == "arango_sparql.service" or m.startswith("arango_sparql.service."))
        print("service modules:", loaded)
        """
    )
    assert proc.returncode == 0, proc.stderr
    assert "service modules: []" in proc.stdout


def test_library_import_leaves_logging_to_the_caller() -> None:
    """The service's ``configure_observability`` (handler + INFO on the
    ``arango_sparql`` logger) runs only when the service is imported."""
    proc = _run_without_analyzer(
        """
        import logging
        import arango_sparql.nl2sparql
        lg = logging.getLogger("arango_sparql")
        print("handlers:", len(lg.handlers), "level:", lg.level)
        """
    )
    assert proc.returncode == 0, proc.stderr
    assert "handlers: 0 level: 0" in proc.stdout


def test_the_service_still_refuses_to_boot_without_the_analyzer() -> None:
    """The fix moves the NL import off the guard; it must not weaken the guard."""
    proc = _run_without_analyzer("import arango_sparql.service\n")
    assert proc.returncode != 0
    assert "AnalyzerStartupGuardError" in proc.stderr


@pytest.mark.parametrize("name", _CONTRACT_MODELS)
def test_lazy_names_resolve_to_the_service_classes(name: str) -> None:
    service_cls = getattr(service_models, name)
    assert getattr(nl_models, name) is service_cls
    assert getattr(nl2sparql, name) is service_cls
    # Out of __all__ on purpose: a star import would otherwise boot the service.
    assert name not in nl_models.__all__
    assert name not in nl2sparql.__all__


def test_from_import_of_a_lazy_name_works() -> None:
    from arango_sparql.nl2sparql import NlExecuteRequest
    from arango_sparql.nl2sparql.models import NlTranslateResponse

    assert NlExecuteRequest is service_models.NlExecuteRequest
    assert NlTranslateResponse is service_models.NlTranslateResponse


def test_every_public_name_resolves() -> None:
    for module in (nl2sparql, nl_models):
        for name in module.__all__:
            assert getattr(module, name) is not None, f"{module.__name__}.{name}"


def test_unknown_attribute_is_still_an_attribute_error() -> None:
    with pytest.raises(AttributeError, match="no attribute 'NoSuchModel'"):
        _ = nl_models.NoSuchModel  # type: ignore[attr-defined]
    with pytest.raises(AttributeError, match="no attribute 'NoSuchModel'"):
        _ = nl2sparql.NoSuchModel  # type: ignore[attr-defined]
