#!/usr/bin/env python3
"""Read-only pre-shutdown bloat and retention coverage audit."""
from __future__ import annotations
import argparse, json, sqlite3
from pathlib import Path

def q(n): return '"' + n.replace('"','""') + '"'

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--db", default="sentinuity_matrix.db")
    ap.add_argument("--top", type=int, default=40)
    args=ap.parse_args()
    p=Path(args.db).resolve()
    con=sqlite3.connect(f"file:{p.as_posix()}?mode=ro",uri=True,timeout=30)
    con.row_factory=sqlite3.Row
    quick=con.execute("PRAGMA quick_check").fetchone()[0]
    sizes={}
    try:
        sizes={r[0]:float(r[1] or 0)/1048576 for r in con.execute(
            "SELECT name,SUM(pgsize) FROM dbstat GROUP BY name"
        )}
    except sqlite3.Error as exc:
        print("dbstat unavailable:",exc)
    tables=[r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )]
    rows=[]
    for t in tables:
        try: count=con.execute(f"SELECT COUNT(*) FROM {q(t)}").fetchone()[0]
        except: count=None
        rows.append({"table":t,"rows":count,"mb":round(sizes.get(t,0),3)})
    rows.sort(key=lambda x:x["mb"],reverse=True)
    print("="*100)
    print("SENTINUITY READ-ONLY DB BLOAT AUDIT")
    print("="*100)
    print("DB:",p)
    print("Size MB:",round(p.stat().st_size/1048576,3))
    print("quick_check:",quick)
    print("\nTOP TABLE OBJECTS")
    for r in rows[:args.top]:
        print(f"{r['table']:<48} rows={str(r['rows']):>10} mb={r['mb']:>9.3f}")
    print("\nTOP ALL OBJECTS (INCLUDING INDEXES)")
    for name,mb in sorted(sizes.items(),key=lambda x:x[1],reverse=True)[:args.top]:
        print(f"{name:<58} mb={mb:>9.3f}")
    con.close()

if __name__=="__main__":
    main()
