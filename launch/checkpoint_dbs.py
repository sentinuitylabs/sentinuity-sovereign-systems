import sqlite3
for db in ['sentinuity_matrix.db','sentinuity_intelligence.db']:
    try:
        c=sqlite3.connect(db)
        c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        c.close()
        print('checkpointed', db)
    except Exception as e:
        print('checkpoint skipped', db, e)
