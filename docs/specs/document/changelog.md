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
| 0.4.0 | 2026-11-11 | Breaking: 13 actions take `bulk` lists (`paths`/`files`/`documents`, `SaveAsEntry`/`RenameEntry`) and return `{results, ok, failed}`; singular `path`/`file`/`document`/`name` removed on them (§ 2.7). `edits_document` per action (§ 3), `ensure_editable` in save/advance/update/import-active, `save`/`save_as` lock with verification and `read_only` param (§ 6.4, § 6.5), `set_read_only` via core helper with `READ_ONLY_NOT_APPLIED` (§ 6.8), folder limit (§ 2.2, § 2.8; `save_as` folder default), live scenarios `bulk`, `save_locks`, `auto_unlock`, `folder_limit` (§ 10.2); REQ-DOC-023..027; REQ-DOC-011/014/015 acceptance amended; TASK-DOC-011..014 |
| 0.4.1 | 2026-11-11 | Consistency r1: `save` fails closed on upload pending and captures the data file before `doc.save`; removed-singular-name hints; worst-case latency note; extra folder/`preserve_read_only` negative tests; present-tense § 2.7 |
| 0.4.2 | 2026-11-11 | § 6.8 `set_read_only` step 2: a raising `findFileById` falls back to `df` (as core § 5.13) |
| 0.4.3 | 2026-11-11 | Validation r1: wording follows the core guard interface (`guard.*`); `_folder_path` wrapper removed |
| 0.5.0 | 2026-11-12 | `close` re-locks documents the core guard unlocked (`relocked`, `warnings`, timeout 120; § 7.1, REQ-DOC-028, REQ-DOC-019); new bulk action `datafile_upload` via `Folder.uploadFile` for f3d/f3z with transient open to lock (§ 6.13, REQ-DOC-029); `import` keeps `.f3d` only and rejects f3z with a pointer to upload + open (§ 6.11, § 9.1, REQ-DOC-017); REQ-DOC-023, REQ-DOC-025, REQ-DOC-027 amended; fourteen list actions; live scenarios `datafile_upload`, `datafile_upload_f3z`, extended `auto_unlock`/`folder_limit`/`import`, live notes § 10.3; TASK-DOC-015..018 |
| 0.5.1 | 2026-11-12 | `datafile_upload` locks and reports every file a multi-document .f3z creates (`created`, root as the entry; REQ-DOC-029, § 6.13, TASK-DOC-016) |
| 0.5.2 | 2026-11-12 | datafile_upload waits up to 30 s for cloud ids after an upload (V1-r1-3) |
