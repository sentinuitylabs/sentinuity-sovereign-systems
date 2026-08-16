#!/usr/bin/env python3
from __future__ import annotations
import argparse, sqlite3
from pathlib import Path
COLS={
 "display_high_price":"REAL", "display_high_source":"TEXT",
 "authoritative_high_source":"TEXT", "authoritative_high_at":"REAL",
 "runner_floor_state":"TEXT"
}
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--db',type=Path,default=Path('sentinuity_matrix.db')); a=ap.parse_args()
 c=sqlite3.connect(str(a.db),timeout=30); c.execute('PRAGMA busy_timeout=30000')
 assert c.execute('PRAGMA quick_check').fetchone()[0]=='ok'
 have={r[1] for r in c.execute('PRAGMA table_info(paper_positions)')}
 for k,t in COLS.items():
  if k not in have: c.execute(f'ALTER TABLE paper_positions ADD COLUMN {k} {t}')
 c.execute("UPDATE paper_positions SET display_high_price=COALESCE(display_high_price,highest_price_seen), display_high_source=COALESCE(display_high_source,'legacy_import') WHERE display_high_price IS NULL")
 c.commit(); assert c.execute('PRAGMA quick_check').fetchone()[0]=='ok'; c.close(); print('[PASS] migration applied')
 return 0
if __name__=='__main__': raise SystemExit(main())
