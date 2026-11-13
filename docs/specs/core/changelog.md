# changelog.md — docs/specs/core/

| Version | Date | Change |
|---|---|---|
| 0.1.0 | 2026-11-10 | Initial spec: REQ-CORE-001..040, NFR-CORE-001..005, DEP-CORE-001..003, TASK-CORE-001..025 |
| 0.1.1 | 2026-11-10 | Reconcile with tool sets: `@action` `main_thread`/`name`, Ctx `allowed`/`profile_items`/`units`, Design product lookup, JSON attrs, `call_inline` (§ 5.11), types `Entity`/`MeshBody`/`Point2`/`JointOrigin`/`RigidGroup`/`ContactSet`/`CamSetup`/`CamOperation`, `TimelineItem` index form, unions and nested resolution, Plane/Axis component scope, non-reentrant dispatcher, error extras, `INVALID_PROFILE` code, input units, tagged `LIVE_SCENARIOS`, module accessors (§ 12); REQ-CORE-041..049; TASK-CORE-003/007/008/010–013/015–017/021 updated |
| 0.1.2 | 2026-11-10 | spec-consistency s1 r1 fixes (C1/C2/C3 findings) |
| 0.1.3 | 2026-11-10 | spec-consistency s1 r2 and s2 r1 fixes (C1 r2, C4 findings) |
| 0.2.0 | 2026-11-10 | § 5.1: allow `dict[str, T]` (T scalar); import check also counts actions from `<module>_*` modules |
| 0.3.0 | 2026-11-11 | § 10 live harness: final step freezes editable `defusion-live-*` leftovers |
| 0.4.0 | 2026-11-11 | Bulk entries, read-only guard, folder limit: `types.bulk`/`ctx.bulk` (§ 5.12), `@action` `edits_document` + `preserve_read_only` + pipeline `ensure_editable` + `made_editable` envelope key (§ 5.1, § 5.3, § 5.5, § 5.13), `guard.py` read-only set/verify (10 × 1 s) and folder check, codes `READ_ONLY`/`READ_ONLY_NOT_APPLIED`, `?folder=` query and config `folder` (§ 6.1, § 6.4), installer `--folder` and exit 4 (§ 9), `open_session(folder=)` and list-form freeze step (§ 10), INSTRUCTIONS sentences (§ 3.2); REQ-CORE-050..055; REQ-CORE-010/029/038/039/040 amended; TASK-CORE-026..030 |
| 0.4.1 | 2026-11-11 | Consistency r1: fail closed on upload-pending docs under a folder limit; oversized-bulk and verify-failure hints; restore-activate failure reported; freeze step needs `results[0].ok`; blank config folder is a config error; verify limit worded "10 tries, 1 s apart" |
| 0.4.2 | 2026-11-11 | § 5.13 `ensure_editable` step 2: a raising `findFileById` falls back to `df`; § 6.1 / REQ-CORE-053: config `folder` error keeps the `invalid config <path>: ` prefix |
| 0.4.3 | 2026-11-11 | Validation r1: guard interface is module functions (`guard.ensure_editable`, `guard.check_folder`, `guard.fresh_file`, `guard.norm_folder`); `Ctx` methods removed |
| 0.5.0 | 2026-11-12 | Re-lock registry: `guard.UNLOCKED` (data file id → name) added by `ensure_editable` when Make Editable is issued, removed when `set_read_only` observes locked or `close` drops the document (§ 5.13); folder-limit scope names `datafile_upload` and the `close` re-lock; REQ-CORE-056; TASK-CORE-031; `Axis` sketch-line form `{"sketch", "line"}` and `Profile` multi-sketch entry list (§ 5.4; REQ-CORE-057, REQ-CORE-058; REQ-CORE-018, REQ-CORE-019 amended; TASK-CORE-032) |
| 0.6.0 | 2026-11-12 | Installer runs pip from `sys.executable` with `--python <fusion python>` (pip 22.3+, exit 1 with message if missing/older), builds `lib/` in `lib.tmp-<pid>` and replaces `lib/` only on success; `--uninstall` also removes `lib.tmp-*` (§ 9 step 2); REQ-CORE-038 amended; TASK-CORE-033 |
| 0.6.1 | 2026-11-12 | Installer repoints a stale `De-Fusion` symlink (REQ-RELEASE-006): requirements install bullet, § 9 step 3 (C1 r2) |
