import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_license_is_mit_with_copyright():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License")
    assert "Copyright (c) 2026 Doug Logan" in text


def test_license_named_in_readme():
    lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    assert any("MIT" in line for line in lines)


def test_template_has_required_stanzas():
    text = (ROOT / "packaging" / "de-fusion-mcp.rb.tmpl").read_text(encoding="utf-8")
    assert ('url "https://github.com/securityvoid/de-fusion-mcp/releases/download/'
            'v#{version}/de-fusion-mcp-#{version}.tar.gz"') in text
    for stanza in ("sha256 ", "name ", "desc ", "homepage ", 'depends_on formula: "python@3.13"', "depends_on :macos"):
        assert stanza in text
    postflight = text.split("postflight do", 1)[1].split("\n  end", 1)[0]
    assert "install.py" in postflight and "must_succeed: true" in postflight
    preflight = text.split("uninstall_preflight do", 1)[1].split("\n  end", 1)[0]
    assert "--uninstall" in preflight
    caveats = text.split("caveats", 1)[1]
    assert "Run on Startup" in caveats and "De-Fusion Setup" in caveats


@pytest.mark.skipif(not shutil.which("ruby"), reason="ruby not installed")
def test_template_rendered_cask_passes_ruby_syntax_check(tmp_path):
    out = subprocess.run([sys.executable, str(ROOT / "scripts/render_cask.py"), "0.1.0", "a" * 64],
                         stdout=subprocess.PIPE, text=True, check=True).stdout
    cask = tmp_path / "de-fusion-mcp.rb"
    cask.write_text(out, encoding="utf-8")
    assert subprocess.run(["ruby", "-c", str(cask)], stdout=subprocess.DEVNULL).returncode == 0


WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def workflow_text():
    return WORKFLOW.read_text(encoding="utf-8")


def workflow_steps():
    return workflow_text().split("\n      - ")[1:]


def test_workflow_triggers_only_on_version_tags():
    text = workflow_text()
    assert "tags:" in text and '"v*"' in text
    assert "branches:" not in text
    assert "permissions:\n  contents: write" in text


def test_workflow_builds_tarball_and_creates_release():
    text = workflow_text()
    assert "git archive --format=tar.gz --prefix=de-fusion-mcp-$VER/" in text
    assert "sha256" in text
    assert "gh release create" in text
    assert "GITHUB_ENV" in text


def test_workflow_renders_before_release_create():
    text = workflow_text()
    assert text.index("render_cask.py") < text.index("gh release create")


def test_workflow_updates_tap_without_embedded_credentials():
    text = workflow_text()
    for needle in ("securityvoid/homebrew-tap",
                   "Casks/de-fusion-mcp.rb", "git push", "actions/checkout@v4",
                   "token: ${{ secrets.HOMEBREW_TAP_TOKEN }}"):
        assert needle in text
    assert "x-access-token:" not in text and "@github.com/" not in text


def test_workflow_tap_commit_order_and_skips_commit_when_unchanged():
    text = workflow_text()
    commit = text.index("git commit")
    assert text.index('git config user.name') < commit
    assert text.index('git config user.email') < commit
    assert text.index("git add Casks/de-fusion-mcp.rb") < commit
    diff = text.index("git diff --cached --quiet")
    assert diff < text.index("exit 0", diff) < commit


def test_workflow_tokens_are_scoped_to_their_steps():
    steps = workflow_steps()
    assert sum("HOMEBREW_TAP_TOKEN" in s for s in steps) == 1
    tap = next(s for s in steps if "HOMEBREW_TAP_TOKEN" in s)
    assert "repository: securityvoid/homebrew-tap" in tap
    gh_steps = [s for s in steps if "gh " in s]
    assert gh_steps and all("GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in s for s in gh_steps)
    assert sum("GH_TOKEN" in s for s in steps) == len(gh_steps)
    assert "curl" not in workflow_text() and "brew " not in workflow_text()
