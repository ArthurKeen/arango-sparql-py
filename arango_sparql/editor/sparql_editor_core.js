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

  // A local name as the library's producer writes one
  // (arango_sparql/schema/vocabulary.py): a safe subset of SPARQL's PN_LOCAL.
  const LOCAL = '[A-Za-z_][A-Za-z0-9_-]*';

  // ---- lexing ----------------------------------------------------------------

  // IRIREF's characters (SPARQL 1.1 grammar [139]): no whitespace, controls,
  // or any of <>"{}|^`\
  const IRIREF = /<[^<>"{}|^`\\\u0000-\u0020]*>/y;
  // An IRI still being typed: no closing `>` yet, but a scheme colon, which a
  // less-than (`?a < 3`, `?a<?b`) does not have. Its `#` is not a comment.
  const OPEN_IRI = /<[^<>"{}|^`\\\u0000-\u0020]*/y;

  let lexCache = { doc: null, spans: [] };

  /**
   * The spans of `doc` that are not SPARQL syntax, in order:
   * [{type: 'comment' | 'iri' | 'open-iri' | 'string' | 'open-string', from, to,
   *   inParens}].
   * Strings are short or long (triple-quoted), single- or double-quoted,
   * and escape-aware; an unterminated short string runs to the end of its
   * line, a long one to the end of the text. A backslash outside a literal
   * escapes the next character (PN_LOCAL_ESC, e.g. `:O\'Brien`), so it never
   * opens a string. `inParens` marks a span inside an expression's
   * parentheses, where `<` can also be less-than. One pass; the last result
   * is cached, because every helper below asks for the same text.
   */
  function lex(doc) {
    if (lexCache.doc === doc) return lexCache.spans;
    const spans = [];
    const n = doc.length;
    let i = 0, depth = 0;
    while (i < n) {
      const ch = doc[i];
      if (ch === '\\') { i += 2; continue; }
      if (ch === '(') { depth += 1; i += 1; continue; }
      if (ch === ')') { depth = Math.max(0, depth - 1); i += 1; continue; }
      if (ch === '#') {
        const nl = doc.indexOf('\n', i);
        const to = nl < 0 ? n : nl;
        spans.push({ type: 'comment', from: i, to, inParens: depth > 0 });
        i = to;
        continue;
      }
      if (ch === '<') {
        IRIREF.lastIndex = i;
        const m = IRIREF.exec(doc);
        if (m) {
          spans.push({ type: 'iri', from: i, to: i + m[0].length, inParens: depth > 0 });
          i += m[0].length;
        } else {
          OPEN_IRI.lastIndex = i;
          const open = OPEN_IRI.exec(doc)[0];
          if (open.includes(':')) {
            spans.push({ type: 'open-iri', from: i, to: i + open.length, inParens: depth > 0 });
            i += open.length;
          } else {
            i += 1; // less-than
          }
        }
        continue;
      }
      if (ch === '"' || ch === "'") {
        const q = doc.startsWith(ch + ch + ch, i) ? ch + ch + ch : ch;
        let j = i + q.length;
        let closed = false;
        while (j < n) {
          if (doc[j] === '\\') { j += 2; continue; }
          if (q.length === 1 && doc[j] === '\n') break;
          if (doc.startsWith(q, j)) { j += q.length; closed = true; break; }
          j += 1;
        }
        const to = Math.min(j, n);
        spans.push({ type: closed ? 'string' : 'open-string', from: i, to, inParens: depth > 0 });
        i = to;
        continue;
      }
      i += 1;
    }
    lexCache = { doc, spans };
    return spans;
  }

  const NOISE = ['comment', 'iri', 'open-iri', 'string', 'open-string'];
  const TEXT = ['comment', 'string', 'open-string']; // keeps IRIs

  // `doc` with the spans of the given types blanked to spaces (newlines kept),
  // so every offset still lines up with the original.
  function blank(doc, types) {
    let out = '';
    let last = 0;
    for (const s of lex(doc)) {
      if (!types.includes(s.type)) continue;
      out += doc.slice(last, s.from) + doc.slice(s.from, s.to).replace(/[^\n]/g, ' ');
      last = s.to;
    }
    return out + doc.slice(last);
  }

  // ---- prefixes, variables, CURIEs -----------------------------------------

  /** Parse `PREFIX x: <iri>` and `@prefix x: <iri> .` into { x: iri }. */
  function parsePrefixes(doc) {
    const re = /(?:PREFIX|@prefix)\s+([A-Za-z][\w.-]*)?\s*:\s*<([^>]*)>/gi;
    const text = blank(doc, TEXT); // not inside comments or strings
    const out = {};
    let m;
    while ((m = re.exec(text)) !== null) out[m[1] ?? ''] = m[2];
    return out;
  }

  /** Prefixes referenced as `x:local` in the body (excluding declarations). */
  function usedPrefixes(doc) {
    const body = blank(doc, NOISE).replace(/(?:PREFIX|@prefix)\s+[\w.-]*\s*:\s*/gi, ' ');
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
    const body = blank(doc, NOISE);
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
    const conceptIri = base ? new RegExp('^<' + escRe(base) + '(' + LOCAL + ')>$') : null;
    const re = new RegExp([
      prefixes.length                                                   // 1 concept, prefixed
        ? '((?<![\\w.-])(?:' + prefixes.map(escRe).join('|') + '):' + LOCAL + ')'
        : '(' + NEVER + ')',
      '([?$][A-Za-z_][A-Za-z0-9_]*)',                                   // 2 variable
      '(\\b(?:' + KEYWORD_WORDS.join('|') + ')\\b)',                    // 3 keyword
      '((?<![\\w:.-])a(?![\\w:-]))',                                    // 4 rdf:type shorthand
    ].join('|'), 'g');
    const out = [];
    const concept = (from, to, name) => {
      const c = classifyConcept(name, vocab);
      return { from, to, type: 'concept', name, role: c.role, kind: c.kind };
    };
    const scan = (from, to) => {
      const text = doc.slice(from, to);
      re.lastIndex = 0;
      let m;
      while ((m = re.exec(text)) !== null) {
        const a = from + m.index;
        const b = a + m[0].length;
        if (m[1]) out.push(concept(a, b, m[0].slice(m[0].indexOf(':') + 1)));
        else out.push({ from: a, to: b, type: m[2] ? 'variable' : m[3] ? 'keyword' : 'a' });
      }
    };
    let last = 0;
    for (const s of lex(doc)) {
      scan(last, s.from);
      const iri = s.type === 'iri' && conceptIri && doc.slice(s.from, s.to).match(conceptIri);
      const type = s.type === 'open-iri' ? 'iri' : s.type;
      out.push(iri ? concept(s.from, s.to, iri[1]) : { from: s.from, to: s.to, type });
      last = s.to;
    }
    scan(last, doc.length);
    return out;
  }

  // ---- suggestions -----------------------------------------------------------

  // The token being typed at the caret. Tokens never span lines, so only the
  // caret's line is scanned.
  const TOKEN_AT_CARET = /(<[^>\s]*|(?:[A-Za-z][\w.-]*)?:[A-Za-z0-9_-]*|[?$][A-Za-z0-9_]*|[A-Za-z][A-Za-z0-9_]*)$/;

  function tokenBeforeCaret(doc, caret) {
    const upto = doc.slice(0, caret);
    const line = upto.slice(upto.lastIndexOf('\n') + 1);
    const m = line.match(TOKEN_AT_CARET);
    if (!m) return null;
    const start = upto.length - m[0].length;
    return { text: m[0], start, before: upto.slice(0, start) };
  }

  // Is `caret` inside a comment or a string literal (where nothing applies)?
  function inText(doc, caret) {
    for (const s of lex(doc)) {
      if (s.from >= caret) break;
      if (s.type === 'iri' || s.type === 'open-iri') continue;
      if (s.type === 'string' ? caret < s.to : caret <= s.to) return true;
    }
    return false;
  }

  // The subject variable's name (no sigil) of the statement the caret is in:
  // the `?var` that opens the text after the last `{`, `}` or statement-ending
  // `.`, once any FILTER / BIND constraints before it are skipped (they need
  // no `.` after them). `before` has comments, strings and IRIs blanked, so a
  // `.` or `(` inside them never counts, and neither does the `.` of a
  // decimal (`1.5`) or a dotted name.
  function currentSubject(before) {
    let seg = before.split(/[{}]|\.(?=\s|$|[?${}])/).pop() || '';
    for (;;) {
      const m = seg.match(/^\s*(?:FILTER|BIND)\s*(?:[A-Za-z_]\w*\s*)?\(/i);
      if (!m) break;
      let depth = 0;
      let i = m[0].length - 1;
      for (; i < seg.length; i += 1) {
        if (seg[i] === '(') depth += 1;
        else if (seg[i] === ')' && --depth === 0) break;
      }
      if (i >= seg.length) return null; // the caret is inside the constraint
      seg = seg.slice(i + 1);
    }
    const v = seg.match(/^\s*[?$]([A-Za-z0-9_]+)/);
    return v ? v[1] : null;
  }

  // The class the query declares for the subject (`?s a c:Class`, or the full
  // IRI spelling), when the vocabulary knows it: the last declaration before
  // the caret, so a sibling UNION branch or a later statement does not
  // decide. `text` is the text before the caret with comments and strings
  // blanked (IRIs kept); `?s` and `$s` are the same variable.
  function subjectClass(text, subj, vocab, prefixes) {
    if (!subj) return null;
    const alts = [];
    if (prefixes.length) alts.push('(?:' + prefixes.map(escRe).join('|') + '):(' + LOCAL + ')');
    if (vocab.base) alts.push('<' + escRe(vocab.base) + '(' + LOCAL + ')>');
    if (!alts.length) return null;
    const re = new RegExp('[?$]' + escRe(subj) + '\\s+a\\s+(?:' + alts.join('|') + ')', 'g');
    const classes = classesOf(vocab);
    let found = null;
    let m;
    while ((m = re.exec(text)) !== null) {
      const name = m[1] || m[2];
      if (Object.prototype.hasOwnProperty.call(classes, name)) found = name;
    }
    return found;
  }

  function startsWithCI(word, typed) {
    return word.toLowerCase().startsWith(typed.toLowerCase());
  }

  /**
   * What to offer at `caret`: {from, to, items} (items in display order), or
   * null when nothing applies (including inside a comment or a string). Each
   * item is {label, insert, hint, tag, type}: `insert` replaces doc[from, to);
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
    if (!tok || inText(doc, caret)) return null;
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

    const textBefore = blank(doc, TEXT).slice(0, tok.start);
    const afterA = /(^|[\s;,({])a\s+$/.test(textBefore);
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
        if (/PREFIX\s+(?:[A-Za-z][\w.-]*)?:\s*<?[^\s>]*$/i.test(tok.before + tok.text)) {
          return result([ns]);
        }
        out.push(ns);
      }
      if (!afterA) {
        const owners = propOwners(v);
        const subj = subjectClass(textBefore, currentSubject(blank(doc, NOISE).slice(0, tok.start)),
          v, prefixes);
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

  /**
   * Pretty-print a SPARQL query: PREFIX lines, one triple statement per
   * line, `;` continuations hanging-indented, braces on structural lines.
   * IRIs and literals (every quoting form, escape-aware) are protected first,
   * so no query content is ever altered — only whitespace moves. Returned
   * unchanged instead when joining lines could change the query:
   *   - it has a `#` comment (the lines joined after it would be commented out)
   *   - it has an unterminated literal (there is no end to protect), or an
   *     unterminated IRI holding a `#` (rdflib reads that `#` as a comment)
   *   - an "IRI" inside an expression's parentheses holds a `#`: rdflib reads
   *     `FILTER(?a<1)#c>` as less-than followed by a comment, not as an IRI
   */
  function formatSparql(src) {
    const text = String(src);
    const spans = lex(text);
    const hash = (sp) => text.slice(sp.from, sp.to).includes('#');
    const risky = spans.some((sp) => sp.type === 'comment' || sp.type === 'open-string'
      || (sp.type === 'open-iri' && hash(sp)) || (sp.type === 'iri' && sp.inParens && hash(sp)));
    if (risky) return text;
    const store = [];
    let s = '';
    let last = 0;
    for (const sp of spans) {
      store.push(text.slice(sp.from, sp.to));
      s += text.slice(last, sp.from) + '\u0000' + (store.length - 1) + '\u0000';
      last = sp.to;
    }
    s += text.slice(last);
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
