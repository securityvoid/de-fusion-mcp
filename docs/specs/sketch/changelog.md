# changelog.md — docs/specs/sketch/

| Version | Date | Change |
|---|---|---|
| 0.1.0 | 2026-11-10 | Initial spec: REQ-SK-001..022, TASK-SK-001..010 |
| 0.1.1 | 2026-11-10 | Reconcile: SkPoint/to_sketch replaced by core Point2; live fixtures use exact sketch/model params (extrude, construction_plane); LIVE_SCENARIOS in core § 10 tagged format; DEP-SK-002 added |
| 0.2.0 | 2026-11-10 | Add actions text, ellipse, slot, point, fillet, delete_constraint, delete_dimension, mirror, pattern (REQ-SK-023..031, TASK-SK-011..013); info/replay cover ellipses, texts, standalone points and owned curves (REQ-SK-032, TASK-SK-014); live scenarios for 28 actions |
| 0.2.1 | 2026-11-10 | spec-consistency s1 r1 fixes (C1/C2/C3 findings) |
| 0.2.2 | 2026-11-10 | spec-consistency s1 r2 and s2 r1 fixes (C1 r2, C4 findings) |
| 0.3.0 | 2026-11-11 | § 3.2: SketchText angle is read via the `text_angle` helper (Fusion raises on `SketchText.angle`) |
| 0.4.0 | 2026-11-11 | `auto_constrain` returns FUSION_ERROR on licence-restricted accounts; TASK-SK-010 verify excludes `sketch.auto_constrain` |
| 0.4.1 | 2026-11-11 | REQ-SK-022 acceptance: `auto_constrain` excluded on licence-restricted accounts |
| 0.4.2 | 2026-11-11 | design § 9 and § 10.2: `auto_constrain` FUSION_ERROR on licence-restricted accounts, excluded from live verify |
