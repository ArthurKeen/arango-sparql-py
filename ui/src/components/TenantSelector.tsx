// Multitenancy selector. Mirrored from `references/arango-cypher-py/
// ui/src/components/TenantSelector.tsx` for layout symmetry. The
// SPARQL service has not yet wired multitenancy guards (they're
// part of the next milestone — see `.cursor/rules/100-backend-
// python.mdc`), so App.tsx hides this component today. We keep the
// file so the component map matches the Cypher UI 1:1 and the
// future wire-up only has to swap from "hidden" to "shown".

export interface TenantContext {
  property: string;
  value: string;
  display?: string;
}

export interface TenantRecord {
  id: string;
  key: string;
  name: string | null;
  subdomain: string | null;
  hex_id: string | null;
}

interface Props {
  tenants: TenantRecord[];
  loading: boolean;
  selection: TenantContext | null;
  onSelect: (ctx: TenantContext | null) => void;
  detected: boolean;
  resolvedCollection?: string | null;
  source?: "client" | "heuristic" | null;
  error?: string | null;
}

export default function TenantSelector({
  tenants,
  loading,
  selection,
  onSelect,
  detected,
  resolvedCollection,
  source,
  error,
}: Props) {
  if (!detected && !loading && tenants.length === 0) return null;

  const tooltip = (() => {
    const parts: string[] = [];
    if (resolvedCollection) {
      parts.push(`collection: ${resolvedCollection}`);
    }
    if (source) parts.push(`source: ${source}`);
    if (error) parts.push(`error: ${error}`);
    return parts.join(" · ") || "Select a tenant to scope queries";
  })();

  return (
    <div
      className="flex items-center gap-1.5 px-2 py-1 rounded bg-amber-900/20 border border-amber-700/40 text-xs"
      title={tooltip}
    >
      <span className="text-amber-400">Tenant:</span>
      <select
        value={selection?.value ?? ""}
        onChange={(e) => {
          const v = e.target.value;
          if (!v) {
            onSelect(null);
            return;
          }
          const tenant = tenants.find((t) => t.key === v);
          onSelect({
            property: "_key",
            value: v,
            display: tenant?.name ?? tenant?.subdomain ?? v,
          });
        }}
        className="bg-gray-800 border border-amber-700/40 text-amber-200 text-xs rounded px-1.5 py-0.5 focus:border-amber-500 focus:outline-none"
        disabled={loading || tenants.length === 0}
      >
        <option value="">All tenants</option>
        {tenants.map((t) => (
          <option key={t.key} value={t.key}>
            {t.name || t.subdomain || t.key}
          </option>
        ))}
      </select>
    </div>
  );
}
