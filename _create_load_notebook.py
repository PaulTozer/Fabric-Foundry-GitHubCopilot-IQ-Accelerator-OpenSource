"""Synthesize a Fabric notebook that loads ./_demo_data_files/*.json into the
matching ENT_* Delta tables in EnterpriseKG_Lakehouse.

Run this *after*:
    1. Workspace + lakehouse exist
    2. Empty Delta tables created (via create_lakehouse_tables.py)
    3. Demo data generated (via _gen_demo_data.py)

Then upload the generated notebook to the Fabric workspace and run it (attached
to the lakehouse). For convenience the JSON files are bundled into the
notebook itself as base64-encoded cells — no separate file upload step needed.

Usage:
    python _create_load_notebook.py [--out _load_demo_data.ipynb]
"""
from __future__ import annotations
import argparse, base64, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from enterprise_schema import TABLE_NAMES


def build_notebook(in_dir: str, out_path: str) -> None:
    cells: list[dict] = [
        {
            "cell_type": "markdown",
            "metadata":  {},
            "source": [
                "# EnterpriseKG Lakehouse — load demo data\n",
                "\n",
                "Loads freshly generated synthetic rows across 12 ENT_* Delta tables in `EnterpriseKG_Lakehouse`.\n",
                "\n",
                "Each cell deserialises a base64-bundled JSON payload, infers the schema,\n",
                "casts to the existing Delta schema, and **overwrites** the table.\n",
                "\n",
                "Run cells top-to-bottom on the lakehouse-attached default Spark pool.\n",
            ],
        }
    ]

    # Common helper cell
    cells.append({
        "cell_type": "code",
        "metadata":  {"language": "python"},
        "source": [
            "import base64, gzip, json\n",
            "from pyspark.sql.functions import col, to_date, to_timestamp\n",
            "from pyspark.sql.types import (StringType, IntegerType, LongType,\n",
            "    DoubleType, BooleanType, DateType, TimestampType, DecimalType)\n",
            "\n",
            "def _load_b64_gzip_json(payload_b64: str):\n",
            "    raw = base64.b64decode(payload_b64)\n",
            "    decompressed = gzip.decompress(raw)\n",
            "    return json.loads(decompressed)\n",
            "\n",
            "def _load_into(table_name: str, rows: list):\n",
            "    if not rows:\n",
            "        print(f'{table_name}: 0 rows — skipped')\n",
            "        return\n",
            "    rdd = spark.sparkContext.parallelize([json.dumps(r) for r in rows])\n",
            "    df  = spark.read.json(rdd)\n",
            "    # Cast columns to match the existing Delta table schema where possible\n",
            "    target = spark.table(table_name)\n",
            "    target_cols = {f.name: f.dataType for f in target.schema.fields}\n",
            "    for c in df.columns:\n",
            "        if c in target_cols:\n",
            "            t = target_cols[c]\n",
            "            if isinstance(t, DateType):\n",
            "                df = df.withColumn(c, to_date(col(c)))\n",
            "            elif isinstance(t, TimestampType):\n",
            "                df = df.withColumn(c, to_timestamp(col(c)))\n",
            "            else:\n",
            "                df = df.withColumn(c, col(c).cast(t))\n",
            "    # Reorder to match target table\n",
            "    df = df.select(*[c for c in target.columns if c in df.columns])\n",
            "    df.write.mode('overwrite').option('overwriteSchema','false').format('delta').saveAsTable(table_name)\n",
            "    cnt = spark.table(table_name).count()\n",
            "    print(f'{table_name}: wrote {cnt:,} rows')\n",
        ],
        "outputs": [],
        "execution_count": None,
    })

    # One cell per table — embed the gzipped JSON inline
    for name in TABLE_NAMES:
        path = os.path.join(in_dir, f"{name}.json")
        if not os.path.exists(path):
            print(f"WARN: {path} not found — skipping cell for {name}")
            continue
        with open(path, "rb") as f:
            data = f.read()
        import gzip
        gzipped = gzip.compress(data, compresslevel=9)
        payload_b64 = base64.b64encode(gzipped).decode("ascii")
        # Chunk the base64 string so the notebook is readable (and JSON-safe)
        chunk = 8000
        chunks = [payload_b64[i:i+chunk] for i in range(0, len(payload_b64), chunk)]
        src = [f"# === {name} ===  ({len(data):,d} bytes JSON, {len(gzipped):,d} bytes gzipped)\n"]
        src.append(f"_payload = (\n")
        for c in chunks:
            src.append(f"    '{c}'\n")
        src.append(")\n")
        src.append(f"rows = _load_b64_gzip_json(_payload)\n")
        src.append(f"_load_into('{name.lower()}', rows)\n")
        src.append("del _payload, rows\n")
        cells.append({
            "cell_type": "code",
            "metadata":  {"language": "python"},
            "source": src,
            "outputs": [],
            "execution_count": None,
        })

    nb = {
        "nbformat":       4,
        "nbformat_minor": 5,
        "metadata": {
            "language_info": {"name": "python"},
            "kernelspec":    {"name": "synapse_pyspark", "display_name": "Synapse PySpark"},
        },
        "cells": cells,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(nb, f)
    size = os.path.getsize(out_path) / 1024 / 1024
    print(f"\nWrote {out_path}  ({size:.1f} MiB)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-dir", default="_demo_data_files",
                    help="Directory containing ENT_*.json files")
    ap.add_argument("--out",    default="_load_demo_data.ipynb",
                    help="Output notebook path")
    args = ap.parse_args()
    in_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.in_dir)
    out    = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.out)
    if not os.path.isdir(in_dir):
        print(f"ERROR: {in_dir} does not exist. Run _gen_demo_data.py first.")
        sys.exit(1)
    build_notebook(in_dir, out)
