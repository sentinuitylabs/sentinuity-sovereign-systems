#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sqlite3
from pathlib import Path

def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'

def size_mb(path: Path) -> float:
    return round(path.stat().st_size / 1048576, 3) if path.exists() else 0.0

def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only Sentinuity SQLite bloat audit")
    ap.add_argument("--db", required=True)
    ap.add_argument("--json")
    args = ap.parse_args()
    db = Path(args.db).resolve()
    if not db.exists():
        raise SystemExit(f"DB not found: {db}")

    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    page_size = int(con.execute("PRAGMA page_size").fetchone()[0])
    page_count = int(con.execute("PRAGMA page_count").fetchone()[0])
    freelist = int(con.execute("PRAGMA freelist_count").fetchone()[0])

    report = {
        "db": str(db),
        "file_mb": size_mb(db),
        "quick_check": con.execute("PRAGMA quick_check").fetchone()[0],
        "page_size": page_size,
        "page_count": page_count,
        "freelist_pages": freelist,
        "free_mb": round(page_size * freelist / 1048576, 3),
        "objects": [],
        "tables": [],
    }

    try:
        rows = con.execute("""
            SELECT name, COALESCE(SUM(pgsize),0) AS bytes
            FROM dbstat
            GROUP BY name
            ORDER BY bytes DESC
        """).fetchall()
        report["objects"] = [
            {"name": name, "mb": round((b or 0)/1048576, 3)}
            for name, b in rows
        ]
    except sqlite3.Error as exc:
        report["dbstat_error"] = str(exc)

    table_names = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]
    obj_sizes = {r["name"]: r["mb"] for r in report["objects"]}
    for table in table_names:
        try:
            count = int(con.execute(f"SELECT COUNT(*) FROM {q(table)}").fetchone()[0])
        except sqlite3.Error:
            count = None
        table_mb = obj_sizes.get(table, 0.0)
        idx_mb = 0.0
        indexes = []
        for idx_row in con.execute(f"PRAGMA index_list({q(table)})").fetchall():
            idx_name = idx_row[1]
            mb = obj_sizes.get(idx_name, 0.0)
            idx_mb += mb
            indexes.append({"name": idx_name, "mb": mb})
        report["tables"].append({
            "table": table,
            "rows": count,
            "table_mb": round(table_mb, 3),
            "index_mb": round(idx_mb, 3),
            "total_mb": round(table_mb + idx_mb, 3),
            "indexes": sorted(indexes, key=lambda x: x["mb"], reverse=True),
        })
    report["tables"].sort(key=lambda x: x["total_mb"], reverse=True)
    con.close()

    text = json.dumps(report, indent=2)
    print(text)
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
