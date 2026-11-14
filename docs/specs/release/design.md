# design.md — docs/specs/release/
> De-Fusion MCP — release workflow, cask, license

## 1. Overview
Tag `v0.1.0` -> `release.yml` -> tarball -> `render_cask.py` -> GitHub release -> push `Casks/de-fusion-mcp.rb` to
`securityvoid/homebrew-tap` -> `brew install --cask securityvoid/tap/de-fusion-mcp`.

Files: `LICENSE` (REQ-001), `README.md` (REQ-001, -009), `tests/test_readme.py` (REQ-009), `.github/workflows/release.yml` (REQ-002, -004, NFR-RELEASE-001),
`scripts/render_cask.py` (REQ-003), `packaging/de-fusion-mcp.rb.tmpl` (REQ-003, -005), `install.py` (REQ-006, -010),
`tests/test_render_cask.py` (REQ-007), `tests/test_release.py` (REQ-008), `tests/test_install.py` (REQ-006, -010, append).

## 2. Reuse
- `tests/test_install.py` fixture `env` (temp HOME, patched `install.ROOT`) for the repoint test.
- `install.is_our_link`, `make_link`, `remove_link`, `conflict` (unchanged).
- Test style: plain text assertions; `tomllib` is the only parser in the repo, no YAML parser exists and none is added.

## 3. `release.yml`
Job `release` on `ubuntu-latest`, `permissions: contents: write`, `on: push: tags: ["v*"]`. Steps:
Only the release-create and tap steps receive tokens (NFR-RELEASE-001): `GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}` on the `gh release create` step only, `HOMEBREW_TAP_TOKEN` on the tap checkout step only.
1. `actions/checkout@v4`.
2. Env `TAG=${GITHUB_REF_NAME}`, `VER=${TAG#v}`; `git archive --format=tar.gz --prefix=de-fusion-mcp-$VER/ -o de-fusion-mcp-$VER.tar.gz $TAG`; `SHA=$(sha256sum de-fusion-mcp-$VER.tar.gz | cut -d' ' -f1)`; write TAG, VER, SHA to `$GITHUB_ENV`.
3. `python3 scripts/render_cask.py $VER $SHA > cask.rb` (before any publish, so an invalid tag fails first).
4. `gh release create $TAG de-fusion-mcp-$VER.tar.gz --title $TAG --generate-notes`.
   Order: build tarball -> sha -> render -> create release -> update tap.
5. `actions/checkout@v4` with `repository: securityvoid/homebrew-tap`, `token: ${{ secrets.HOMEBREW_TAP_TOKEN }}`, `path: tap`; then
   `mkdir -p tap/Casks`; `cp cask.rb tap/Casks/de-fusion-mcp.rb`; in `tap`: `git config user.name "github-actions[bot]"`, `git config user.email "41898282+github-actions[bot]@users.noreply.github.com"`, `git add Casks/de-fusion-mcp.rb`; if `git diff --cached --quiet` succeeds, end the step with exit 0 (no commit, no push);
   else commit `de-fusion-mcp $VER` and `git push origin HEAD:main`. No git clone URL carries a token. If `gh release create` or any later step fails: `gh release delete <tag> --yes`, then re-run the job; if the tag was moved: delete the release, then `git push -f origin <tag>` (starts a new run).
External setup (human, one-time; TASK-RELEASE-008): make the source repo public; `securityvoid/homebrew-tap` already exists; add secret `HOMEBREW_TAP_TOKEN`.

