## Conflict Detection Report

Mode: new (bootstrap; no existing `.planning/` context to check against).
Precedence: ADR > SPEC > PRD > DOC. Docs ingested: 8 (2 ADR, 1 SPEC, 1 PRD,
4 DOC). No `UNKNOWN`/low-confidence classifications. No `locked` ADRs — so no
LOCKED-vs-LOCKED contradiction is possible in this set.

### BLOCKERS (0)

None.

### WARNINGS (0)

None. There is a single PRD, so no competing acceptance variants across PRDs
can arise; each §3 acceptance criterion has exactly one measurable definition.

### INFO (4)

[INFO] Benign cross-reference cycles among redirect stubs and the canonical PRD
  Found: cycle detection on the cross_refs graph found loops — `PRD.md → vision.md → PRD.md`, and (via the `decisions/` directory reference) `PRD.md → 0001-named-graphs-per-document.md → PRD.md` and `PRD.md → 0002-cross-subject-optional-leftjoin.md → PRD.md`.
  Note: all three back-referencing docs (vision.md, ADR-0001, ADR-0002) are redirect stubs whose content was consolidated into PRD.md (Appendices C, B.1, B.2). Stubs carry zero synthesizable content, so these are navigational back-references, not content-dependency cycles — no synthesis hazard. Synthesis proceeded on the full set. Sources: /Users/plosiewicz/dev/arango-sparql-py/docs/architecture/PRD.md, docs/architecture/vision.md, docs/architecture/decisions/0001-named-graphs-per-document.md, docs/architecture/decisions/0002-cross-subject-optional-leftjoin.md

[INFO] ADR decision content lives in the PRD, not the ADR files
  Found: ADR-0001 and ADR-0002 source files are one-line redirect stubs with no Status field (classifier set locked=false accordingly); the authoritative decisions ("Accepted" / "Partially resolved") live in PRD.md Appendix B.1 / B.2.
  Note: decisions.md records the ADRs at ADR precedence but points to the PRD appendix as authoritative content. The PRD itself states "Where an ADR and the main body disagree, the main body wins" — a documented internal precedence that does not conflict with the ADR>PRD default here because the ADR content is physically hosted inside the PRD. Sources: docs/architecture/PRD.md (Appendix B), docs/architecture/decisions/0001-named-graphs-per-document.md, docs/architecture/decisions/0002-cross-subject-optional-leftjoin.md

[INFO] implementation_plan.md declares a self-scoped precedence carve-out vs the PRD
  Found: implementation_plan.md (DOC) states "When the two disagree about intent, the PRD wins; when they disagree about status, this file is the source of truth."
  Note: consistent with default precedence (DOC < PRD) for intent; the plan claims status-truth authority. Downstream roadmapper should treat the PRD as intent/spec source and implementation_plan.md as work-package status source. No contradiction to resolve. Sources: docs/architecture/implementation_plan.md, docs/architecture/PRD.md

[INFO] PRD §3.1 acceptance sub-clause is consciously accepted as violated
  Found: §3.1 requires "no single XFAIL bucket consuming > 30% of remaining failures"; the measured COVERAGE_REPORT shows `ServiceGraphPattern` at 4/9 = 44.4% of algebra XFAILs.
  Note: the PRD documents this as an intentional, well-understood exception — the dominant bucket is SPARQL federation/SERVICE (a single deferred feature), the §3.1 primary bar (≥ 25%) is cleared by 71pp (96.4%), and the ratio can only be lowered by shipping federation. Flagged for transparency so the roadmapper does not treat this as an open failing gate. Sources: docs/architecture/PRD.md (§3.1), tests/w3c/COVERAGE_REPORT.md
