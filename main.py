"""ASGI entry point for the ``arango_sparql.service`` FastAPI app.

Mirrors ``arango-cypher-py/main.py`` so deployment scripts (ServiceMaker,
Container Manager, plain ``uvicorn``) work the same across both repos.
"""

from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")

    uvicorn.run(
        "arango_sparql.service:app",
        host=host,
        port=port,
        reload=False,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
