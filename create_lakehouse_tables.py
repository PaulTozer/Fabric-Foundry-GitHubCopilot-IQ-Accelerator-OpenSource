"""Generate DDL for the 12 ENT_* Delta tables.

Usage
-----
    python create_lakehouse_tables.py            # prints the DDL
    python create_lakehouse_tables.py --notebook # writes a Fabric .ipynb you can upload

The printed/exported DDL is intended to be pasted into a PySpark notebook attached
to the `EnterpriseKG_Lakehouse` lakehouse in the `Enterprise KG` Fabric workspace.

Tables are created with `IF NOT EXISTS`; re-running is safe.
"""
from __future__ import annotations
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from enterprise_schema import TABLES


def _ddl_for(table_name: str, cols: list) -> str:
    lines = [f"CREATE TABLE IF NOT EXISTS {table_name.lower()} ("]
    col_lines = [f"  `{name}` {dtype}" for name, dtype in cols]
    lines.append(",\n".join(col_lines))
    lines.append(") USING DELTA")
    return "\n".join(lines)


def all_ddl() -> list[str]:
    return [_ddl_for(name, cols) for name, cols in TABLES]


def write_notebook(path: str) -> None:
    """Write a Fabric notebook that creates every table via spark.sql."""
    cells = [
        {
            "cell_type": "markdown",
            "metadata":  {},
            "source": [
                "# EnterpriseKG Lakehouse — table bootstrap\n",
                "\n",
                "Creates the 12 ENT_* Delta tables in `EnterpriseKG_Lakehouse`.\n",
                "\n",
                "Run after the workspace + lakehouse exist, before loading demo data.",
            ],
        }
    ]
    for ddl in all_ddl():
        cells.append({
            "cell_type": "code",
            "metadata":  {"language": "python"},
            "source": [f"spark.sql('''{ddl}''')"],
            "outputs": [],
            "execution_count": None,
        })
    nb = {
        "nbformat":       4,
        "nbformat_minor": 5,
        "metadata": {
            "language_info": {"name": "python"},
            "kernelspec":    {"name": "synapse_pyspark",
                              "display_name": "Synapse PySpark"},
        },
        "cells": cells,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(nb, f, indent=2)
    print(f"Wrote {path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--notebook",
                    nargs="?", const="_create_tables.ipynb", default=None,
                    help="Write a Fabric notebook instead of printing the DDL.")
    args = ap.parse_args()
    if args.notebook:
        write_notebook(args.notebook)
    else:
        for ddl in all_ddl():
            print(ddl)
            print(";")
