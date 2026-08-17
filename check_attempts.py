import os
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise Exception("DATABASE_URL not found")

conn = psycopg2.connect(DATABASE_URL)

cur = conn.cursor()

cur.execute("""
    SELECT
        column_name,
        data_type
    FROM information_schema.columns
    WHERE table_name = 'quiz_attempts'
    ORDER BY ordinal_position
""")

print("\nQUIZ_ATTEMPTS COLUMNS:\n")

for row in cur.fetchall():
    print(row)

cur.close()
conn.close()