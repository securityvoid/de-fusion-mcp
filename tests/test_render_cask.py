import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SHA = "a" * 64
URL_TAIL = "v#{version}/de-fusion-mcp-#{version}.tar.gz"


def render(*args):
    return subprocess.run([sys.executable, str(ROOT / "scripts/render_cask.py"), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def test_render_substitutes_version_and_sha():
    p = render("1.2.3", SHA)
    assert p.returncode == 0
    assert 'version "1.2.3"' in p.stdout
    assert 'sha256 "%s"' % SHA in p.stdout
    assert "@VERSION@" not in p.stdout and "@SHA256@" not in p.stdout


def test_render_keeps_url_line_with_ruby_interpolation():
    out = render("1.2.3", SHA).stdout
    assert any(line.strip().startswith("url ") and line.rstrip().endswith(URL_TAIL + '"') for line in out.splitlines())


def test_render_output_is_stable():
    assert render("1.2.3", SHA).stdout == render("1.2.3", SHA).stdout


@pytest.mark.parametrize("args", [[], ["1.2.3"], ["1.2.3", SHA, "x"], ["1.2", SHA], ["v1.2.3", SHA],
                                  ["1.2.3", "a" * 63], ["1.2.3", "A" * 64], ["1.2.3", "g" * 64]])
def test_render_invalid_input_exits_2_with_empty_stdout(args):
    p = render(*args)
    assert p.returncode == 2
    assert p.stdout == ""
    assert "usage: render_cask.py <version> <sha256>" in p.stderr
