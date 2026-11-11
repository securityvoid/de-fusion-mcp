# changelog.md — docs/specs/core/

| Version | Date | Change |
|---|---|---|
| 0.1.0 | 2026-11-10 | Initial spec: REQ-CORE-001..040, NFR-CORE-001..005, DEP-CORE-001..003, TASK-CORE-001..025 |
| 0.1.1 | 2026-11-10 | Reconcile with tool sets: `@action` `main_thread`/`name`, Ctx `allowed`/`profile_items`/`units`, Design product lookup, JSON attrs, `call_inline` (§ 5.11), types `Entity`/`MeshBody`/`Point2`/`JointOrigin`/`RigidGroup`/`ContactSet`/`CamSetup`/`CamOperation`, `TimelineItem` index form, unions and nested resolution, Plane/Axis component scope, non-reentrant dispatcher, error extras, `INVALID_PROFILE` code, input units, tagged `LIVE_SCENARIOS`, module accessors (§ 12); REQ-CORE-041..049; TASK-CORE-003/007/008/010–013/015–017/021 updated |
| 0.1.2 | 2026-11-10 | spec-consistency s1 r1 fixes (C1/C2/C3 findings) |
| 0.1.3 | 2026-11-10 | spec-consistency s1 r2 and s2 r1 fixes (C1 r2, C4 findings) |
| 0.2.0 | 2026-11-10 | § 5.1: allow `dict[str, T]` (T scalar); import check also counts actions from `<module>_*` modules |
| 0.3.0 | 2026-11-11 | § 10 live harness: final step freezes editable `defusion-live-*` leftovers |
