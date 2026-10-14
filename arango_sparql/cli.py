"""Typer-based CLI for ``arango-sparql-py``.

Mirrors ``arango_cypher.cli`` so cross-repo muscle memory works:

    arango-sparql-py translate <file.sparql> --ontology schema.ttl
    arango-sparql-py serve --port 8000
    arango-sparql-py mine-examples --provider openai --db IAM --graph IAM_DEMO
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Any

#: LLM backends ``mine-examples`` can be pointed at, by name, and the API-key
#: variable each reads before falling back to ``NL2SPARQL_API_KEY``.
_MINING_PROVIDERS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def _connection_target(host: str | None, port: int | None) -> tuple[str, str]:
    """``(url, auth_method)`` from flags, then ``ARANGO_URL``, then host/port env.

    Ported from arango-cypher-py's ``cli._connection_target``. Explicit
    ``--host`` / ``--port`` win; otherwise ``ARANGO_URL`` names the coordinator,
    so an HTTPS cluster is reachable. Auth is JWT for HTTPS (platform clusters
    such as prod.demo refuse HTTP Basic) and Basic for HTTP;
    ``ARANGO_AUTH_METHOD=basic|jwt`` overrides.
    """
    from ._env import read_arango_url

    if host or port:
        url = f"http://{host or os.getenv('ARANGO_HOST', 'localhost')}:{port or int(os.getenv('ARANGO_PORT', '8529'))}"
    else:
        url = (read_arango_url(caller="arango_sparql.cli") or "").strip() or (
            f"http://{os.getenv('ARANGO_HOST', 'localhost')}:{os.getenv('ARANGO_PORT', '8529')}"
        )
    method = os.getenv("ARANGO_AUTH_METHOD", "").strip().lower()
    if method not in ("basic", "jwt"):
        method = "jwt" if url.lower().startswith("https://") else "basic"
    return url.rstrip("/"), method


def _connect(
    host: str | None, port: int | None, db: str | None, user: str | None, password: str | None
) -> Any:
    """A python-arango ``StandardDatabase`` from flags / env vars / defaults."""
    from arango import ArangoClient

    from ._env import read_arango_database, read_arango_password, read_arango_username

    url, auth_method = _connection_target(host, port)
    database = db or read_arango_database(caller="arango_sparql.cli") or "_system"
    username = user or read_arango_username(caller="arango_sparql.cli") or "root"
    secret = password if password is not None else read_arango_password(caller="arango_sparql.cli")
    return ArangoClient(hosts=url).db(
        database, username=username, password=secret or "", auth_method=auth_method
    )


def _mining_client(name: str, model: str | None) -> Any:
    """The named LLM client, or ``None`` when it has no API key.

    Mining spends on an LLM only when told which: the provider is an explicit
    flag, never inferred from whichever key happens to be configured.
    """
    from .nl2sparql.client import AnthropicClient, OpenAICompatibleClient

    api_key = os.getenv(_MINING_PROVIDERS[name]) or None  # else the client reads NL2SPARQL_API_KEY
    if name == "anthropic":
        client: Any = AnthropicClient(api_key=api_key, model=model, temperature=0.0, timeout=120.0)
    else:
        client = OpenAICompatibleClient(
            provider=name, api_key=api_key, model=model, temperature=0.0, timeout=120.0
        )
    return client if getattr(client, "api_key", "") else None


def _mining_resolver(bundle: Any) -> tuple[Any, str]:
    """``(resolver, ontology_turtle)`` built the way the Workbench translates.

    The bundle's OWL (or, without one, the Turtle the Workbench's ontology
    panel renders from the mapping) merged with the bundle's physical mapping
    (``service/mapping.py::_resolver_from_request``), so an example verified
    here translates the same way when a user runs it.
    """
    from types import SimpleNamespace

    from .service.mapping import _resolver_from_request
    from .translate.owl import mapping_to_turtle

    ttl = bundle.owl_turtle or mapping_to_turtle(bundle)
    return _resolver_from_request(
        SimpleNamespace(ontology_ttl=ttl, mapping=None), analyzer_bundle=bundle
    ), ttl


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

    @app.command("mine-examples")
    def mine_examples(
        provider_name: str = typer.Option(
            ..., "--provider", help=f"LLM that drafts question + SPARQL: {', '.join(_MINING_PROVIDERS)}"
        ),
        model: str = typer.Option(None, "--model", help="Model id (default: the provider's default)"),
        graph: str = typer.Option(None, "--graph", "-g", help="Only saved queries of this named graph"),
        host: str = typer.Option(None, "--host", help="ArangoDB host (default: ARANGO_URL)"),
        port: int = typer.Option(None, "--port", help="ArangoDB port"),
        db: str = typer.Option(None, "--db", help="Database name (default: ARANGO_DB)"),
        user: str = typer.Option(None, "--user", help="Username"),
        password: str = typer.Option(None, "--password", help="Password (default: ARANGO_PASSWORD)"),
        mapping_file: Path = typer.Option(
            None, "--mapping-file", "-m", help="Mapping wire JSON (default: analyze the database live)"
        ),
        include_builtins: bool = typer.Option(
            False, "--include-builtins", help="Also mine the visualizer's defaults"
        ),
        max_attempts: int = typer.Option(3, "--max-attempts", min=1, max=6, help="Drafts per saved query"),
        dry_run: bool = typer.Option(False, "--dry-run", help="Verify but write nothing to the database"),
        report_file: Path = typer.Option(
            None, "--report", help="Write every outcome (incl. failed drafts) as JSON"
        ),
    ) -> None:
        """Mine the database's saved AQL queries for verified NL -> SPARQL examples.

        Reads the graph visualizer's saved queries and canvas actions and the
        query editor's saves, runs each read-only as the reference, has the LLM
        draft a question and SPARQL, and keeps a draft only if its translated
        AQL returns the same resources. Verified examples are written to the
        arango_sparql_examples collection in the same database, where the
        Workbench shows them (PRD §7.7).
        """
        from rich.console import Console
        from rich.table import Table

        from .nl2sparql.schema_summary import build_schema_summary, default_namespace
        from .query_mining.miner import mine_database
        from .query_mining.store import collection_name, save_outcomes
        from .schema.acquire import acquire_mapping_bundle
        from .schema.fingerprint import bundle_shape_fingerprint
        from .schema.graph_scope import scope_bundle_to_graph
        from .translate.mapping import MappingSource, mapping_from_wire_dict

        console = Console()
        if provider_name not in _MINING_PROVIDERS:
            console.print(f"[red]--provider must be one of {', '.join(_MINING_PROVIDERS)}[/red]")
            raise typer.Exit(1)
        client = _mining_client(provider_name, model)
        if client is None:
            console.print(
                f"[red]No API key for {provider_name}: set {_MINING_PROVIDERS[provider_name]} "
                "(or NL2SPARQL_API_KEY).[/red]"
            )
            raise typer.Exit(1)
        try:
            database = _connect(host, port, db, user, password)
            database.collections()  # authenticate now, not halfway through mining
        except Exception as exc:  # noqa: BLE001 - reported, then exit
            console.print(f"[red]Connection failed:[/red] {exc}")
            raise typer.Exit(1) from exc

        try:
            if mapping_file is not None:
                bundle = dataclasses.replace(
                    mapping_from_wire_dict(json.loads(mapping_file.read_text())),
                    source=MappingSource(kind="manual", notes=f"from {mapping_file}"),
                )
            else:
                bundle = acquire_mapping_bundle(database, include_owl=True)
            if graph:
                bundle = scope_bundle_to_graph(database, bundle, graph)
            resolver, ttl = _mining_resolver(bundle)
        except Exception as exc:  # noqa: BLE001 - reported, then exit
            console.print(f"[red]Failed to load the mapping:[/red] {type(exc).__name__}: {exc}")
            raise typer.Exit(1) from exc
        namespace = default_namespace(ttl)
        if not namespace:
            console.print("[red]The ontology declares no default (:) namespace for the SPARQL to use.[/red]")
            raise typer.Exit(1)

        try:
            outcomes, skipped = mine_database(
                database,
                bundle,
                client,
                resolver=resolver,
                schema_summary=build_schema_summary(bundle, namespace=namespace),
                namespace=namespace,
                graph=graph,
                include_builtins=include_builtins,
                max_attempts=max_attempts,
            )
        except Exception as exc:  # noqa: BLE001 - reported, then exit
            console.print(f"[red]Mining failed:[/red] {type(exc).__name__}: {exc}")
            raise typer.Exit(1) from exc

        table = Table(title=f"Saved queries mined from {database.name!r}" + (f" ({graph})" if graph else ""))
        table.add_column("Saved query")
        table.add_column("Result")
        table.add_column("Question / reason")
        for o in outcomes:
            if o.example is not None:
                table.add_row(
                    o.query.name, f"[green]verified[/green] ({o.example.verdict.kind})", o.example.question
                )
            else:
                table.add_row(o.query.name, "[yellow]rejected[/yellow]", o.reason)
        console.print(table)
        for where, why in skipped:
            console.print(f"[dim]skipped {where}: {why}[/dim]")

        if report_file is not None:
            report = [
                {
                    "source": f"{o.query.source}/{o.query.key}",
                    "name": o.query.name,
                    "verified": o.verified,
                    "question": o.example.question if o.example else None,
                    "sparql": o.example.sparql if o.example else None,
                    "reason": o.reason,
                    "failed_attempts": [a.__dict__ for a in o.failed_attempts],
                }
                for o in outcomes
            ]
            report_file.write_text(
                json.dumps({"outcomes": report, "skipped": skipped}, indent=2, default=str)
            )

        verified = sum(o.verified for o in outcomes)
        if dry_run:
            console.print(f"[green]{verified} of {len(outcomes)} verified[/green] (dry run: nothing written)")
            return
        model_id = f"{provider_name}:{getattr(client, 'model', '') or 'default'}"
        counts = save_outcomes(
            database, outcomes, mapping_hash=bundle_shape_fingerprint(bundle), model=model_id
        )
        console.print(
            f"[green]{verified} of {len(outcomes)} verified[/green]; {counts['saved']} saved, "
            f"{counts['removed']} stale removed, {counts['kept']} kept (provider failed) in {collection_name()}"
        )

else:  # pragma: no cover

    def app() -> None:  # type: ignore[no-redef]
        raise SystemExit("Install the [cli] extra to use the CLI: pip install 'arango-sparql-py[cli]'")
