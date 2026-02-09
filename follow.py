import sqlite3

conn = sqlite3.connect("medbuddy.db")
c = conn.cursor()

try:
    c.execute("""
        ALTER TABLE appointments
        ADD COLUMN consultation_type TEXT DEFAULT 'FIRST'
    """)
    print("✅ consultation_type column added")
except Exception as e:
    print("⚠️", e)

conn.commit()
conn.close()
