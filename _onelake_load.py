"""Write ENT_* demo data straight to OneLake as Delta tables (no Spark).
Bypasses the F2 Spark limitation. Uses a storage bearer token for OneLake.
"""
import json, os, sys, subprocess, datetime as dt, decimal
import pandas as pd
import pyarrow as pa
from deltalake import write_deltalake
import enterprise_schema as s

WS = sys.argv[1]          # workspace id
LH = sys.argv[2]          # lakehouse id
DATA = os.path.join(os.path.dirname(__file__), "_demo_data_files")

# OneLake bearer token
TOKEN = subprocess.check_output(
    'az account get-access-token --resource https://storage.azure.com --query accessToken -o tsv',
    shell=True, text=True).strip()

STORAGE_OPTS = {
    "bearer_token": TOKEN,
    "use_fabric_endpoint": "true",
}

def arrow_type(t: str):
    if t == "INT": return pa.int32()
    if t == "BIGINT": return pa.int64()
    if t == "BOOLEAN": return pa.bool_()
    if t == "DATE": return pa.date32()
    if t == "STRING": return pa.string()
    if t.startswith("DECIMAL"):
        p, sc = t[t.index("(")+1:t.index(")")].split(",")
        return pa.decimal128(int(p), int(sc))
    return pa.string()

def cast_val(v, t):
    if v is None or v == "": return None
    try:
        if t == "INT" or t == "BIGINT": return int(v)
        if t == "BOOLEAN":
            if isinstance(v, bool): return v
            return str(v).lower() in ("true","1","yes")
        if t == "DATE":
            return dt.date.fromisoformat(str(v)[:10])
        if t.startswith("DECIMAL"):
            return decimal.Decimal(str(v))
        return str(v)
    except Exception:
        return None

total = 0
for name, cols in s.TABLES:
    fp = os.path.join(DATA, f"{name}.json")
    if not os.path.exists(fp):
        print(f"{name}: no file, skipped"); continue
    rows = json.load(open(fp, encoding="utf-8"))
    coltypes = dict(cols)
    # build column-oriented arrays
    data = {}
    fields = []
    for cname, ctype in cols:
        at = arrow_type(ctype)
        vals = [cast_val(r.get(cname), ctype) for r in rows]
        data[cname] = pa.array(vals, type=at)
        fields.append(pa.field(cname, at))
    table = pa.table(data, schema=pa.schema(fields))
    uri = f"abfss://{WS}@onelake.dfs.fabric.microsoft.com/{LH}/Tables/{name.lower()}"
    write_deltalake(uri, table, mode="overwrite", storage_options=STORAGE_OPTS)
    total += len(rows)
    print(f"{name.lower()}: wrote {len(rows):,} rows")

print(f"DONE — {total:,} rows across {len(s.TABLES)} tables")
