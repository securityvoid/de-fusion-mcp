# changelog.md — docs/specs/release/

| Version | Date | Change |
|---|---|---|
| 0.1.0 | 2026-11-12 | Initial spec: REQ-RELEASE-001..008, NFR-RELEASE-001, TASK-RELEASE-001..005 |
| 0.1.1 | 2026-11-12 | C1-r1-1..5, C2-r1-1..4: link wording, placeholder names, url test, drop macOS minimum, NFR cite, tap checkout action, `must_succeed`, render before create, release-exists guard |
| 0.1.2 | 2026-11-12 | C2-r1-4 resume removed, C2-r2-1 |
| 0.1.3 | 2026-11-12 | C1-r3-1, C1-r3-2, C2-r3-1, C2-r3-2 |
| 0.1.4 | 2026-11-12 | C4-r1-1..3: bot git identity before commit, `$GITHUB_ENV`, test names match `-k` |
| 0.1.5 | 2026-11-12 | C4-r2-1..3: operator setup task TASK-RELEASE-008, `git add` path and no-change exit, install() replacement block |
| 0.1.6 | 2026-11-13 | Design §5 `is_stale_link`: drop `not WINDOWS and` (is_symlink is False for junctions) (V1-r2-1, V3-r1-2) |
| 0.2.0 | 2026-11-14 | REQ-RELEASE-009 (README brew install), REQ-RELEASE-005 desc and `postflight_steps`/`uninstall_preflight_steps`, TASK-RELEASE-009..011 |
| 0.2.2 | 2026-11-14 | C1-r1-1, C2-r1-1: `uninstall_preflight_steps` `must_succeed: false`; C1-r1-2: design §1 Files list |
| 0.2.3 | 2026-11-14 | C4-r1-1: test_readme.py asserts on `section("Install")`, order by `str.index`; C4-r1-2: REQ-RELEASE-009 keeps manual-route prerequisite, flags, exit codes, restart paragraph |
| 0.2.4 | 2026-11-14 | V3-r1-1, V3-r1-2: design §4 steps DSL and TASK-RELEASE-011 no longer repeat REQ-RELEASE-005 / §6 |
| 0.3.0 | 2026-11-14 | REQ-RELEASE-005: `installer script:` / `uninstall script:` replace `*_steps` (sandboxed without real home, so `install.py` cannot run); TASK-RELEASE-012 replaces TASK-RELEASE-011 |
| 0.3.1 | 2026-11-14 | C1-r1-1: TASK-RELEASE-002 template-test parenthetical points to § 6 |
| 0.3.2 | 2026-11-14 | C2-r1-1, C2-r1-2: installer stanza keeps default `must_succeed`, no `sudo` (acceptance and § 6) |
| 0.3.3 | 2026-11-14 | spec-lint out-of-scope (REQ-CLI-055/056): Operator ask sentence and `## Out of Scope` section removed |
| 0.3.4 | 2026-11-14 | C4-r1-1..3: TASK-RELEASE-012 in Phase 3, § 6 stanza delimiter, sandbox rationale once (design § 4) |
| 0.4.0 | 2026-11-14 | REQ-RELEASE-010 (`install.py --copy`: Fusion lists a symlinked add-in twice), REQ-RELEASE-005 cask passes `--copy`, TASK-RELEASE-013..014 |
| 0.4.1 | 2026-11-14 | C1-r1-1..5, C2-r1-1..3: marker written first (partial copy is replaced next run), `OSError` guard, hostile-entry and README tests, REQ-010 in § 1 and TASK-RELEASE-005 |
| 0.4.2 | 2026-11-14 | C1-r2-1, C2-r2-1: `remove_entry` deletes the marker last; TASK-RELEASE-013 verify runs the readme test |
| 0.4.3 | 2026-11-14 | C4-r1-1, C4-r1-3: `install()` signature, § 6 copy tests point to the acceptance |
