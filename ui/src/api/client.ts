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
// NL → SPARQL pipeline (placeholder shapes — backend lands separately)
// ---------------------------------------------------------------------------

export interface NL2SparqlResponse {
  sparql: string;
  explanation: string;
  confidence: number;
  method: string;
  elapsed_ms?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
}

export interface NL2SparqlOptions {
  useLlm?: boolean;
  useFewshot?: boolean;
  sessionToken?: string;
  retryContext?: string;
}

export async function nl2Sparql(
  question: string,
  ontologyTtl: string | undefined,
  opts: NL2SparqlOptions | boolean = {},
): Promise<NL2SparqlResponse> {
  const options: NL2SparqlOptions =
    typeof opts === "boolean" ? { useLlm: opts } : opts;
  const body: Record<string, unknown> = { question };
  if (ontologyTtl) body.ontology_ttl = ontologyTtl;
  if (options.useLlm !== undefined) body.use_llm = options.useLlm;
  if (options.useFewshot !== undefined) body.use_fewshot = options.useFewshot;
  if (options.sessionToken) body.session_token = options.sessionToken;
  if (options.retryContext) body.retry_context = options.retryContext;
  return request("/nl2sparql", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export interface NL2AqlResponse {
  aql: string;
  bind_vars: Record<string, unknown>;
  explanation: string;
  confidence: number;
  method: string;
  elapsed_ms?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
}

export async function nl2Aql(
  question: string,
  ontologyTtl?: string,
): Promise<NL2AqlResponse> {
  const body: Record<string, unknown> = { question };
  if (ontologyTtl) body.ontology_ttl = ontologyTtl;
  return request("/nl2aql", {
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
