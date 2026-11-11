import json
from pathlib import Path

import defusion

MANIFEST = Path(__file__).resolve().parent.parent / "De-Fusion" / "De-Fusion.manifest"


def test_manifest():
    m = json.loads(MANIFEST.read_text())
    assert m["runOnStartup"] is True
    assert m["supportedOS"] == "windows|mac"
    assert m["author"] == "Doug Logan"
    assert m["description"] == "De-Fusion: a comprehensive MCP server for Autodesk Fusion"
    assert m["version"] == defusion.__version__
