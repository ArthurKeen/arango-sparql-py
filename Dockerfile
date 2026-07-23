# Container image for the arango-sparql-py service.
#
# Neither this repo's docker-compose.yml (test ArangoDB only) nor the
# sister project ships a service image; this Dockerfile closes that
# deployment gap. The contextual-data-fabric P1 demo embeds the
# transpiler as a library inside the M5 engine, so this image serves
# the STANDALONE deployment story (BYOC /sparql microservice + UI).
#
# Build:  docker build -t arango-sparql-py .
# Run:    docker run -p 8000:8000 -e ARANGO_URL=http://arangodb:8529 arango-sparql-py
#
# Liveness:  GET /health        (static — process is up)
# Readiness: GET /health/ready  (pings the configured ArangoDB)

# ---- build stage: wheel + deps into an isolated venv -----------------
FROM python:3.12-slim AS build

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY arango_sparql ./arango_sparql

RUN python -m venv /venv \
    && /venv/bin/pip install --no-cache-dir --upgrade pip \
    && /venv/bin/pip install --no-cache-dir ".[service,nl,cli]"

# ---- runtime stage ---------------------------------------------------
FROM python:3.12-slim

# Non-root: the service needs no filesystem writes beyond /tmp.
RUN useradd --system --no-create-home appuser
COPY --from=build /venv /venv
ENV PATH="/venv/bin:$PATH"

USER appuser
EXPOSE 8000

# Container-level healthcheck hits the READINESS probe so an
# orchestrator without k8s-style probe config still restarts a
# wedged container (liveness) and keeps it out of rotation until
# ArangoDB is reachable (readiness).
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=4 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2).status == 200 else 1)" || exit 1

CMD ["arango-sparql-py", "serve", "--host", "0.0.0.0", "--port", "8000"]
