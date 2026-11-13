"""Install De-Fusion into Autodesk Fusion (design § 9).

Run with any Python >= 3.10 outside Fusion:
    python install.py [--uninstall] [--fusion-python PATH] [--folder PATH]
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
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
WINDOWS = sys.platform == "win32"
EXIT_CODES = ("Exit codes: 0 success, 1 pip failed or missing/older than 22.3, 2 Fusion Python not found, 3 link conflict, "
              "4 invalid --folder.")


def norm(path):  # defusion.guard.norm (design § 5.13); install.py does not import defusion
    return [s for s in path.strip().strip("/").split("/") if s]


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


def is_stale_link(link):
    return link.is_symlink() and Path(os.readlink(link)).name == "De-Fusion"


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


def top_level_config(path):
    """Top-level ``port``, ``token`` and ``folder`` of config.toml (lines before the first table).

    ``folder`` is the normalised path, or ``(None, error)`` when the config value is invalid (§ 6.1)."""
    port, token, folder = 8642, None, (None, None)
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("["):
            break
        m = re.match(r'\s*port\s*=\s*(\d+)', line)
        if m:
            port = int(m.group(1))
        m = re.match(r'\s*token\s*=\s*"([^"]*)"', line)
        if m and m.group(1).strip():
            token = m.group(1)
        m = re.match(r'\s*folder\s*=\s*(?:"([^"]*)"|([^#]*?))\s*(?:#.*)?$', line)
        if m:
            value = m.group(1) if m.group(1) is not None else m.group(2)
            folder = ("/".join(norm(value)), None) if m.group(1) is not None and norm(value) else (
                None, "config folder '%s': needs a project-qualified path like Project/Folder" % value)
    return port, token, folder


def clear_tmp():
    """Remove every installer build dir (lib.tmp-*); errors are ignored."""
    for d in (ROOT / "De-Fusion").glob("lib.tmp-*"):
        try:
            shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def uninstall():
    link = link_path()
    if link.is_symlink() or link.exists():
        if not is_our_link(link):
            return conflict(link)
        remove_link(link)
    shutil.rmtree(ROOT / "De-Fusion" / "lib", ignore_errors=True)
    clear_tmp()
    print("Removed the De-Fusion link, lib/ and lib.tmp-*; kept %s" % (config_dir() / "config.toml"))
    return 0


def install(fusion_python, folder=None):
    python, searched = find_fusion_python(fusion_python)
    if not python or not Path(python).exists():
        print("Fusion Python not found. Searched:\n  " + "\n  ".join(searched) + "\nUse --fusion-python PATH.", file=sys.stderr)
        return 2
    code, out = run_pip([sys.executable, "-m", "pip", "--version"])
    m = re.match(r"pip (\d+)\.(\d+)", out) if code == 0 else None
    if not m or (int(m.group(1)), int(m.group(2))) < (22, 3):
        print("pip 22.3 or newer is required to install into Fusion's Python (found %s); run python -m pip "
              "install --upgrade pip, or rerun install.py with a Python that has pip"
              % ("%s.%s" % m.groups() if m else "none"), file=sys.stderr)
        return 1
    lib = ROOT / "De-Fusion" / "lib"
    tmp = lib.with_name("lib.tmp-%d" % os.getpid())
    clear_tmp()
    code, output = run_pip([sys.executable, "-m", "pip", "--python", python, "install", "--upgrade",
                            "--no-compile", "--target", str(tmp), "-r", str(ROOT / "requirements.lock")])
    if code != 0:
        clear_tmp()
        print(output, file=sys.stderr)
        return 1
    try:
        if lib.exists():
            shutil.rmtree(lib)
    except OSError:
        clear_tmp()
        print("close Fusion and rerun install.py", file=sys.stderr)
        return 1
    try:
        os.rename(tmp, lib)
    except OSError:
        clear_tmp()
        print("rerun install.py", file=sys.stderr)
        return 1
    link = link_path()
    if link.is_symlink() or link.exists():
        if not is_our_link(link):
            if not is_stale_link(link):
                return conflict(link)
            remove_link(link)
            make_link(link)
    else:
        make_link(link)
    cfg = config_dir() / "config.toml"
    if not cfg.exists():
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(default_toml(), encoding="utf-8")
    port, token, (cfg_folder, folder_error) = top_level_config(cfg)
    if folder is None:
        folder = cfg_folder
        if folder_error:
            print("%s: %s; the url below has no ?folder=" % (cfg, folder_error), file=sys.stderr)
    url = "http://127.0.0.1:%d/mcp" % port
    if folder:
        url += "?folder=" + quote("/".join(norm(folder)), safe="")
    entry = {"type": "http", "url": url}
    if token:
        entry["headers"] = {"Authorization": "Bearer " + token}
    print("Add to your MCP client config:")
    print(json.dumps({"mcpServers": {"de-fusion": entry}}, indent=2))
    print("Append %sprofile=<items> to the url to select a profile." % ("&" if folder else "?"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="install.py", description="Install De-Fusion into Autodesk Fusion.",
                                 epilog=EXIT_CODES, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--uninstall", action="store_true", help="remove the link, lib/ and lib.tmp-*; keep config.toml")
    ap.add_argument("--fusion-python", metavar="PATH", help="Fusion's Python instead of the newest discovered one")
    ap.add_argument("--folder", metavar="PATH",
                    help="print the url with ?folder=PATH (a folder limit like Project/Folder); "
                         "overrides config folder, does not write config.toml")
    args = ap.parse_args(argv)
    if args.folder is not None and not norm(args.folder):
        print("--folder needs a project-qualified path like Project/Folder", file=sys.stderr)
        return 4
    return uninstall() if args.uninstall else install(args.fusion_python, args.folder)


if __name__ == "__main__":
    sys.exit(main())
