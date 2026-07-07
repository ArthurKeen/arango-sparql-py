// Pure accessors for the schema-mapping wire dict (WP-UI-AQL). The
// backend emits the canonical **camelCase** shape from
// `arango_sparql.translate.mapping.mapping_to_wire_dict`
// (`{ conceptualSchema, physicalMapping, metadata, owlTurtle }`), so the
// AQL editor's schema-aware completion must read `physicalMapping` — not
// the snake_case `physical_mapping`. We accept both spellings so a
// Python-emitted bundle (snake_case) still lights up completion.

export interface PhysicalMapping {
  /** `physicalMapping.entities`: { Label: { collectionName, properties } } */
  entities: Record<string, unknown>;
  /** `physicalMapping.relationships`: { Type: { edgeCollectionName, ... } } */
  relationships: Record<string, unknown>;
}

/**
 * Extract the physical mapping (entities + relationships) from a schema
 * wire dict, tolerant of camelCase (wire) and snake_case (Python) keys.
 * Returns `null` when no usable physical mapping is present so callers
 * can clear completion context.
 */
export function physicalMappingOf(
  mapping: Record<string, unknown> | null | undefined,
): PhysicalMapping | null {
  if (!mapping || typeof mapping !== "object") return null;
  const pm = (mapping.physicalMapping ?? mapping.physical_mapping) as
    | Record<string, unknown>
    | undefined;
  if (!pm || typeof pm !== "object") return null;
  const entities =
    pm.entities && typeof pm.entities === "object"
      ? (pm.entities as Record<string, unknown>)
      : {};
  const relationships =
    pm.relationships && typeof pm.relationships === "object"
      ? (pm.relationships as Record<string, unknown>)
      : {};
  return { entities, relationships };
}
