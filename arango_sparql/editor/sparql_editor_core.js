// SPARQL editor core: the rules the two syntax-directed SPARQL editors share
// (arango-solutions-org/arango-sparql-py#68).
//
//   - the workbench's CodeMirror editor (ui/src/lang/sparqlComplete.ts)
//   - the contextual-data-fabric demo's textarea editor (deploy/demo/editor.js
//     in that repo), which embeds this file from the installed package via
//     arango_sparql.editor.editor_core_js()
//
// Everything here is a pure function of (query text, caret offset, editor
// vocabulary v1): no DOM, no editor framework, no dependencies. Each editor
// keeps its own shell (painting, menu, bracket pairing, keys) and asks this
// file what to suggest at the caret, what each token is, and how to
// pretty-print. A rule fixed here is fixed in both editors.
//
// Editor vocabulary v1 (arango_sparql.editor.EditorVocabV1 is the definition):
//   {"base": "<concept namespace IRI>",
//    "classes": {"<Class>": {"props": ["<prop>", ...], "source": "...", "kind": "..."}}}
//
// One file, three loaders, so it must stay a plain script with no
// import/export statements:
//   - a classic <script> (CDF inlines it)        -> globalThis.SparqlEditorCore
//   - a side-effect import (the workbench build) -> globalThis.SparqlEditorCore
//   - require() (node tests)                     -> module.exports as well
//
// Provenance: suggestion rules, tokenizer and formatSparql moved from CDF's
// editor.js (its 2026-08-10 version); the prefix / variable / hover helpers
// moved from the workbench's sparqlComplete.ts (WP-UI-EDITOR). Deliberate
// changes on the way in are listed in docs/architecture/implementation_plan.md
// (WP-EDITOR-CORE).
(function (root) {
  'use strict';

  const VOCAB_CONTRACT = 'editor-vocabulary/v1';

  const SPARQL_KEYWORDS = Object.freeze([
    'SELECT', 'CONSTRUCT', 'ASK', 'DESCRIBE', 'WHERE', 'FROM', 'PREFIX',
    'BASE', 'OPTIONAL', 'FILTER', 'UNION', 'MINUS', 'GRAPH', 'SERVICE',
    'BIND', 'VALUES', 'GROUP BY', 'ORDER BY', 'HAVING', 'LIMIT', 'OFFSET',
    'DISTINCT', 'REDUCED', 'AS', 'ASC', 'DESC', 'NOT EXISTS', 'EXISTS',
    'IN', 'true', 'false', 'a',
  ]);

  const SPARQL_FUNCTIONS = Object.freeze([
    'STR', 'LANG', 'LANGMATCHES', 'DATATYPE', 'BOUND', 'IRI', 'URI',
    'BNODE', 'RAND', 'ABS', 'CEIL', 'FLOOR', 'ROUND', 'CONCAT', 'STRLEN',
    'UCASE', 'LCASE', 'ENCODE_FOR_URI', 'CONTAINS', 'STRSTARTS', 'STRENDS',
    'STRBEFORE', 'STRAFTER', 'YEAR', 'MONTH', 'DAY', 'HOURS', 'MINUTES',
    'SECONDS', 'TIMEZONE', 'TZ', 'NOW', 'UUID', 'STRUUID', 'MD5', 'SHA1',
    'SHA256', 'COALESCE', 'IF', 'STRLANG', 'STRDT', 'sameTerm', 'isIRI',
    'isURI', 'isBLANK', 'isLITERAL', 'isNUMERIC', 'REGEX', 'SUBSTR',
    'REPLACE', 'COUNT', 'SUM', 'MIN', 'MAX', 'AVG', 'SAMPLE', 'GROUP_CONCAT',
  ]);

  // Common namespaces so completion + hover work before the user has typed
  // their own PREFIX lines. Declarations parsed from the document override
  // these on collision.
  const WELL_KNOWN_PREFIXES = Object.freeze({
    rdf: 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
    rdfs: 'http://www.w3.org/2000/01/rdf-schema#',
    owl: 'http://www.w3.org/2002/07/owl#',
    xsd: 'http://www.w3.org/2001/XMLSchema#',
    foaf: 'http://xmlns.com/foaf/0.1/',
    skos: 'http://www.w3.org/2004/02/skos/core#',
    dc: 'http://purl.org/dc/elements/1.1/',
    dct: 'http://purl.org/dc/terms/',
    sh: 'http://www.w3.org/ns/shacl#',
    ex: 'http://example.org/',
  });

  // Single words the tokenizer marks as keywords (`a` has its own token type).
  const KEYWORD_WORDS = [...new Set(
    SPARQL_KEYWORDS.filter((k) => k !== 'a').flatMap((k) => k.split(' ')),
  )];

  const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

  // ---- prefixes, variables, CURIEs -----------------------------------------

  // Blank out IRIs, string literals, and comments so prefix detection
  // doesn't trip over `http://…` inside `<>` or `foo:` inside a string.
  function stripNoise(doc) {
    return doc
      .replace(/<[^>]*>/g, ' ')
      .replace(/"(?:[^"\\]|\\.)*"/g, '""')
      .replace(/'(?:[^'\\]|\\.)*'/g, "''")
      .replace(/#[^\n]*/g, ' ');
  }

  /** Parse `PREFIX x: <iri>` and `@prefix x: <iri> .` into { x: iri }. */
  function parsePrefixes(doc) {
    const re = /(?:PREFIX|@prefix)\s+([A-Za-z][\w.-]*)?\s*:\s*<([^>]*)>/gi;
    const out = {};
    let m;
    while ((m = re.exec(doc)) !== null) out[m[1] ?? ''] = m[2];
    return out;
  }

  /** Prefixes referenced as `x:local` in the body (excluding declarations). */
  function usedPrefixes(doc) {
    const body = stripNoise(doc).replace(/(?:PREFIX|@prefix)\s+[\w.-]*\s*:\s*/gi, ' ');
    const set = new Set();
    const re = /(?:^|[\s(;,.[<])([A-Za-z][\w.-]*):[A-Za-z0-9_%-]/g;
    let m;
    while ((m = re.exec(body)) !== null) set.add(m[1]);
    return [...set];
  }

  /** Prefixes used but not declared in the document. */
  function missingPrefixes(doc) {
    const declared = parsePrefixes(doc);
    return usedPrefixes(doc).filter((p) => !(p in declared));
  }

  /** Format a single SPARQL PREFIX declaration line. */
  function prefixLine(prefix, iri) {
    return 'PREFIX ' + prefix + ': <' + iri + '>';
  }

  /**
   * PREFIX lines for missing prefixes that can be resolved from the
   * well-known table. Unknown prefixes (no canonical IRI) are returned
   * separately so the UI can prompt for their IRI instead of guessing.
   */
  function resolvableMissingDeclarations(doc) {
    const resolvable = [];
    const unknown = [];
    for (const p of missingPrefixes(doc)) {
      if (WELL_KNOWN_PREFIXES[p]) resolvable.push({ prefix: p, iri: WELL_KNOWN_PREFIXES[p] });
      else unknown.push(p);
    }
    return { resolvable, unknown };
  }

  /** Expand a `prefix:local` CURIE to a full IRI, or null if unresolvable. */
  function expandCurie(curie, prefixes) {
    const i = curie.indexOf(':');
    if (i < 0) return null;
    const ns = prefixes[curie.slice(0, i)];
    if (ns === undefined) return null;
    return ns + curie.slice(i + 1);
  }

  /** Distinct `?var` / `$var` names present in the document (no sigil). */
  function extractVars(doc) {
    const set = new Set();
    const re = /[?$]([A-Za-z_]\w*)/g;
    const body = stripNoise(doc);
    let m;
    while ((m = re.exec(body)) !== null) set.add(m[1]);
    return [...set];
  }

  /**
   * The CURIE under offset `pos` and its expanded IRI (document prefixes
   * override the well-known ones), or null when the offset isn't on a
   * resolvable CURIE.
   */
  function hoverInfo(doc, pos) {
    const isCurieChar = (ch) => /[A-Za-z0-9_.:%-]/.test(ch);
    let from = pos;
    let to = pos;
    while (from > 0 && isCurieChar(doc[from - 1])) from--;
    while (to < doc.length && isCurieChar(doc[to])) to++;
    const token = doc.slice(from, to);
    if (!token || !token.includes(':')) return null;
    if (/^[a-z]+:\/\//i.test(token)) return null; // a URL scheme, not a CURIE
    const iri = expandCurie(token, Object.assign({}, WELL_KNOWN_PREFIXES, parsePrefixes(doc)));
    if (iri === null) return null;
    return { curie: token, iri, from, to };
  }

  // ---- concepts --------------------------------------------------------------

  /**
   * The prefixes that spell a concept in this document: every prefix the
   * document binds to the vocabulary's base, plus each editor's default
   * (CDF: `c`, the workbench: the empty `:` prefix) unless the document
   * binds that prefix to some other namespace.
   */
  function conceptPrefixes(doc, vocab, defaults) {
    const declared = parsePrefixes(doc);
    const out = [];
    const base = vocab && vocab.base;
    if (base) {
      for (const [p, iri] of Object.entries(declared)) if (iri === base) out.push(p);
    }
    for (const p of defaults || []) {
      if (!(p in declared) && !out.includes(p)) out.push(p);
    }
    return out;
  }

  function classesOf(vocab) {
    return (vocab && vocab.classes) || {};
  }

  // property -> owning classes (a shared key like accountId spans classes).
  // Cached per vocabulary object: the tokenizer asks once per token.
  const OWNERS = new WeakMap();
  function propOwners(vocab) {
    const key = classesOf(vocab);
    let owners = OWNERS.get(key);
    if (owners) return owners;
    owners = Object.create(null); // no inherited names: `constructor` is not a property
    for (const [cls, info] of Object.entries(key)) {
      for (const p of info.props) (owners[p] = owners[p] || []).push(cls);
    }
    OWNERS.set(key, owners);
    return owners;
  }

  /**
   * What a concept local name is in the vocabulary:
   * {role: 'class' | 'property' | 'unknown', kind: <source kind> | null}.
   * A property shared by classes of different kinds has kind null.
   */
  function classifyConcept(name, vocab) {
    const classes = classesOf(vocab);
    if (Object.prototype.hasOwnProperty.call(classes, name)) {
      return { role: 'class', kind: classes[name].kind };
    }
    const owners = propOwners(vocab)[name];
    if (owners) {
      const kinds = [...new Set(owners.map((c) => classes[c].kind))];
      return { role: 'property', kind: kinds.length === 1 ? kinds[0] : null };
    }
    return { role: 'unknown', kind: null }; // not in the vocabulary
  }

  // ---- tokens ----------------------------------------------------------------

  const NEVER = '(?!)';

  /**
   * Highlighting tokens: [{from, to, type, name?, role?, kind?}] in document
   * order, where type is one of comment | concept | iri | string |
   * open-string | variable | keyword | a. Concept tokens (in any concept
   * prefix, or the full `<base…Name>` IRI) carry their local `name` and
   * `classifyConcept` result. Text between tokens is plain.
   */
  function tokenize(doc, vocab, options) {
    const base = vocab && vocab.base;
    const prefixes = conceptPrefixes(doc, vocab, options && options.conceptPrefixes);
    const re = new RegExp([
      '(#[^\\n]*)',                                                 // 1 comment (IRIs win below)
      base ? '(<' + escRe(base) + '[A-Za-z_][A-Za-z0-9_]*>)' : '(' + NEVER + ')', // 2 concept, full IRI
      '(<[^>\\n]*>)',                                               // 3 other IRI
      '("(?:[^"\\\\\\n]|\\\\.)*")',                                 // 4 string
      '("(?:[^"\\\\\\n]|\\\\.)*)',                                  // 5 UNTERMINATED string
      prefixes.length
        ? '((?<![\\w.-])(?:' + prefixes.map(escRe).join('|') + '):[A-Za-z_][A-Za-z0-9_]*)'
        : '(' + NEVER + ')',                                        // 6 concept, prefixed
      '([?$][A-Za-z_][A-Za-z0-9_]*)',                               // 7 variable
      '(\\b(?:' + KEYWORD_WORDS.join('|') + ')\\b)',                // 8 keyword
      '(\\ba\\b)',                                                  // 9 rdf:type shorthand
    ].join('|'), 'g');
    const out = [];
    let m;
    while ((m = re.exec(doc)) !== null) {
      const t = m[0];
      const tok = { from: m.index, to: m.index + t.length, type: '' };
      if (m[1]) tok.type = 'comment';
      else if (m[2] || m[6]) {
        tok.type = 'concept';
        tok.name = m[2] ? t.slice(1 + base.length, -1) : t.slice(t.indexOf(':') + 1);
        const c = classifyConcept(tok.name, vocab);
        tok.role = c.role;
        tok.kind = c.kind;
      } else if (m[3]) tok.type = 'iri';
      else if (m[4]) tok.type = 'string';
      else if (m[5]) tok.type = 'open-string';
      else if (m[7]) tok.type = 'variable';
      else if (m[8]) tok.type = 'keyword';
      else tok.type = 'a';
      out.push(tok);
    }
    return out;
  }

  // ---- suggestions -----------------------------------------------------------

  // The token being typed at the caret. Tokens never span lines, so only the
  // caret's line is scanned.
  const TOKEN_AT_CARET = /(<[^>\s]*|(?:[A-Za-z][\w.-]*)?:[A-Za-z0-9_]*|[?$][A-Za-z0-9_]*|[A-Za-z][A-Za-z0-9_]*)$/;

  function tokenBeforeCaret(doc, caret) {
    const upto = doc.slice(0, caret);
    const line = upto.slice(upto.lastIndexOf('\n') + 1);
    const m = line.match(TOKEN_AT_CARET);
    if (!m) return null;
    const start = upto.length - m[0].length;
    return { text: m[0], start, before: upto.slice(0, start) };
  }

  // The subject variable of the triple block the caret is in: last `?var`
  // that opens a statement (after `{`, `.`, or the block start).
  function currentSubject(before) {
    const seg = before.split(/[{.]/).pop() || '';
    const m = seg.match(/^\s*([?$][A-Za-z0-9_]+)/);
    return m ? m[1] : null;
  }

  // The class the query declares for `subjVar` (`?s a c:Class` or the full
  // IRI spelling), when the vocabulary knows it.
  function subjectClass(doc, subjVar, vocab, prefixes) {
    if (!subjVar) return null;
    const alts = [];
    if (prefixes.length) alts.push('(?:' + prefixes.map(escRe).join('|') + '):([A-Za-z0-9_]+)');
    if (vocab.base) alts.push('<' + escRe(vocab.base) + '([A-Za-z0-9_]+)>');
    if (!alts.length) return null;
    const m = doc.match(new RegExp(escRe(subjVar) + '\\s+a\\s+(?:' + alts.join('|') + ')'));
    const name = m && (m[1] || m[2]);
    return name && Object.prototype.hasOwnProperty.call(classesOf(vocab), name) ? name : null;
  }

  function startsWithCI(word, typed) {
    return word.toLowerCase().startsWith(typed.toLowerCase());
  }

  /**
   * What to offer at `caret`: {from, to, items} (items in display order), or
   * null when nothing applies. Each item is
   * {label, insert, hint, tag, type}: `insert` replaces doc[from, to);
   * `type` is class | property | variable | keyword | function | namespace;
   * `tag` is the owning source kind for concepts, else 'kw' / 'var'.
   *
   *   ?x / $x         -> variables already in the query
   *   after `a`       -> classes only
   *   <concept prefix>:  or  <base…  -> the subject's class properties (all
   *                      classes' when the subject's class is unknown), then
   *                      classes, in the spelling being typed
   *   bare word       -> keywords, functions, prefix names
   *
   * options.conceptPrefixes: the editor's default concept prefixes (see
   * conceptPrefixes). The items are already filtered and ordered, so an
   * editor should show them as given rather than re-rank them.
   */
  function suggest(doc, caret, vocab, options) {
    const tok = tokenBeforeCaret(doc, caret);
    if (!tok) return null;
    const v = vocab || { classes: {} };
    const classes = classesOf(v);
    const base = v.base || '';
    const prefixes = conceptPrefixes(doc, v, options && options.conceptPrefixes);
    const result = (items) => (items.length ? { from: tok.start, to: caret, items } : null);

    if (tok.text.startsWith('?') || tok.text.startsWith('$')) {
      const sigil = tok.text[0];
      return result(extractVars(doc).map((n) => sigil + n).sort()
        .filter((n) => n.startsWith(tok.text) && n !== tok.text)
        .map((n) => ({ label: n, insert: n, hint: 'variable', tag: 'var', type: 'variable' })));
    }

    const afterA = /(^|[\s;,({])a\s+$/.test(tok.before);
    // Full-IRI spelling engages from the first `<`: any prefix of the concept
    // base means a concept IRI is being typed, no need to finish the base.
    const iriForm = !!base && tok.text.startsWith('<')
      && ('<' + base).startsWith(tok.text.slice(0, base.length + 1));
    const colon = tok.text.startsWith('<') ? -1 : tok.text.indexOf(':');
    const typedPrefix = colon >= 0 ? tok.text.slice(0, colon) : null;
    const prefixedForm = typedPrefix !== null && prefixes.includes(typedPrefix);

    if (afterA || iriForm || prefixedForm) {
      // Spell in the form being typed: prefix:Name, or the full concept IRI.
      const spellPrefix = prefixedForm ? typedPrefix : prefixes[0];
      if (!iriForm && !prefixedForm && spellPrefix === undefined && !base) return null;
      const useIri = iriForm || (!prefixedForm && spellPrefix === undefined);
      const local = iriForm ? (tok.text.length > base.length ? tok.text.slice(1 + base.length) : '')
        : prefixedForm ? tok.text.slice(colon + 1)
        : colon >= 0 ? null : tok.text; // a bare word after `a` filters too
      if (local === null) return null; // `a xsd:` — a non-concept prefix
      const spell = (name) => (useIri ? '<' + base + name + '>' : spellPrefix + ':' + name);
      const short = (name) => (useIri ? '…' + base.slice(-1) + name : spellPrefix + ':' + name);
      const out = [];
      if (iriForm && local === '') {
        // The bare namespace is a completion too — it is what a PREFIX
        // declaration needs, and what you want back after deleting the '#'.
        const ns = { label: '<' + base + '>', insert: '<' + base + '>',
          hint: 'namespace · PREFIX base', tag: 'kw', type: 'namespace' };
        if (/PREFIX\s+[A-Za-z_][\w-]*:\s*<?[^\s>]*$/.test(tok.before + tok.text)) {
          return result([ns]);
        }
        out.push(ns);
      }
      if (!afterA) {
        const owners = propOwners(v);
        const subj = subjectClass(doc, currentSubject(tok.before), v, prefixes);
        const scoped = subj ? [subj] : Object.keys(classes);
        const seen = new Set();
        for (const cls of scoped) {
          for (const p of classes[cls].props) {
            if (seen.has(p) || !p.startsWith(local)) continue;
            seen.add(p);
            out.push({ label: short(p), insert: spell(p), tag: classes[cls].kind,
              hint: subj ? subj : owners[p].join(', '), type: 'property' });
          }
        }
      }
      for (const [cls, info] of Object.entries(classes)) {
        if (!cls.startsWith(local)) continue;
        out.push({ label: short(cls), insert: spell(cls), tag: info.kind,
          hint: 'class · ' + info.source, type: 'class' });
      }
      // after `a` only classes make sense; otherwise properties lead
      return result(afterA ? out.filter((c) => c.type !== 'property') : out);
    }

    if (typedPrefix !== null || tok.text.startsWith('<')) return null;

    const typed = tok.text;
    const out = [];
    for (const k of SPARQL_KEYWORDS) {
      if (startsWithCI(k, typed) && k !== typed) {
        out.push({ label: k, insert: k, hint: 'SPARQL', tag: 'kw', type: 'keyword' });
      }
    }
    for (const f of SPARQL_FUNCTIONS) {
      if (startsWithCI(f, typed) && f !== typed) {
        out.push({ label: f, insert: f, hint: 'function', tag: 'kw', type: 'function' });
      }
    }
    const known = Object.assign({}, WELL_KNOWN_PREFIXES, parsePrefixes(doc));
    for (const p of prefixes) if (p && !(p in known) && base) known[p] = base;
    for (const [p, iri] of Object.entries(known)) {
      if (p && startsWithCI(p, typed)) {
        out.push({ label: p + ':', insert: p + ':', hint: iri, tag: 'kw', type: 'namespace' });
      }
    }
    return result(out);
  }

  // ---- formatting ------------------------------------------------------------

  // Literal and IRI spellings the formatter must never touch: long strings
  // first, so `"""a "b" c"""` is one span, not three.
  const PROTECTED = /("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|<[^>\s]*>)/g;

  /**
   * Pretty-print a SPARQL query: PREFIX lines, one triple statement per
   * line, `;` continuations hanging-indented, braces on structural lines.
   * Strings and IRIs are protected first, so no query content is ever
   * altered — only whitespace moves. A query with a `#` comment is returned
   * unchanged: collapsing its newline would comment out the rest of the line.
   */
  function formatSparql(src) {
    const store = [];
    let s = String(src).replace(PROTECTED, (m) => {
      store.push(m);
      return '\u0000' + (store.length - 1) + '\u0000';
    });
    if (s.includes('#')) return String(src);
    s = s.replace(/\s+/g, ' ')
      .replace(/([{}])/g, ' $1 ')
      .replace(/(^|\s)\.(?=\s|$)/g, ' . ')
      .replace(/\s;\s/g, ' ; ')
      .trim();

    const words = s.split(' ').filter(Boolean);
    const lines = [];
    let depth = 0, cur = [], hang = false;
    const flush = (suffix) => {
      if (!cur.length && !suffix) return;
      const indent = '  '.repeat(depth + (hang && cur[0] !== 'FILTER' && cur[0] !== 'OPTIONAL' && cur[0] !== 'VALUES' ? 1 : 0));
      lines.push((indent + cur.join(' ') + (suffix || '')).replace(/\(\s+/g, '(').replace(/\s+\)/g, ')'));
      cur = [];
    };
    for (const w of words) {
      if (w === 'PREFIX' && depth === 0) { flush(''); hang = false; cur.push(w); }
      else if (w === '{') { cur.push(w); flush(''); depth += 1; hang = false; }
      else if (w === '}') { flush(''); depth = Math.max(0, depth - 1); hang = false; cur.push(w); flush(''); }
      else if (w === '.') { flush(' .'); hang = false; }
      else if (w === ';') { flush(' ;'); hang = true; }
      else if (w === 'SELECT' && depth === 0) { flush(''); hang = false; cur.push(w); }
      else { cur.push(w); }
    }
    flush('');
    return lines.join('\n').replace(/\u0000(\d+)\u0000/g, (_, i) => store[+i]);
  }

  const api = Object.freeze({
    VOCAB_CONTRACT,
    SPARQL_KEYWORDS,
    SPARQL_FUNCTIONS,
    WELL_KNOWN_PREFIXES,
    parsePrefixes,
    usedPrefixes,
    missingPrefixes,
    prefixLine,
    resolvableMissingDeclarations,
    expandCurie,
    extractVars,
    hoverInfo,
    conceptPrefixes,
    classifyConcept,
    tokenize,
    suggest,
    formatSparql,
  });

  root.SparqlEditorCore = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(globalThis);
