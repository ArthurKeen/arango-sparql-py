# Security policy

## Supported versions

This project is pre-1.0. Security fixes are made on the `main` branch only;
there are no maintenance branches yet. Once a `1.x` release ships, this
section will be updated with the supported release matrix.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security-impacting bugs.

Instead, report privately via GitHub's [Private vulnerability reporting
form](https://github.com/ArthurKeen/arango-sparql-py/security/advisories/new),
or email <aakeen@yahoo.com> with:

- a description of the issue and its impact,
- a minimal reproducer (SPARQL query, ontology, request payload),
- the version / commit SHA you reproduced against.

You should receive an acknowledgement within 7 days. A fix timeline depends
on severity; a typical critical issue is patched and tagged within 30 days.

## Scope

In scope:

- SPARQL → AQL translation correctness bugs that allow a query author to
  read or modify data outside their intended scope,
- AQL injection via bind-variable handling, schema-resolver IRI mapping, or
  `nl2sparql` LLM-output paths,
- HTTP service authentication / session-handling bypasses
  (`arango_sparql.service`),
- CORS / public-mode misconfiguration that exposes the service beyond its
  declared origin set.

Out of scope:

- Findings against the legacy [`arango-sparql`](https://github.com/ArthurKeen/arango-sparql)
  Foxx service — please file those upstream,
- Vulnerabilities in third-party dependencies (`rdflib`, `pyoxigraph`,
  `python-arango`, `fastapi`, …) — please report to the upstream project,
- DoS via expensive-but-valid SPARQL queries: this is a known class of
  issues and is mitigated via service-side limits documented in
  `arango_sparql/service/models.py`. New mitigation ideas are welcome as
  regular issues / PRs.