## 4. Template and renderer
Template placeholders `@VERSION@`, `@SHA256@` (Ruby `#{version}` is untouched). Stanzas:
```ruby
cask "de-fusion-mcp" do
  version "@VERSION@"
  sha256 "@SHA256@"
  url "https://github.com/securityvoid/de-fusion-mcp/releases/download/v#{version}/de-fusion-mcp-#{version}.tar.gz"
  name "De-Fusion MCP"
  desc "Model Context Protocol (MCP) server add-in for Autodesk Fusion CAD"
  homepage "https://github.com/securityvoid/de-fusion-mcp"
  depends_on :macos
  depends_on formula: "python@3.13"
  installer script: {
    executable: "#{HOMEBREW_PREFIX}/opt/python@3.13/bin/python3.13",
    args:       ["#{staged_path}/de-fusion-mcp-#{version}/install.py", "--copy"],
  }
  uninstall script: {
    executable:   "#{HOMEBREW_PREFIX}/opt/python@3.13/bin/python3.13",
    args:         ["#{staged_path}/de-fusion-mcp-#{version}/install.py", "--uninstall"],
    must_succeed: false,
  }
  caveats <<~EOS
    In Fusion, open Utilities > Add-Ins and tick "Run on Startup" for De-Fusion (brew cannot tick it).
    Use the "De-Fusion Setup" button to write MCP client configs.
  EOS
end
```
Why `installer script:` (Homebrew 7 Cask Cookbook, "Cask artifact trust and sandboxing"): `*_steps` blocks run sandboxed with no real-home access and `postflight` blocks are deprecated, while `installer script:` runs unsandboxed, as `install.py` needs (Fusion's Python and AddIns folder under the real home, `~/.defusion`, pip). `installer` requires an `uninstall` stanza, which the `uninstall script:` provides.

Python decision: Homebrew `python@3.13` (declared dependency) because `/usr/bin/python3` is the Xcode shim with pip below the 22.3 that
`install.py` needs; `install.py` finds Fusion's Python itself and runs pip from the running Python. Cask tarball top directory is
`de-fusion-mcp-<ver>/`, hence the staged path.

`render_cask.py`: `sys.argv` checks per REQ-003 (regexes), reads the template next to the repo (`Path(__file__).parent.parent/"packaging"`),
`str.replace` both placeholders, `sys.stdout.write`. Exit 2 with `usage: render_cask.py <version> <sha256>` on stderr.

## 5. `install.py` upgrade finding
Current code: `is_our_link` is true only when the link is a symlink resolving (`samefile`) to the current `ROOT/De-Fusion`; otherwise
`install()` returns `conflict` (exit 3). So a link left by an older cask version (or dangling) is a conflict: change needed.
Change: add
```python
def is_stale_link(link):
    return link.is_symlink() and Path(os.readlink(link)).name == "De-Fusion"
```
and in `install()` replace `if not is_our_link(link): return conflict(link)` (inside `if link.is_symlink() or link.exists():`; its `else: make_link(link)` stays) by
```python
if not is_our_link(link):
    if not is_stale_link(link):
        return conflict(link)
    remove_link(link)
    make_link(link)
```
`--uninstall` keeps `is_our_link`/`conflict` (never removes a link it does not own). `readlink` is used, not `resolve`, so dangling links work.

## 5a. `install.py --copy`
Fusion lists a symlinked add-in twice (its AddIns path and the resolved Caskroom path), so the cask installs a copy. Add to `install.py`:
```python
MARKER = ".de-fusion-copy"

def is_our_copy(link):
    return link.is_dir() and not link.is_symlink() and (link / MARKER).is_file()

def remove_entry(link):
    if not is_our_copy(link):
        return remove_link(link)
    for child in link.iterdir():  # the marker goes last, so a failed removal leaves a marked entry
        if child.name != MARKER:
            shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
    (link / MARKER).unlink()
    link.rmdir()

def make_copy(link):
    link.mkdir(parents=True, exist_ok=True)
    (link / MARKER).write_text("")
    shutil.copytree(ROOT / "De-Fusion", link, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("lib.tmp-*", "__pycache__"))
```
`main` gets `--copy` (`store_true`, help "install a copy of De-Fusion instead of a link") passed to `install(fusion_python, folder=None, copy=False)`. In `install()`, where the link is placed: when `copy`, an existing entry that is `is_our_copy`, `is_our_link` or `is_stale_link` is removed with `remove_entry`, any other existing entry returns `conflict(link)`, then `make_copy(link)` (the marker is written before the copy, so a partial copy is a marked entry the next run replaces); `remove_entry` and `make_copy` run in `try/except OSError` that prints `close Fusion and rerun install.py` to stderr and returns 1, as the `rmtree(lib)` step does, and so does the `remove_entry` call in `uninstall()`; without `copy` the code is unchanged. In `uninstall()` the test `is_our_link(link)` becomes `is_our_link(link) or is_our_copy(link)` and `remove_link(link)` becomes `remove_entry(link)`. The copy is made after `lib/` is built, so it contains `lib/`.

## 6. Tests
- `test_render_cask.py`: run `scripts/render_cask.py` via `subprocess` with `sys.executable`; assertions per REQ-RELEASE-007 (url line keeps `v#{version}/de-fusion-mcp-#{version}.tar.gz`).
- `test_release.py`: read text of `release.yml`, template, `LICENSE`, `README.md`; assertions per REQ-RELEASE-008. `release.yml`: no `x-access-token:` or `@github.com/`,
  secret passed via `token:`, `render_cask.py` precedes `gh release create`, every step running `gh ` sets `GH_TOKEN`, `HOMEBREW_TAP_TOKEN` appears only in the tap checkout step, `GITHUB_ENV` appears, both `git config` lines appear before `git commit`, `git add Casks/de-fusion-mcp.rb` precedes `git commit`. Template: assertions per REQ-RELEASE-005 acceptance; a stanza is the text from its `installer script:` / `uninstall script:` line to the next line starting `  }`. Include `ruby -c` on the rendered
  cask only when `shutil.which("ruby")`.
- `test_install.py` copy additions (names contain `copy`): one test per case of the REQ-RELEASE-010 acceptance, plus removal raising `OSError` after deleting some children (not a stub that raises first).
- `test_readme.py`: `section("Install")` text (existing helper) contains `--copy` and contains `brew install --cask securityvoid/tap/de-fusion-mcp` before `python install.py` (`str.index` of the brew line < that of `python install.py`), `brew uninstall --cask de-fusion-mcp` and `Run on Startup` (REQ-RELEASE-009).
- `test_install.py` additions: stale live link, stale dangling link, real directory (exit 3, untouched), link to `other` (exit 3).
- Post-merge smoke (human): tag `v0.1.0`; confirm release asset, tap commit, `brew install --cask securityvoid/tap/de-fusion-mcp`.
