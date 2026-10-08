import { describe, expect, it } from "vitest";
import { apiBaseFor } from "./client";

// Ported from arango-cypher-py's ui/src/api/client.test.ts. Regression guard
// for the deployed Workbench: under the Container Manager mount the old
// prefix-strip returned "" and sent /connect/platform to the cluster root,
// so the UI never auto-connected.
describe("apiBaseFor", () => {
  it("uses the platform mount when the SPA is served at the service root", () => {
    expect(apiBaseFor("/_service/uds/_db/IAM/arango-sparql-py/")).toBe(
      "/_service/uds/_db/IAM/arango-sparql-py",
    );
  });

  it("treats a mount opened without its trailing slash as a directory", () => {
    expect(apiBaseFor("/_service/uds/_db/IAM/arango-sparql-py")).toBe(
      "/_service/uds/_db/IAM/arango-sparql-py",
    );
  });

  it("drops an index.html page from the path", () => {
    expect(apiBaseFor("/_service/uds/_db/IAM/arango-sparql-py/index.html")).toBe(
      "/_service/uds/_db/IAM/arango-sparql-py",
    );
  });

  it("strips the /frontend and /ui sub-mounts to reach the API", () => {
    expect(apiBaseFor("/_service/uds/_db/d/app/frontend/")).toBe("/_service/uds/_db/d/app");
    expect(apiBaseFor("/_service/uds/_global/app/ui/index.html")).toBe("/_service/uds/_global/app");
    expect(apiBaseFor("/frontend/")).toBe("");
    expect(apiBaseFor("/ui")).toBe("");
  });

  it("is empty for local dev at the origin root", () => {
    expect(apiBaseFor("/")).toBe("");
    expect(apiBaseFor("")).toBe("");
  });

  it("compares whole segments, not substrings", () => {
    expect(apiBaseFor("/_service/uds/_db/d/ui-demo/")).toBe("/_service/uds/_db/d/ui-demo");
    expect(apiBaseFor("/_service/uds/_db/frontend-db/app/")).toBe("/_service/uds/_db/frontend-db/app");
  });
});
