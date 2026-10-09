"""Step 5: generate the Athena CREATE TABLE statements from the exported Parquet files.

Why: the column names and types are read from the actual files, so the table
definition always matches the data (nothing typed by hand). This matters
because we cannot delete a table once created.

Run from the project root after export_parquet.py:
    python scripts/make_athena_ddl.py
It prints one statement per table and saves them to sql/athena_create_tables.sql.
Paste ONE statement at a time into the Athena query editor (workgroup S04-WBR).
"""

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
EXPORT_DIR = ROOT / "data" / "export"
OUT_FILE = ROOT / "sql" / "athena_create_tables.sql"

BUCKET = "aizoya-s04-wbr-source-704554708405"
DATABASE = "s04_wbr"

# (S3 folder, table) -> Athena table name. Golden tables get a "golden_" prefix
# because there is only one database.
TABLES = {
    ("source", "weekly_metrics"): "weekly_metrics",
    ("source", "weekly_category_metrics"): "weekly_category_metrics",
    ("source", "order_facts"): "order_facts",
    ("fixtures", "weekly_metrics"): "golden_weekly_metrics",
    ("fixtures", "weekly_category_metrics"): "golden_weekly_category_metrics",
}

# DuckDB type -> Athena type
TYPE_MAP = {
    "BIGINT": "bigint", "INTEGER": "int", "SMALLINT": "smallint", "TINYINT": "tinyint",
    "DOUBLE": "double", "FLOAT": "float", "BOOLEAN": "boolean",
    "VARCHAR": "string", "DATE": "date", "TIMESTAMP": "timestamp",
}


def athena_type(duck_type: str) -> str:
    t = duck_type.upper()
    if t in TYPE_MAP:
        return TYPE_MAP[t]
    if t.startswith("DECIMAL"):
        return t.lower()
    raise ValueError(f"No safe Athena type for {duck_type}")


def main() -> int:
    con = duckdb.connect()
    statements, problems = [], []
    for (folder, table), athena_name in TABLES.items():
        file = EXPORT_DIR / folder / table / f"{table}.parquet"
        if not file.exists():
            problems.append(f"missing {file} (run export_parquet.py first)")
            continue
        cols = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{file.as_posix()}')").fetchall()
        lines = []
        for name, dtype, *_ in cols:
            try:
                lines.append(f"  `{name}` {athena_type(dtype)}")
            except ValueError as exc:
                problems.append(f"{athena_name}.{name}: {exc}")
        statements.append(
            f"-- {athena_name}: {len(cols)} columns, from s3://{BUCKET}/{folder}/{table}/\n"
            f"CREATE EXTERNAL TABLE IF NOT EXISTS {DATABASE}.{athena_name} (\n"
            + ",\n".join(lines)
            + f"\n)\nSTORED AS PARQUET\nLOCATION 's3://{BUCKET}/{folder}/{table}/';"
        )
    con.close()

    if problems:
        print("Problems found. Do not run these in Athena:")
        for p in problems:
            print(" -", p)
        return 1
    text = "\n\n".join(statements) + "\n"
    OUT_FILE.parent.mkdir(exist_ok=True)
    OUT_FILE.write_text(text)
    print(text)
    print(f"Saved to {OUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
