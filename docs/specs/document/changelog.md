# changelog.md — docs/specs/document/

| Version | Date | Change |
|---|---|---|
| 0.1.0 | 2026-11-10 | Initial spec: REQ-DOC-001..022, TASK-DOC-001..010 |
| 0.1.1 | 2026-11-10 | Reconcile: `_make_box` uses sketch.create + sketch.rectangle + model.extrude; § 10 tuple LIVE_SCENARIOS with action tags; task waits cite TASK-SK-003/004, TASK-MDL-002 |
| 0.1.2 | 2026-11-10 | spec-consistency s1 r1 fixes (C1/C2/C3 findings) |
| 0.1.3 | 2026-11-10 | spec-consistency s1 r2 and s2 r1 fixes (C1 r2, C4 findings) |
| 0.2.0 | 2026-11-11 | Live corrections: `datafile_set` renames only (`DataFile.description` has no setter; REQ-DOC-015, § 6.9), licence-restricted export is `UNSUPPORTED` (§ 8.1), `_doc_summary` tolerates `dataFile` error during upload (§ 6.3) |
| 0.3.0 | 2026-11-11 | Live scenarios mark every saved `defusion-live-*` document read-only after its last edit (Personal account editable-document cap); harness final step freezes leftovers (core § 10) |
| 0.3.1 | 2026-11-11 | REQ-DOC-006 acceptance: Fusion-refused (restricted) export format returns `UNSUPPORTED` (validation r2 V1-r1-2) |
