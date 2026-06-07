import sqlite3

DB = 'tml_arena.db'
conn = sqlite3.connect(DB)
cur = conn.cursor()

print('Before schema:')
for row in cur.execute("PRAGMA table_info('membership')"):
    print(row)

try:
    cur.execute("ALTER TABLE membership ADD COLUMN type VARCHAR(20) NOT NULL DEFAULT 'membership';")
    conn.commit()
    print('Column added')
except Exception as e:
    print('Add column skipped or failed:', e)

print('After schema:')
for row in cur.execute("PRAGMA table_info('membership')"):
    print(row)

conn.close()
