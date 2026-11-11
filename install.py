"""Install De-Fusion into Autodesk Fusion (design § 9).

Run with any Python >= 3.10 outside Fusion:
    python install.py [--uninstall] [--fusion-python PATH]
"""
import argparse
import ast
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WINDOWS = sys.platform == "win32"
EXIT_CODES = "Exit codes: 0 success, 1 pip failed, 2 Fusion Python not found, 3 link conflict."


def _env(name):
    return os.environ.get(name) or ""


def home():
    return Path(_env("USERPROFILE") if WINDOWS else _env("HOME") or Path.home())


def search_patterns():
    if WINDOWS:
        return [str(Path(_env("LOCALAPPDATA")) / "Autodesk" / "webdeploy" / "production" / "*" / "Python" / "python.exe")]
    return [str(home() / "Library/Application Support/Autodesk/webdeploy/production/*/Autodesk Fusion.app"
                "/Contents/Frameworks/Python.framework/Versions/Current/bin/python3")]


def build_dir(python):
    return Path(python).parents[1] if WINDOWS else Path(python).parents[7]


def find_fusion_python(explicit=None):
    """Return (python path | None, patterns searched)."""
    patterns = search_patterns()
    if explicit:
        return explicit, [explicit]
    found = [p for pat in patterns for p in glob.glob(pat)]
    if not found:
        return None, patterns
    return max(found, key=lambda p: build_dir(p).stat().st_mtime), patterns


def link_path():
    if WINDOWS:
        base = Path(_env("APPDATA")) / "Autodesk" / "Autodesk Fusion 360" / "API" / "AddIns"
    else:
        base = home() / "Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns"
    return base / "De-Fusion"


def config_dir():
    env = _env("DEFUSION_CONFIG_DIR")
    return Path(env) if env else home() / ".defusion"


def default_toml():
    """DEFAULT_TOML from defusion/config.py, read without importing (its imports need Fusion's packages)."""
    tree = ast.parse((ROOT / "De-Fusion" / "defusion" / "config.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "DEFAULT_TOML" for t in node.targets):
            return ast.literal_eval(node.value)
    raise RuntimeError("DEFAULT_TOML not found in config.py")


def run_pip(cmd):
    """Run pip; returns (exit code, combined output). Tests replace this function."""
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return p.returncode, p.stdout


def is_our_link(link):
    try:
        return (WINDOWS or link.is_symlink()) and os.path.samefile(link, ROOT / "De-Fusion")
    except OSError:
        return False


def make_link(link):
    target = ROOT / "De-Fusion"
    link.parent.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, stdout=subprocess.DEVNULL)
    else:
        os.symlink(target, link)


def remove_link(link):
    if WINDOWS:
        os.rmdir(link)
    else:
        link.unlink()


def conflict(link):
    print("%s exists and is not the De-Fusion link; remove it and rerun install.py" % link, file=sys.stderr)
    return 3


def port_and_token(path):
    """Top-level ``port`` and ``token`` of config.toml (lines before the first table)."""
    port, token = 8642, None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("["):
            break
        m = re.match(r'\s*port\s*=\s*(\d+)', line)
        if m:
            port = int(m.group(1))
        m = re.match(r'\s*token\s*=\s*"([^"]*)"', line)
        if m and m.group(1).strip():
            token = m.group(1)
    return port, token


def uninstall():
    link = link_path()
    if link.is_symlink() or link.exists():
        if not is_our_link(link):
            return conflict(link)
        remove_link(link)
    shutil.rmtree(ROOT / "De-Fusion" / "lib", ignore_errors=True)
    print("Removed the De-Fusion link and lib/; kept %s" % (config_dir() / "config.toml"))
    return 0


def install(fusion_python):
    python, searched = find_fusion_python(fusion_python)
    if not python or not Path(python).exists():
        print("Fusion Python not found. Searched:\n  " + "\n  ".join(searched) + "\nUse --fusion-python PATH.", file=sys.stderr)
        return 2
    lib = ROOT / "De-Fusion" / "lib"
    shutil.rmtree(lib, ignore_errors=True)
    code, output = run_pip([python, "-m", "pip", "install", "--upgrade", "--no-compile",
                            "--target", str(lib), "-r", str(ROOT / "requirements.lock")])
    if code != 0:
        print(output, file=sys.stderr)
        return 1
    link = link_path()
    if link.is_symlink() or link.exists():
        if not is_our_link(link):
            return conflict(link)
    else:
        make_link(link)
    cfg = config_dir() / "config.toml"
    if not cfg.exists():
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(default_toml(), encoding="utf-8")
    port, token = port_and_token(cfg)
    entry = {"type": "http", "url": "http://127.0.0.1:%d/mcp" % port}
    if token:
        entry["headers"] = {"Authorization": "Bearer " + token}
    print("Add to your MCP client config:")
    print(json.dumps({"mcpServers": {"de-fusion": entry}}, indent=2))
    print("Append ?profile=<items> to the url to select a profile.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="install.py", description="Install De-Fusion into Autodesk Fusion.",
                                 epilog=EXIT_CODES, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--uninstall", action="store_true", help="remove the link and lib/; keep config.toml")
    ap.add_argument("--fusion-python", metavar="PATH", help="Fusion's Python instead of the newest discovered one")
    args = ap.parse_args(argv)
    return uninstall() if args.uninstall else install(args.fusion_python)


if __name__ == "__main__":
    sys.exit(main())
