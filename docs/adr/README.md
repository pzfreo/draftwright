# Architecture decision records (draftwright-rust)

Each record states:
- a decision;
- what it adopts from, translates, or supersedes in the Python implementation's records
  (pzfreo/draftwright-python `docs/adr/`, which stay unedited there as the record of why);
- a numbered list of invariants, each naming the test that fails if it is broken.

As in the Python repo, no record text becomes accepted without the maintainer's sign-off.
Records here start as **Proposed**, and are marked **Accepted** with the date the maintainer approves them.

| Record | Owns | Status |
| --- | --- | --- |
| [R1: the document model](R1-document-model.md) | Analysis, Spec and Plan; the five operations; the CLI, service and MCP adapters | Accepted 2026-10-07 |
| [R2: the kernel](R2-kernel.md) | haecceity in place of OpenCascade; accuracy over parity; the verdict system | Accepted 2026-10-07 |
| [R3: revisions](R3-revisions.md) | quiddity's correspondence; rebinding a spec to a revised part | Accepted 2026-10-07 |
| [R4: carried invariants](R4-carried-invariants.md) | Each Python ADR rule, marked kept, translated or retired, against `docs/rules/` | Accepted 2026-10-07 |
| [R5: multi-sheet drawings](R5-multi-sheet.md) | Sheet allocation (policy accepted 2026-10-11), sheet pins, SHEET n OF m, zones and cross-references, multi-sheet formats and MCP addresses | Proposed (policy accepted 2026-10-11) |
