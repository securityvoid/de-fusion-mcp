import re
from pathlib import Path

README = (Path(__file__).resolve().parent.parent / "README.md").read_text()


def section(title):
    m = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", README, re.S | re.M)
    assert m, f"README.md lacks section {title}"
    return m.group(1)


def test_core_sections_present():
    for t in ("Install", "mcp.json", "Config", "Profiles", "Token", "Units"):
        assert section(t).strip()


def test_bulk_entries_section():
    assert section("Bulk entries").strip()


def test_read_only_section_names_tokens():
    s = section("Read-only documents")
    for tok in ("preserve_read_only", "READ_ONLY", "READ_ONLY_NOT_APPLIED"):
        assert tok in s


def test_folder_limit_section_names_tokens():
    s = section("Folder limit")
    for tok in ("?folder=", "folder =", "--folder"):
        assert tok in s
