"""Typer-based CLI for ``arango-sparql-py``.

Mirrors ``arango_cypher.cli`` so cross-repo muscle memory works:

    arango-sparql-py translate <file.sparql> --ontology schema.ttl
    arango-sparql-py serve --port 8000
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    import typer
except ImportError:  # pragma: no cover - typer is in [cli] extra
    typer = None  # type: ignore[assignment]


if typer is not None:
    app = typer.Typer(add_completion=False, no_args_is_help=True)

    @app.command()
    def translate(
        sparql_file: Path = typer.Argument(..., exists=True, readable=True),
        ontology: Path | None = typer.Option(None, "--ontology", "-o", exists=True, readable=True),
    ) -> None:
        """Translate a SPARQL file to AQL and print the result."""
        from rdflib import Graph

        from .api import translate as _translate
        from .translate.resolver import SchemaResolver

        graph = Graph()
        if ontology:
            graph.parse(ontology, format="turtle")
        resolver = SchemaResolver(ontology=graph)
        result = _translate(sparql_file.read_text(), resolver=resolver)
        typer.echo(result.aql)
        if result.bind_vars:
            typer.echo("--- bind vars ---")
            for k, v in result.bind_vars.items():
                typer.echo(f"  {k} = {v!r}")

    @app.command()
    def serve(
        port: int = typer.Option(int(os.getenv("PORT", "8000")), "--port", "-p"),
        host: str = typer.Option(os.getenv("HOST", "0.0.0.0"), "--host", "-h"),
    ) -> None:
        """Run the FastAPI service."""
        import uvicorn

        uvicorn.run(
            "arango_sparql.service:app",
            host=host,
            port=port,
            reload=False,
            proxy_headers=True,
            forwarded_allow_ips="*",
        )

else:  # pragma: no cover

    def app() -> None:  # type: ignore[no-redef]
        raise SystemExit("Install the [cli] extra to use the CLI: pip install 'arango-sparql-py[cli]'")
