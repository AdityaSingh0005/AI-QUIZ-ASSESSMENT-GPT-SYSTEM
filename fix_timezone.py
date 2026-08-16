import os
import psycopg2

try:
    print("🔄 Connecting to PostgreSQL...")

    connection = psycopg2.connect(
        os.getenv("DATABASE_URL")
    )

    connection.autocommit = True

    cursor = connection.cursor()

    print("✅ Connected!")

    # available_from
    cursor.execute("""
        ALTER TABLE quizzes
        ALTER COLUMN available_from
        TYPE TIMESTAMPTZ
        USING available_from AT TIME ZONE 'UTC';
    """)

    print("✅ available_from converted to TIMESTAMPTZ")

    # available_until
    cursor.execute("""
        ALTER TABLE quizzes
        ALTER COLUMN available_until
        TYPE TIMESTAMPTZ
        USING available_until AT TIME ZONE 'UTC';
    """)

    print("✅ available_until converted to TIMESTAMPTZ")

    print()
    print("🎉 TIMEZONE UPDATE SUCCESSFUL!")

except Exception as e:

    print()
    print("❌ ERROR:")
    print(e)

finally:

    try:
        cursor.close()
        connection.close()
        print("🔒 Database connection closed.")
    except:
        pass