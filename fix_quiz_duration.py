import os
import psycopg2


DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise Exception("DATABASE_URL environment variable is not set")


print("🔄 Connecting to PostgreSQL...")

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True
cur = conn.cursor()

print("✅ Connected to PostgreSQL!")


# ============================================================
# ADD DURATION_MINUTES TO QUIZZES
# ============================================================

cur.execute("""
ALTER TABLE quizzes
ADD COLUMN IF NOT EXISTS duration_minutes INTEGER DEFAULT 10;
""")

print("✓ quizzes.duration_minutes ready")


# ============================================================
# VERIFY
# ============================================================

cur.execute("""
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'quizzes'
AND column_name = 'duration_minutes';
""")

result = cur.fetchone()

print("\n📋 DURATION_MINUTES COLUMN:\n")

if result:
    print(f"   Column  : {result[0]}")
    print(f"   Type    : {result[1]}")
    print(f"   Default : {result[2]}")
else:
    print("❌ duration_minutes column was not found!")


print("\n==========================================")
print("🎉 QUIZ DURATION SETUP COMPLETED!")
print("==========================================")


cur.close()
conn.close()

print("🔒 Database connection closed.")