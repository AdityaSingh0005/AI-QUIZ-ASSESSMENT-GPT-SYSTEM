import os
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    print("❌ DATABASE_URL environment variable nahi mila.")
    print("Render ka External Database URL use karna hoga.")
    exit()

try:
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()

    print("✅ PostgreSQL connected!")

    cursor.execute("""
        ALTER TABLE results
        ADD COLUMN IF NOT EXISTS attempt_id INTEGER;
    """)

    conn.commit()

    print("✅ attempt_id column successfully added!")

    cursor.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'results'
        ORDER BY ordinal_position;
    """)

    columns = cursor.fetchall()

    print("\n📋 RESULTS TABLE COLUMNS:")
    for column in columns:
        print(" -", column[0])

    cursor.close()
    conn.close()

    print("\n🎉 DATABASE UPDATE SUCCESSFUL!")

except Exception as e:
    print("\n❌ DATABASE ERROR:")
    print(type(e).__name__)
    print(e)