# changelog.md — docs/specs/history/

| Version | Date | Change |
|---|---|---|
| 0.1.0 | 2026-11-10 | Initial spec: REQ-HIS-001..REQ-HIS-022, DEP-HIS-001..DEP-HIS-004, TASK-HIS-001..TASK-HIS-012 |
| 0.1.1 | 2026-11-10 | Reconcile: model action/param names (construction_plane/axis, rect/circ_pattern, move decomposition, thread faces), ModelParameter expressions + user parameter steps, coil defusion.request/1 consumption and base-sketch skip, core TimelineItem/call_inline references, per-action LIVE_SCENARIOS tags |
| 0.1.2 | 2026-11-10 | spec-consistency s1 r1 fixes (C1/C2/C3 findings) |
| 0.1.3 | 2026-11-10 | spec-consistency s1 r2 and s2 r1 fixes (C1 r2, C4 findings) |
| 0.2.0 | 2026-11-11 | Live corrections: undo via `Transaction.Undo`/`Transaction.Redo` text commands (§ 4.8, § 9.1; `UndoCommand.execute()` is a no-op), reorder `moved` read from the re-read index (§ 4.2) |
