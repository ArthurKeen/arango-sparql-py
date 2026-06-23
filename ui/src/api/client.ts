// Typed wrappers around the `arango_sparql` service HTTP surface.
//
// Mirrors `references/arango-cypher-py/ui/src/api/client.ts` but
// adapted to the SPARQL request/response shapes defined in
// `arango_sparql/service/models.py`:
//
//   * /translate → { sparql, ontology_ttl, params }
//   * /execute   → { sparql, ontology_ttl, params, database }
//
// Endpoints that the SPARQL backend has not yet implemented (e.g.
// /connect, /validate, /schema/owl, /nl2sparql, /explain) are still
// declared here so the surface mirrors the Cypher UI 1:1 — failures
// surface as standard HTTP errors at runtime instead of 404s on the
// dev-server proxy.

export interface ConnectRequest {
  url: string;
  database: string;
  username: string;
  password: string;
}

export interface ConnectResponse {
  token: string;
  databases: string[];
}

export interface ConnectDefaults {
  url: string;
  database: string;
  username: string;
  password?: string;
}

export interface TranslateRequest {
  sparql: string;
  ontology_ttl?: string;
  params?: Record<string, unknown>;
}

export interface TranslateResponse {
  aql: string;
  bind_vars: Record<string, unknown>;
  warnings: Array<{ message: string }>;
  elapsed_ms?: number;
}

// SPARQL `/execute` returns an SPARQL solution-mappings list (one
// dict per row keyed by SPARQL projection variable). We surface them
// under `bindings:` to match the backend, but also expose a
// `results:`-shaped alias so the existing ResultsPanel (ported from
// the Cypher UI) can render them with no code changes — the table
// view treats each row as a record and the graph view picks up
// `_id`/`_from`/`_to` IRIs the same way it does in Cypher.
export interface SparqlExecuteRequest {
  sparql: string;
  ontology_ttl?: string;
  params?: Record<string, unknown>;
  database?: string;
}

export interface SparqlExecuteResponse {
  bindings: Array<Record<string, unknown>>;
  warnings: Array<{ message: string }>;
  aql?: string | null;
  bind_vars?: Record<string, unknown> | null;
  elapsed_ms?: number;
}

export interface ValidateResponse {
  ok: boolean;
  errors: Array<{ message: string; code?: string }>;
}

export interface ExplainResponse {
  aql: string;
  bind_vars: Record<string, unknown>;
  plan: unknown;
  translate_ms?: number;
}

export interface ProfileResponse {
  aql: string;
  bind_vars: Record<string, unknown>;
  results: unknown[];
  statistics: Record<string, unknown>;
  profile: unknown;
  translate_ms?: number;
}

function authHeaders(token: string): Record<string, string> {
  return { "X-Arango-Session": token };
}

// SPA mount-point detection. Same logic as the Cypher UI: when the SPA
// is mounted under `/frontend/` (production AMP deploy) or `/ui/`
// (legacy / local-dev) we strip the prefix to recover the actual API
// base. For Vite dev-server we fall through to the empty string and
// rely on `vite.config.ts`'s proxy table.
function apiBase(): string {
  for (const prefix of ["/frontend", "/ui"]) {
    const idx = window.location.pathname.indexOf(prefix);
    if (idx >= 0) return window.location.pathname.slice(0, idx);
  }
  return "";
}

export const AUTH_EXPIRED_MESSAGE =
  "Your session has expired. Please re-authenticate to the database.";

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const { headers: extraHeaders, ...rest } = options;
  const res = await fetch(apiBase() + path, {
    ...rest,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(extraHeaders as Record<string, string>),
    },
  });
  if (!res.ok) {
    if (res.status === 401) {
      await res.text().catch(() => "");
      throw new ApiError(401, AUTH_EXPIRED_MESSAGE);
    }
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, body.detail ?? body);
  }
  return res.json();
}

function formatDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const obj = detail as Record<string, unknown>;
    if (typeof obj.error === "string") return obj.error;
    if (typeof obj.detail === "string") return obj.detail;
    if (typeof obj.message === "string") return obj.message;
  }
  return JSON.stringify(detail);
}

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(formatDetail(detail));
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export function isAuthError(err: unknown): boolean {
  return err instanceof ApiError && err.status === 401;
}

export interface HealthResponse {
  status: string;
  version: string;
}

export async function getHealth(): Promise<HealthResponse> {
  return request("/health");
}

export async function getConnectDefaults(): Promise<ConnectDefaults> {
  return request("/connect/defaults");
}

export async function connect(req: ConnectRequest): Promise<ConnectResponse> {
  return request("/connect", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export async function disconnect(token: string): Promise<void> {
  await request("/disconnect", {
    method: "POST",
    headers: authHeaders(token),
  });
}

// ---------------------------------------------------------------------------
// ArangoDB named-graph scoping (GET /graphs, POST /session/graph). Lets the
// UI restrict schema acquisition to one graph's collections so a shared DB's
// unrelated collections don't pollute translation / suggestions.
// ---------------------------------------------------------------------------

export interface GraphInfo {
  name: string;
  edgeCollections: string[];
  vertexCollections: string[];
  orphanCollections: string[];
  collectionCount: number;
}

export async function listGraphs(token: string): Promise<{ graphs: GraphInfo[] }> {
  return request("/graphs", { headers: authHeaders(token) });
}

export async function bindGraph(
  graphName: string | null,
  token: string,
): Promise<{ graph_name: string | null; bound: boolean }> {
  return request("/session/graph", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ graphName }),
  });
}

