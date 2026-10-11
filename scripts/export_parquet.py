"""Step 3: export the DuckDB tables to Parquet files, ready to upload to S3.

Why: Athena does not read DuckDB files. It reads files sitting in S3, and
Parquet is the format it reads most cheaply (columnar + compressed, keeps types).

Output layout (mirrors the agreed S3 folders, one folder per table):
    data/export/source/weekly_metrics/weekly_metrics.parquet
    data/export/source/weekly_category_metrics/weekly_category_metrics.parquet
    data/export/source/order_facts/order_facts.parquet
    data/export/fixtures/weekly_metrics/weekly_metrics.parquet
    data/export/fixtures/weekly_category_metrics/weekly_category_metrics.parquet

Each file is read back and checked against the DuckDB table (row count,
revenue total, column types). The script exits with an error if anything
does not match, so a bad file is never uploaded.

Run from the project root, with no notebook connected to the databases:
    python scripts/export_parquet.py
"""

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
EXPORT_DIR = ROOT / "data" / "export"

DATABASES = {
    "source": ROOT / "data" / "olist.duckdb",     # real (original) tables
    "fixtures": ROOT / "data" / "golden.duckdb",  # tables with planted scenarios
}

EXPORTS = {
    "source": ["weekly_metrics", "weekly_category_metrics", "order_facts"],
    "fixtures": ["weekly_metrics", "weekly_category_metrics"],
}

# Column used for the "same total" check, first one found wins
REVENUE_CANDIDATES = ["product_revenue", "category_revenue"]

# Types Athena cannot read from Parquet (or reads differently)
NOT_ATHENA_SAFE = ("HUGEINT", "UHUGEINT", "INTERVAL")


def table_types(con, relation: str) -> dict:
    """Column name -> type, for a table or a parquet file."""
    return {row[0]: row[1] for row in con.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()}


def main() -> int:
    for name, path in DATABASES.items():
        if not path.exists():
            print(f"Missing database: {path}")
            return 1

    con = duckdb.connect()  # in-memory; the real files are attached read-only
    for name, path in DATABASES.items():
        con.execute(f"ATTACH '{path.as_posix()}' AS {name} (READ_ONLY)")

    failures, total_bytes = [], 0
    for db, tables in EXPORTS.items():
        for table in tables:
            label = f"{db}/{table}"
            out_dir = EXPORT_DIR / db / table
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / f"{table}.parquet"
            source = f"{db}.main.{table}"
            parquet = f"read_parquet('{out_file.as_posix()}')"

            src_types = table_types(con, source)
            bad = [f"{c} ({t})" for c, t in src_types.items() if t.upper().startswith(NOT_ATHENA_SAFE)]
            if bad:
                failures.append(f"{label}: not Athena-safe types: {', '.join(bad)} (cast them in the SQL)")
                continue

            con.execute(f"COPY (SELECT * FROM {source}) TO '{out_file.as_posix()}' "
                        f"(FORMAT PARQUET, COMPRESSION SNAPPY)")

            # Read the file back and compare with the table
            problems = []
            src_rows = con.execute(f"SELECT COUNT(*) FROM {source}").fetchone()[0]
            out_rows = con.execute(f"SELECT COUNT(*) FROM {parquet}").fetchone()[0]
            if src_rows != out_rows:
                problems.append(f"rows {src_rows} vs {out_rows}")

            revenue = next((c for c in REVENUE_CANDIDATES if c in src_types), None)
            if revenue:
                src_sum = con.execute(f"SELECT SUM({revenue}) FROM {source}").fetchone()[0]
                out_sum = con.execute(f"SELECT SUM({revenue}) FROM {parquet}").fetchone()[0]
                if src_sum is None or out_sum is None or abs(float(src_sum) - float(out_sum)) > 0.01:
                    problems.append(f"{revenue} total {src_sum} vs {out_sum}")

            out_types = table_types(con, parquet)
            if src_types != out_types:
                diff = [f"{c}: {src_types.get(c)} -> {out_types.get(c)}"
                        for c in sorted(set(src_types) | set(out_types))
                        if src_types.get(c) != out_types.get(c)]
                problems.append("types changed: " + "; ".join(diff))

            size = out_file.stat().st_size
            total_bytes += size
            if problems:
                failures.append(f"{label}: " + ", ".join(problems))
                print(f"FAIL  {label:40s} {', '.join(problems)}")
            else:
                checked = f"{revenue} total {float(src_sum):,.2f}" if revenue else "no revenue column"
                print(f"OK    {label:40s} {out_rows:>7,} rows  {size / 1024:>8.1f} KB  {checked}")

    con.close()
    print(f"\nTotal size: {total_bytes / 1024 / 1024:.2f} MB in {EXPORT_DIR}")
    if failures:
        print("\nProblems found. Do not upload:")
        for f in failures:
            print(" -", f)
        return 1
    print("All files match their DuckDB tables. Ready for upload (Step 4).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
