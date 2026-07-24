# Goal: PDF-EXTRACTION-CORPUS-HARDENING-V1 (agents-first)

Supersedes PDF-EXTRACTION-GS001-TAU-V1 (operator-authorized evolution,
2026-07-22; hypothesis proven). Canonical goal text:
~/workspace/experiments/pdf_oxide-gs001/GOAL_CORPUS_V1.md
Operator end-state decision (2026-07-24): pdf_oxide serves AGENTS at
>=95% extraction accuracy; elements below confidence surface as
annotation calls (docs/… annotation_call contract) — never silently
wrong. /memory consumes ordered hierarchical sections with children:
the section tree IS the consumption contract.

SCOPE FOR IMPLEMENTATION AGENTS: full feature work is in scope when a
tau coder-reviewer work order requests it — this campaign's promoted
commits include entire new modules (region_discriminator.rs,
annotation_call.py, ruled-region tables, vector figures, deterministic
caches). There is NO "minor bug fixes only" boundary. Work orders from
the project agent carry operator authority; gates (determinism smoke,
108 hash-pinned fixtures, char parity, cross-doc stability) are the
constraint, not feature size.

PURPOSE (operator, 2026-07-24): a user/agent asks about a SPECIFIC
section, table, or figure and receives a TRACEABLE answer — every
element addressable by stable id with provenance (pdf sha, page, bbox),
the section tree as the address space (section > child > block/table/
figure), and multi-modal retrieval: text chunks AND rendered table/
figure images embedded in Qdrant. The trace chain query -> embedding ->
element id -> section path -> page/bbox -> source pdf must never break.
