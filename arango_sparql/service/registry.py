"""Process-wide extension registry for ``arango_sparql.service``.

Mirror of ``arango_cypher.service.registry`` — the Cypher project uses
this module to construct a single ``ExtensionRegistry`` at package
import time so every translate / execute / explain request reuses the
same instance instead of paying the registration cost per request.

``arango-sparql-py`` does not yet ship a dedicated ``extensions``
subpackage (no AQL helper functions registered for SPARQL→AQL
translation today), so :data:`_default_registry` is :data:`None`. The
module exists to lock in the import path that the ``/translate`` and
``/execute`` routes use; when extensions land, swap the body for the
Cypher project's ``_build_registry()`` shape:

.. code-block:: python

    from arango_query_core import ExtensionPolicy, ExtensionRegistry
    from ..extensions import register_all_extensions

    def _build_registry() -> ExtensionRegistry:
        reg = ExtensionRegistry(policy=ExtensionPolicy(enabled=True))
        register_all_extensions(reg)
        return reg

    _default_registry = _build_registry()

The route layer is already structured to pass ``_default_registry`` (or
``None``) through to the translation pipeline once
:func:`arango_sparql.api.translate` grows a ``registry=`` parameter, so
no consumer changes will be required when this stub is filled in.
"""

from __future__ import annotations

from typing import Any


def _build_registry() -> Any | None:
    """Construct the process-wide registry. Returns ``None`` until the
    SPARQL extensions subpackage exists; see module docstring for the
    shape this function takes once extensions land.
    """
    return None


_default_registry: Any | None = _build_registry()
