# requirements.md — docs/specs/release/
> De-Fusion MCP — MIT license and tag-driven Homebrew cask release

## Overview
Pushing a tag `v<ver>` builds a source tarball, publishes a GitHub release and updates the
`de-fusion-mcp` cask in `securityvoid/homebrew-tap`. `brew install --cask` then stages the tarball and
runs `install.py`. Section references (§) point to `docs/specs/release/design.md`.

## Operator ask
2026-11-12: Homebrew cask via a release workflow (no GoReleaser), MIT license, source repo public,
first tag `v0.1.0`, upgrades repoint Fusion's `De-Fusion` link. Out of scope: Windows, signing and
notarization, other taps, autostart automation.

## Dependencies
- **DEP-RELEASE-001**: `install.py` (`--uninstall`, Fusion Python discovery, pip >= 22.3 probe, AddIns link),
  specified in `docs/specs/core/` and `docs/specs/setup/`.

## Functional Requirements
- **REQ-RELEASE-001**: The repo shall contain an MIT `LICENSE` at the root with the line
  `Copyright (c) 2026 Doug Logan`, and `README.md` shall name the license in one line.
  - _Acceptance_: `LICENSE` starts with `MIT License`, contains that copyright line; `README.md` has a line containing `MIT`.
- **REQ-RELEASE-002**: `.github/workflows/release.yml` shall trigger only on pushed tags matching `v*`,
  with `permissions: contents: write`, and shall build `de-fusion-mcp-<ver>.tar.gz` (`<ver>` = tag without
  the leading `v`) using `git archive --prefix=de-fusion-mcp-<ver>/ <tag>`, compute its sha256, and create the GitHub
  release with that file via `gh release create` and the built-in `GITHUB_TOKEN`.
  - _Acceptance_: the workflow text has `tags:` with `v*` and no `branches:` trigger, the `git archive --prefix` command,
    `sha256`, and `gh release create`; no personal token is used for the release step.
- **REQ-RELEASE-003**: `scripts/render_cask.py <version> <sha256>` shall print the cask to stdout from
  `packaging/de-fusion-mcp.rb.tmpl`, replacing `@VERSION@` and `@SHA256@`, using only the standard library.
  It shall exit 2 with a usage message on stderr for a wrong argument count, a version not matching
  `^\d+\.\d+\.\d+$`, or a sha256 not matching `^[0-9a-f]{64}$`.
  - _Acceptance_: valid args print a cask containing the version and sha256 and neither `@VERSION@` nor `@SHA256@`; the same args give identical output twice; each invalid input exits 2 with empty stdout.
- **REQ-RELEASE-004**: The workflow shall render `Casks/de-fusion-mcp.rb` with `scripts/render_cask.py`, check out
  `securityvoid/homebrew-tap` with `actions/checkout@v4` using repository secret `HOMEBREW_TAP_TOKEN`, write the file, and commit and push to the
  tap's `main`; when the file is unchanged it shall not commit.
  - _Acceptance_: the workflow text references `render_cask.py`, `securityvoid/homebrew-tap`, `secrets.HOMEBREW_TAP_TOKEN`,
    `Casks/de-fusion-mcp.rb` and `git push`, `token: ${{ secrets.HOMEBREW_TAP_TOKEN }}`, and no `x-access-token:` or `@github.com/`; the tap token is not used in the release step; it renders before `gh release create`.
- **REQ-RELEASE-005**: Cask `de-fusion-mcp` shall be macOS only (no version minimum), with url
  `https://github.com/securityvoid/de-fusion-mcp/releases/download/v#{version}/de-fusion-mcp-#{version}.tar.gz`, `sha256`, `name`, `desc`, `homepage`,
  `depends_on formula: "python@3.13"`, a `postflight` that runs `install.py` from the staged path with that Python (`system_command` with `must_succeed: true`), an `uninstall_preflight` that runs
  `install.py --uninstall` (keeping `~/.defusion/config.toml`), and `caveats` telling the user to tick "Run on Startup" for De-Fusion
  in Fusion (Utilities > Add-Ins) and to use the "De-Fusion Setup" button to write MCP client configs.
  - _Acceptance_: the rendered cask has every stanza above; `ruby -c` passes when `ruby` exists.
- **REQ-RELEASE-006**: `install.py` shall repoint an existing AddIns `De-Fusion` symlink that points to a different checkout
  (stale cask version, including a dangling target) instead of exiting 3; any other existing entry (a real directory, file, or
  symlink whose target's final component is not `De-Fusion`) shall still exit 3 untouched. `--uninstall` behaviour is unchanged.
  - _Acceptance_: a link to another dir named `De-Fusion` (live or dangling) ends pointing at the current `ROOT/De-Fusion`
    with exit 0; a real directory and a link to a dir named `other` leave the entry unchanged and exit 3.
- **REQ-RELEASE-007**: `tests/test_render_cask.py` shall cover REQ-RELEASE-003 (substitution of version, sha256 and the url (the url line stays
  `...v#{version}/de-fusion-mcp-#{version}.tar.gz`, only `@VERSION@`/`@SHA256@` replaced), no leftover
  `@VERSION@`/`@SHA256@`, stable output, invalid inputs), without network.
  - _Acceptance_: `pytest -q tests/test_render_cask.py` passes and fails if `@VERSION@` is left unreplaced.
- **REQ-RELEASE-008**: `tests/test_release.py` shall check, by plain text assertions (no YAML parser is in `requirements.lock`; no dependency is added), `release.yml`
  per REQ-RELEASE-002, -004 and NFR-RELEASE-001, the template per REQ-RELEASE-005, and `LICENSE`/README per REQ-RELEASE-001, without network.
  - _Acceptance_: `pytest -q tests/test_release.py` passes; removing `gh release create` from the workflow fails it.

## Non-Functional Requirements
- **NFR-RELEASE-001**: Users need no token to install; the release step uses only `GITHUB_TOKEN`; `HOMEBREW_TAP_TOKEN` is a
  fine-grained PAT with Contents read/write on the tap only.
  - _Acceptance_: no `brew` or `curl` step in the workflow or cask uses a credential; GH_TOKEN is set on the `gh release create` step only and HOMEBREW_TAP_TOKEN appears only in the tap checkout step.

## Out of Scope
Windows, signing and notarization, other taps, autostart automation (brew cannot tick Fusion's "Run on Startup"), GoReleaser,
a `[project]` table in `pyproject.toml` (none exists, so no `license` field).
