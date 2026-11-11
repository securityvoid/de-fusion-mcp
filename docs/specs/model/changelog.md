# changelog.md — docs/specs/model/

| Version | Date | Change |
|---|---|---|
| 0.1.0 | 2026-11-10 | Initial spec: REQ-MDL-001..047, DEP-MDL-001..002, TASK-MDL-001..014 |
| 0.1.1 | 2026-11-10 | Reconcile: set_design_type destructive (read 10/destructive 4/write 49, confirm in live scenarios); coil request stored via ctx.attr_set(dict); Entity from core § 5.4 (DEP-MDL-001 satisfied); live scenarios in core § 10 tagged tuple format |
| 0.1.2 | 2026-11-10 | spec-consistency s1 r1 fixes (C1/C2/C3 findings) |
| 0.1.3 | 2026-11-10 | spec-consistency s1 r2 and s2 r1 fixes (C1 r2, C4 findings) |
| 0.1.4 | 2026-11-10 | TASK-MDL-014 live observations recorded in design.md: `kind` Feature test, `owner` parent fallback, thread `faces.count`, offset_faces list input, ruled via `createBRepEdgeProfile` (BRepEdge only), axis `setByEdge`, library lookup skips `RuntimeError`, set_modeling_in_assembly needs assembly intent, coil participantBodies UNSUPPORTED, torus axis, thicken bodies, restricted-licence config |
| 0.2.0 | 2026-11-11 | `create_configured_design` precheck uses `isConfiguredDesign`; FUSION_ERROR on licence-restricted accounts; TASK-MDL-014 verify excludes `model.create_configured_design` |
| 0.2.1 | 2026-11-11 | REQ-MDL-047 acceptance excludes licence-restricted `config` scenario (validation r2 V1-r1-1) |
