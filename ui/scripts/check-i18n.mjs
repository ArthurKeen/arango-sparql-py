#!/usr/bin/env node
// Advisory i18n scan (WP-UI-A11Y, PRD §10.10 i18n row).
//
// Reports hardcoded, user-visible JSX text that isn't yet routed through
// `t()` from `src/i18n`. This is intentionally ADVISORY (always exits 0):
// full string migration is incremental, and failing the build today would
// block every PR. Once the catalogue covers the app, flip EXIT_ON_FIND to
// true to turn this into the CI gate the PRD specifies.
//
// Heuristic, not a parser: it flags JSX text nodes (`>Some words<`) that
// contain two+ letters and aren't an expression (`{...}`) or an entity
// (`&times;`). Expect some false positives — treat the output as a
// worklist, not gospel.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

const EXIT_ON_FIND = false;
const here = dirname(fileURLToPath(import.meta.url));
const SRC = join(here, "..", "src");

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else if (p.endsWith(".tsx") && !p.endsWith(".test.tsx")) out.push(p);
  }
  return out;
}

// >text< that has letters, isn't only an expression/entity/punctuation.
const JSX_TEXT = />\s*([A-Za-z][^<>{}]*[A-Za-z][^<>{}]*)\s*</g;

let total = 0;
const perFile = [];

for (const file of walk(SRC)) {
  const src = readFileSync(file, "utf8");
  const hits = new Set();
  let m;
  while ((m = JSX_TEXT.exec(src)) !== null) {
    const text = m[1].trim();
    // Skip obvious non-copy: single words that look like identifiers are
    // still flagged (they're usually labels); skip pure symbols/URLs.
    if (!/[A-Za-z]{2,}/.test(text)) continue;
    if (/^https?:\/\//.test(text)) continue;
    hits.add(text);
  }
  if (hits.size > 0) {
    perFile.push([relative(SRC, file), [...hits]]);
    total += hits.size;
  }
}

perFile.sort((a, b) => b[1].length - a[1].length);
console.log(`i18n advisory: ${total} hardcoded JSX string(s) across ${perFile.length} file(s)\n`);
for (const [file, hits] of perFile) {
  console.log(`  ${file} (${hits.length})`);
  for (const h of hits.slice(0, 8)) console.log(`    · ${h}`);
  if (hits.length > 8) console.log(`    … ${hits.length - 8} more`);
}
console.log(
  `\nRoute new user-visible strings through t("key") + src/i18n/en.ts. ` +
    `This check is advisory (EXIT_ON_FIND=${EXIT_ON_FIND}).`,
);

process.exit(EXIT_ON_FIND && total > 0 ? 1 : 0);
