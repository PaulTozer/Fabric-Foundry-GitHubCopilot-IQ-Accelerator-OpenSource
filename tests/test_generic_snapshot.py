import json
import re
from pathlib import Path

from enterprise_schema import TABLE_NAMES, TABLES

ROOT = Path(__file__).resolve().parents[1]


def test_complete_debranding_and_portable_notebooks():
    # Construct denied historical identifiers so this guard doesn't match itself.
    denied = re.compile("|".join(["mo" + "tt", "mi" + "tie", "ar" + "up",
                                 "mm" + "d_", "mf" + "m_", "mo" + "ata",
                                 "mm" + "_logo"]), re.IGNORECASE)
    ignored = {".git", ".venv", "node_modules", ".next", "__pycache__",
               ".pytest_cache", ".ruff_cache", "_demo_data_files"}
    for path in ROOT.rglob("*"):
        parts = path.relative_to(ROOT).parts
        if any(part in ignored or part.startswith(".import") or part == ".reference-kratos" for part in parts):
            continue
        if path.name.startswith(".") and path.suffix == ".py":
            continue
        if path.is_file() and path.suffix in {".py", ".ts", ".tsx", ".md", ".json", ".mjs", ".css", ".bicep", ".ipynb", ".example", ".yaml", ".svg"}:
            if path.name == "package-lock.json":
                continue  # Content hashes are not semantic branding identifiers.
            text = path.read_text(encoding="utf-8")
            assert not denied.search(text), str(path.relative_to(ROOT))
            if path.suffix == ".ipynb":
                notebook = json.loads(text)
                assert not notebook.get("metadata", {}).get("dependencies")
                assert all(not cell.get("outputs") for cell in notebook["cells"])
                assert "payload_b64" not in text


def test_twelve_tables_notebook_and_schema_card_match():
    from fabric_tools import _schema_card_payload
    assert len(TABLES) == 12
    assert {table["name"] for table in _schema_card_payload()["tables"]} == set(TABLE_NAMES)
    notebook = (ROOT / "_create_tables.ipynb").read_text(encoding="utf-8")
    for name in TABLE_NAMES:
        assert name.lower() in notebook
    assert all(name.startswith("ENT_") for name in TABLE_NAMES)
