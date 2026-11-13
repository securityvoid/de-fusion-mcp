"""Print the Homebrew cask for a release: render_cask.py <version> <sha256>."""
import re
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "packaging" / "de-fusion-mcp.rb.tmpl"


def main(argv):
    if len(argv) != 2 or not re.fullmatch(r"\d+\.\d+\.\d+", argv[0]) or not re.fullmatch(r"[0-9a-f]{64}", argv[1]):
        print("usage: render_cask.py <version> <sha256>", file=sys.stderr)
        return 2
    text = TEMPLATE.read_text(encoding="utf-8").replace("@VERSION@", argv[0]).replace("@SHA256@", argv[1])
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
