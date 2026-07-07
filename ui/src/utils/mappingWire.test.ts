/**
 * Tests for the schema-mapping wire accessor (WP-UI-AQL).
 *
 * The backend emits camelCase (`physicalMapping`); the AQL editor used to
 * read snake_case (`physical_mapping`) and silently got no completion
 * context. These pin the camelCase read + the snake_case fallback so the
 * regression can't come back.
 */
import { describe, expect, it } from "vitest";

import { physicalMappingOf } from "./mappingWire";

const PM = {
  entities: { Person: { collectionName: "persons" } },
  relationships: { KNOWS: { edgeCollectionName: "knows" } },
};

describe("physicalMappingOf", () => {
  it("reads the canonical camelCase wire key", () => {
    const out = physicalMappingOf({ physicalMapping: PM });
    expect(out).not.toBeNull();
    expect(out!.entities).toEqual(PM.entities);
    expect(out!.relationships).toEqual(PM.relationships);
  });

  it("falls back to the snake_case spelling", () => {
    const out = physicalMappingOf({ physical_mapping: PM });
    expect(out).not.toBeNull();
    expect(out!.entities).toEqual(PM.entities);
  });

  it("prefers camelCase when both are present", () => {
    const out = physicalMappingOf({
      physicalMapping: PM,
      physical_mapping: { entities: { Other: {} }, relationships: {} },
    });
    expect(out!.entities).toEqual(PM.entities);
  });

  it("returns null for null/undefined/empty mappings", () => {
    expect(physicalMappingOf(null)).toBeNull();
    expect(physicalMappingOf(undefined)).toBeNull();
    expect(physicalMappingOf({})).toBeNull();
  });

  it("returns null when physicalMapping is not an object", () => {
    expect(physicalMappingOf({ physicalMapping: "nope" })).toBeNull();
  });

  it("defaults missing entities/relationships to empty objects", () => {
    const out = physicalMappingOf({ physicalMapping: { entities: PM.entities } });
    expect(out!.entities).toEqual(PM.entities);
    expect(out!.relationships).toEqual({});
  });
});
