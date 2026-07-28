import sqlite3
conn = sqlite3.connect("trading.db")
conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
conn.close()
print("WAL checkpoint complete")
