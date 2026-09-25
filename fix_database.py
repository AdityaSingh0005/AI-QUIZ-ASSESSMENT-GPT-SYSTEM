import os
import psycopg2


# ============================================================
# CONNECT TO DATABASE
# ============================================================

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise Exception(
        "DATABASE_URL environment variable is not set."
    )


conn = psycopg2.connect(DATABASE_URL)

cursor = conn.cursor()


# ============================================================
# CHECK CURRENT QUESTIONS TABLE
# ============================================================

print("\n" + "=" * 70)
print("🔍 CHECKING QUESTIONS TABLE")
print("=" * 70)

cursor.execute("""
    SELECT column_name, data_type
    FROM information_schema.columns
    WHERE table_name = 'questions'
    ORDER BY ordinal_position;
""")


columns = cursor.fetchall()


for column_name, data_type in columns:
    print(f"✅ {column_name} -> {data_type}")


# ============================================================
# ADD MISSING EXPLANATION COLUMN
# ============================================================

print("\n" + "=" * 70)
print("🔧 CHECKING EXPLANATION COLUMN")
print("=" * 70)


cursor.execute("""
    ALTER TABLE questions
    ADD COLUMN IF NOT EXISTS explanation TEXT;
""")


conn.commit()


print("✅ explanation column is now available.")


# ============================================================
# VERIFY
# ============================================================

cursor.execute("""
    SELECT column_name, data_type
    FROM information_schema.columns
    WHERE table_name = 'questions'
    AND column_name = 'explanation';
""")


result = cursor.fetchone()


if result:

    print(
        f"✅ VERIFIED: {result[0]} -> {result[1]}"
    )

else:

    print(
        "❌ ERROR: explanation column was not found."
    )


# ============================================================
# CLOSE CONNECTION
# ============================================================

cursor.close()
conn.close()


print("\n" + "=" * 70)
print("🎉 DATABASE FIX COMPLETED")
print("=" * 70)