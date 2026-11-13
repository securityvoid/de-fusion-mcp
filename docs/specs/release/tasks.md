# tasks.md — docs/specs/release/

Legend: `- [ ]` open, `- [x]` done. Run every command from the repository root. Design references are to `docs/specs/release/design.md`.

## Phase 1 — License and renderer
- [x] **TASK-RELEASE-001** [REQ-RELEASE-001, REQ-RELEASE-008]: LICENSE, README line, license test
  - Create MIT `LICENSE` ("Copyright (c) 2026 Doug Logan"); add one line `Licensed under the MIT License.` to `README.md`.
    Create `tests/test_release.py` with the license/README test.
  - _Output_: `LICENSE`, `README.md`, `tests/test_release.py`
  - _Reuse_: none; no license file or release test exists.
  - _Verify_: `pytest -q tests/test_release.py -k license`; name the tests so they contain `license`
- [x] **TASK-RELEASE-002** [REQ-RELEASE-003, REQ-RELEASE-005, REQ-RELEASE-007, REQ-RELEASE-008]: Template, renderer, tests
  - Create `packaging/de-fusion-mcp.rb.tmpl` (§ 4) and `scripts/render_cask.py`; create `tests/test_render_cask.py`; add the template-stanza test (`depends_on :macos`, `must_succeed: true`) to `tests/test_release.py`.
  - _Output_: `packaging/de-fusion-mcp.rb.tmpl`, `scripts/render_cask.py`, `tests/test_render_cask.py`, `tests/test_release.py`
  - _Reuse_: none; stdlib only.
  - _Verify_: `pytest -q tests/test_render_cask.py tests/test_release.py -k "render or template" && python3 scripts/render_cask.py 0.1.0 $(printf 'a%.0s' $(seq 64)) | grep -c 'v#{version}'`; name the tests so they contain `render` / `template`

## Phase 2 — Workflow and installer
- [x] **TASK-RELEASE-003** [REQ-RELEASE-002, REQ-RELEASE-004, REQ-RELEASE-008, NFR-RELEASE-001]: Release workflow
  - Create `.github/workflows/release.yml` per § 3 (tap checkout via `actions/checkout@v4`, render before release create); add the workflow text tests of § 6 to `tests/test_release.py`.
  - _Output_: `.github/workflows/release.yml`, `tests/test_release.py`
  - _Reuse_: none.
  - _Verify_: `pytest -q tests/test_release.py -k workflow`; name the tests so they contain `workflow`
- [x] **TASK-RELEASE-004** [REQ-RELEASE-006]: Repoint stale De-Fusion link
  - Implement § 5 in `install.py`; append the four tests of § 6 to `tests/test_install.py`.
  - _Output_: `install.py`, `tests/test_install.py`
  - _Reuse_: `install.is_our_link`, `make_link`, `remove_link`, `conflict`; fixture `env`.
  - _Verify_: `pytest -q tests/test_install.py`

## Phase 3 — Validation
- [ ] **TASK-RELEASE-008** [REQ-RELEASE-002]: One-time external setup (operator task; only the operator can do these)
  - (1) Make `securityvoid/de-fusion-mcp` public. (2) `securityvoid/homebrew-tap` already exists, is public and has `main` with `Casks/` (verified); nothing to create. (3) Create a fine-grained PAT (Contents read/write, tap only) and add it as repository secret `HOMEBREW_TAP_TOKEN` in `securityvoid/de-fusion-mcp`.
  - _Output_: none
  - _Reuse_: none.
  - _Verify_: `gh repo view securityvoid/de-fusion-mcp --json visibility` and `gh secret list -R securityvoid/de-fusion-mcp` list the repo as public and the secret
- [ ] **TASK-RELEASE-005** [REQ-RELEASE-001..008]: Full suite
  - Run the whole suite; do the post-merge smoke of § 6 (human, listed for the operator; depends on TASK-RELEASE-008).
  - _Output_: none
  - _Reuse_: none.
  - _Verify_: `pytest -q`