export async function translateSparql(
  req: TranslateRequest,
): Promise<TranslateResponse> {
  return request("/translate", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export async function executeSparql(
  req: SparqlExecuteRequest,
  token?: string,
): Promise<SparqlExecuteResponse> {
  return request("/execute", {
    method: "POST",
    body: JSON.stringify(req),
    headers: token ? authHeaders(token) : undefined,
  });
}

export async function validateSparql(
  req: TranslateRequest,
): Promise<ValidateResponse> {
  return request("/validate", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

// Run an AQL string directly, bypassing translation. Mirrors the
// Cypher UI's `executeAql` — this is what the AQL editor uses when
// the user hand-edits the translated AQL and re-runs it.
export async function executeAql(
  aql: string,
  bindVars: Record<string, unknown>,
  token: string,
): Promise<{ results: unknown[]; warnings: Array<{ message: string }>; exec_ms?: number }> {
  return request("/execute-aql", {
    method: "POST",
    body: JSON.stringify({ aql, bind_vars: bindVars }),
    headers: authHeaders(token),
  });
}

export async function explainSparql(
  req: TranslateRequest,
  token: string,
): Promise<ExplainResponse> {
  return request("/explain", {
    method: "POST",
    body: JSON.stringify(req),
    headers: authHeaders(token),
  });
}

export async function profileSparql(
  req: TranslateRequest,
  token: string,
): Promise<ProfileResponse> {
  return request("/aql-profile", {
    method: "POST",
    body: JSON.stringify(req),
    headers: authHeaders(token),
  });
}

// ---------------------------------------------------------------------------
// OWL schema (from `arango-schema-mapper`)
// ---------------------------------------------------------------------------

// Returned by `/schema/owl` (not yet implemented on the SPARQL
// backend — see `.cursor/rules/400-frontend-ui.mdc`). The shape
// matches the Turtle ontology that `arango-schema-mapper` produces:
// a list of OWL classes + properties keyed by IRI. SchemaGraph.tsx
// renders this as a Cytoscape graph; until the endpoint exists we
// fall back to an empty placeholder.
export interface OwlClass {
  iri: string;
  localName: string;
  superClasses: string[];
  comment?: string;
}

export interface OwlProperty {
  iri: string;
  localName: string;
  domain: string[];
  range: string[];
  kind: "object" | "datatype" | "annotation";
  comment?: string;
}

export interface OwlSchemaResponse {
  classes: OwlClass[];
  properties: OwlProperty[];
  // Optional source TTL — round-trip handy for the OntologyPanel.
  turtle?: string;
}

export async function getOwlSchema(
  token?: string,
): Promise<OwlSchemaResponse> {
  return request("/schema/owl", {
    headers: token ? authHeaders(token) : undefined,
  });
}

// ---------------------------------------------------------------------------
// Schema acquisition (PRD §6.4 — backed by `arango_sparql.schema.acquire`)
// ---------------------------------------------------------------------------
//
// These wrappers are a 1:1 mapping of the FastAPI routes added in
// service slice 6 (`arango_sparql/service/routes/schema.py`). The
// shapes intentionally mirror the Pydantic response models; we
// keep them as `Record<string, unknown>` rather than full typed
// interfaces because the analyzer's wire-dict shape evolves
// version-to-version and a permissive type lets the UI render any
// future fields without a frontend rebuild.

export type SchemaStrategy = "auto" | "analyzer" | "heuristic";

export interface SchemaIntrospectQuery {
  database?: string;
  strategy?: SchemaStrategy;
  force?: boolean;
  /** Include the OWL/Turtle ontology in the response. Default true. */
  include_owl?: boolean;
  /** Include statistics. Default true. */
  include_statistics?: boolean;
}

export interface SchemaIntrospectResponse {
  mapping: Record<string, unknown>;
  summary: Record<string, unknown>;
  warnings: Array<{ code: string; message: string; install_hint?: string }>;
  source: Record<string, unknown> | null;
  cache_hit: boolean;
  elapsed_ms: number;
}

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null) continue;
    parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  }
  return parts.length ? `?${parts.join("&")}` : "";
}

export async function schemaIntrospect(
  token: string,
  query: SchemaIntrospectQuery = {},
): Promise<SchemaIntrospectResponse> {
  return request(`/schema/introspect${qs(query as Record<string, string | number | boolean | undefined>)}`, {
    headers: authHeaders(token),
  });
}

export type SchemaDriftStatus =
  | "no_cache"
  | "unchanged"
  | "stats_only"
  | "shape_changed";

export interface SchemaStatusResponse {
  status: SchemaDriftStatus;
  cached_at?: string | null;
  cached_fingerprints?: { shape?: string; statistics?: string };
  live_fingerprints?: { shape?: string; statistics?: string };
  warnings: Array<{ code: string; message: string; install_hint?: string }>;
}

export async function schemaStatus(
  token: string,
  database?: string,
): Promise<SchemaStatusResponse> {
  return request(`/schema/status${qs({ database })}`, {
    headers: authHeaders(token),
  });
}

export interface SchemaInvalidateResponse {
  invalidated: boolean;
  database?: string;
}

export async function schemaInvalidateCache(
  token: string,
  database?: string,
): Promise<SchemaInvalidateResponse> {
  return request("/schema/invalidate-cache", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ database }),
  });
}

export async function schemaForceReacquire(
  token: string,
  query: SchemaIntrospectQuery = {},
): Promise<SchemaIntrospectResponse> {
  return request("/schema/force-reacquire", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(query),
  });
}

// ---------------------------------------------------------------------------
// OWL Import / Export (PRD §6.4 rows 8 & 9)
// ---------------------------------------------------------------------------
//
// Two shapes per route to match the backend's content-negotiated
// behaviour:
//
// * Import — JSON envelope `{turtle, source_notes?}` is the default
//   path because the UI's File API hands us a string. Raw text/turtle
//   is supported for symmetry but not used by the panel today.
// * Export — JSON envelope returns `{turtle, mime_type, triple_count}`;
//   the `Accept: text/turtle` path is used by the "Download" button
//   so the browser saves the raw bytes verbatim.

export interface OwlImportResponse {
  accepted: boolean;
  mapping: Record<string, unknown>;
  triple_count: number;
  warnings: Array<{ code: string; message: string }>;
  source: Record<string, unknown> | null;
  elapsed_ms: number;
}

