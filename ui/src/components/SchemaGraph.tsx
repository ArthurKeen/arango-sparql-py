import { useEffect, useMemo, useState } from "react";
import { getOwlSchema, type OwlClass, type OwlProperty } from "../api/client";
import { owlSchemaFromTurtle } from "../api/owlFromTurtle";
import CytoscapeSchemaGraph from "./CytoscapeSchemaGraph";

// Schema graph viewer for the SPARQL UI.
//
// Mirrors `references/arango-cypher-py/ui/src/components/
// SchemaGraph.tsx` in role: render the active ontology as a
// Cytoscape graph (classes as nodes, properties as edges) so the
// user can see the OWL structure they're querying against.
//
// Two data sources, in priority order:
//   1. The OWL/Turtle the user is editing in the Turtle tab — parsed
//      client-side via `owlSchemaFromTurtle` (n3). This renders the
//      in-flight ontology immediately, with no round-trip, and works
//      even against an empty database. Preferred whenever the editor
//      holds a non-trivial ontology.
//   2. The backend `/schema/owl` endpoint — the database-derived OWL
//      schema. Used when the editor is empty so a connected user still
//      sees the schema the analyzer inferred from their collections.

interface Props {
  // Raw OWL/Turtle text the user is editing in `MappingPanel`. Parsed
  // client-side and rendered in preference to the backend schema.
  ontologyTtl?: string;
}

export default function SchemaGraph({ ontologyTtl }: Props) {
  const [serverClasses, setServerClasses] = useState<OwlClass[]>([]);
  const [serverProperties, setServerProperties] = useState<OwlProperty[]>([]);
  const [serverError, setServerError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Parse the in-editor ontology client-side. A parse failure surfaces a
  // message rather than crashing the panel; an empty editor yields empty
  // lists so we fall through to the backend schema.
  const { editorClasses, editorProperties, editorError } = useMemo(() => {
    try {
      const view = owlSchemaFromTurtle(ontologyTtl);
      return {
        editorClasses: view.classes,
        editorProperties: view.properties,
        editorError: null as string | null,
      };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return {
        editorClasses: [] as OwlClass[],
        editorProperties: [] as OwlProperty[],
        editorError: msg,
      };
    }
  }, [ontologyTtl]);

  const hasEditorSchema =
    editorClasses.length > 0 || editorProperties.length > 0;

  // Only consult the backend when the editor has nothing to render — the
  // editor ontology is the more immediate, user-controlled source.
  useEffect(() => {
    if (hasEditorSchema) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setServerError(null);
    getOwlSchema()
      .then((resp) => {
        if (cancelled) return;
        setServerClasses(resp.classes ?? []);
        setServerProperties(resp.properties ?? []);
      })
      .catch((err) => {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        setServerError(msg);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [hasEditorSchema]);

  if (hasEditorSchema) {
    return (
      <CytoscapeSchemaGraph
        classes={editorClasses}
        properties={editorProperties}
      />
    );
  }

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center text-xs text-gray-500">
        Loading schema…
      </div>
    );
  }

  if (serverClasses.length > 0 || serverProperties.length > 0) {
    return (
      <CytoscapeSchemaGraph
        classes={serverClasses}
        properties={serverProperties}
      />
    );
  }

  return (
    <div className="h-full flex flex-col items-center justify-center gap-3 px-6 text-center">
      <div className="text-sm text-gray-300 font-medium">No schema loaded</div>
      <div className="text-xs text-gray-500 max-w-md">
        Paste an OWL/Turtle ontology in the Turtle tab to see its classes
        and property edges here, or connect to a database whose schema the
        analyzer can infer.
      </div>
      {editorError && (
        <div className="text-[11px] text-amber-500 max-w-md break-words">
          Turtle parse error: {editorError}
        </div>
      )}
      {serverError && !editorError && (
        <div className="text-[11px] text-gray-600 max-w-md break-words">
          <span className="font-mono">/schema/owl</span>: {serverError}
        </div>
      )}
    </div>
  );
}
