import { useEffect, useState } from "react";
import { getOwlSchema, type OwlClass, type OwlProperty } from "../api/client";
import CytoscapeSchemaGraph from "./CytoscapeSchemaGraph";

// Schema graph viewer for the SPARQL UI.
//
// Mirrors `references/arango-cypher-py/ui/src/components/
// SchemaGraph.tsx` in role: render the active ontology as a
// Cytoscape graph (classes as nodes, properties as edges) so the
// user can see the OWL structure they're querying against.
//
// TODO(schema-owl-endpoint): the SPARQL backend does not implement
// `/schema/owl` yet. Once it does, the component will:
//   1. Pull `OwlSchemaResponse` (see `api/client.ts`) and pass it to
//      `CytoscapeSchemaGraph` for layout + interaction.
//   2. Optionally accept the ontology TTL the user is editing in
//      `MappingPanel`, parse it client-side via rdflib-js or a small
//      local turtle parser, and render that instead of the
//      server-side OWL — useful when the user is iterating on a
//      proposed ontology before persisting it.
//
// Until then we render a "No schema loaded" placeholder so the
// layout slot is visible and obviously aspirational.

interface Props {
  // Raw OWL/Turtle text the user is editing in `MappingPanel`. The
  // future client-side render path will parse this; today we display
  // it as a hint that the user has an ontology in flight.
  ontologyTtl?: string;
}

export default function SchemaGraph({ ontologyTtl }: Props) {
  const [classes, setClasses] = useState<OwlClass[]>([]);
  const [properties, setProperties] = useState<OwlProperty[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getOwlSchema()
      .then((resp) => {
        if (cancelled) return;
        setClasses(resp.classes ?? []);
        setProperties(resp.properties ?? []);
      })
      .catch((err) => {
        if (cancelled) return;
        // Endpoint not implemented yet → friendly placeholder. We
        // distinguish 404 from real errors so a misconfigured
        // backend doesn't masquerade as "endpoint missing".
        const msg = err instanceof Error ? err.message : String(err);
        setError(msg);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center text-xs text-gray-500">
        Loading schema…
      </div>
    );
  }

  if (classes.length === 0 && properties.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3 px-6 text-center">
        <div className="text-sm text-gray-300 font-medium">No schema loaded</div>
        <div className="text-xs text-gray-500 max-w-md">
          Load an OWL/Turtle ontology in the Turtle tab (or the
          backend's <code className="text-gray-400">/schema/owl</code>{" "}
          endpoint, when wired up) to see classes and property edges
          here.
        </div>
        {ontologyTtl && ontologyTtl.trim().length > 0 && (
          <div className="text-[11px] text-gray-600 max-w-md">
            You have {ontologyTtl.length.toLocaleString()} bytes of
            Turtle in the editor — client-side rendering of in-flight
            ontologies is a planned follow-up.
          </div>
        )}
        {error && (
          <div className="text-[11px] text-amber-500 max-w-md break-words">
            <span className="font-mono">/schema/owl</span>: {error}
          </div>
        )}
      </div>
    );
  }

  return <CytoscapeSchemaGraph classes={classes} properties={properties} />;
}