export async function importOwl(
  turtle: string,
  token: string,
  sourceNotes?: string,
): Promise<OwlImportResponse> {
  return request("/mapping/import-owl", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({
      turtle,
      ...(sourceNotes ? { source_notes: sourceNotes } : {}),
    }),
  });
}

export interface OwlExportJsonResponse {
  turtle: string;
  mime_type: string;
  triple_count: number;
  elapsed_ms: number;
}

export async function exportOwlJson(
  mapping: Record<string, unknown> | null,
  ontologyTtl: string | undefined,
  token: string,
): Promise<OwlExportJsonResponse> {
  return request("/mapping/export-owl", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({
      ...(mapping ? { mapping } : {}),
      ...(ontologyTtl ? { ontology_ttl: ontologyTtl } : {}),
    }),
  });
}

/**
 * Fetch the export as raw `text/turtle` bytes, suitable for handing
 * to a Blob download. Bypasses `request()` because the response is
 * not JSON.
 */
export async function exportOwlAsTurtle(
  mapping: Record<string, unknown> | null,
  ontologyTtl: string | undefined,
  token: string,
): Promise<{ turtle: string; tripleCount: number }> {
  const res = await fetch(apiBase() + "/mapping/export-owl", {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/turtle",
      ...authHeaders(token),
    },
    body: JSON.stringify({
      ...(mapping ? { mapping } : {}),
      ...(ontologyTtl ? { ontology_ttl: ontologyTtl } : {}),
    }),
  });
  if (!res.ok) {
    if (res.status === 401) throw new ApiError(401, AUTH_EXPIRED_MESSAGE);
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, body.detail ?? body);
  }
  const turtle = await res.text();
  const tripleCount = Number(res.headers.get("x-triple-count") ?? "0") || 0;
  return { turtle, tripleCount };
}

// ---------------------------------------------------------------------------
// Sample queries
// ---------------------------------------------------------------------------

export interface SampleQuery {
  id: string;
  description: string;
  sparql: string;
  dataset: string;
  expected_min_count?: number;
}

export async function getSampleQueries(
  dataset?: string,
): Promise<{ queries: SampleQuery[] }> {
  const qs = dataset ? `?dataset=${encodeURIComponent(dataset)}` : "";
  return request(`/sample-queries${qs}`);
}

// ---------------------------------------------------------------------------
// NL → SPARQL pipeline (POST /nl-translate). The backend runs the LLM,
// parses the SPARQL, and feeds it through the deterministic transpiler,
// so a single call returns BOTH the SPARQL and the ready-to-run AQL.
// Mirrors arango-cypher-py's nl2Cypher but adapted to the SPARQL
// pipeline's request/response shapes (see
// arango_sparql.service.models.NlTranslate{Request,Response}).
// ---------------------------------------------------------------------------

export interface NlTranslateResponse {
  nl: string;
  sparql: string;
  aql: string;
  bind_vars: Record<string, unknown>;
  warnings: Array<Record<string, unknown>>;
  llm_calls: number;
  cost_usd: number;
  latency_ms: number;
  repaired: boolean;
}

export interface NlTranslateOptions {
  /** Inline OWL/Turtle schema the LLM should ground its query in. */
  ontologyTtl?: string;
  /** Max transpiler-driven repair iterations (backend clamps 0..5). */
  maxRepairs?: number;
}

export async function nl2Sparql(
  question: string,
  opts: NlTranslateOptions = {},
): Promise<NlTranslateResponse> {
  const body: Record<string, unknown> = { nl: question };
  if (opts.ontologyTtl) body.ontology_ttl = opts.ontologyTtl;
  if (opts.maxRepairs !== undefined) body.max_repairs = opts.maxRepairs;
  return request("/nl-translate", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export interface NlSamplesResponse {
  queries: string[];
  elapsed_ms?: number;
}

export async function suggestNlQueries(
  ontologyTtl: string | undefined,
  count: number = 8,
  useLlm: boolean = true,
): Promise<NlSamplesResponse> {
  const body: Record<string, unknown> = { count, use_llm: useLlm };
  if (ontologyTtl) body.ontology_ttl = ontologyTtl;
  return request("/nl-samples", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
