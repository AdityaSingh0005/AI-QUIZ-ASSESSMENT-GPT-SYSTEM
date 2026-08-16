import os
import psycopg2


# ==========================================
# GET RENDER DATABASE URL
# ==========================================

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    print("❌ DATABASE_URL nahi mila.")
    print()
    print("PowerShell me pehle Render External Database URL set karo:")
    print('$env:DATABASE_URL="YOUR_RENDER_DATABASE_URL"')
    print()
    exit()


# ==========================================
# CONNECT TO DATABASE
# ==========================================

try:

    print("🔄 Connecting to PostgreSQL...")

    connection = psycopg2.connect(
        DATABASE_URL
    )

    cursor = connection.cursor()

    print("✅ Connected to PostgreSQL!")


    # ==========================================
    # ADD QUESTION TIMER COLUMN
    # ==========================================

    cursor.execute("""
        ALTER TABLE quizzes
        ADD COLUMN IF NOT EXISTS
        question_time_seconds INTEGER DEFAULT 60;
    """)

    print("✅ question_time_seconds checked/created")


    # ==========================================
    # ADD AVAILABLE FROM
    # ==========================================

    cursor.execute("""
        ALTER TABLE quizzes
        ADD COLUMN IF NOT EXISTS
        available_from TIMESTAMPTZ
        DEFAULT CURRENT_TIMESTAMP;
    """)

    print("✅ available_from checked/created")


    # ==========================================
    # ADD AVAILABLE UNTIL
    # ==========================================

    cursor.execute("""
        ALTER TABLE quizzes
        ADD COLUMN IF NOT EXISTS
        available_until TIMESTAMPTZ NULL;
    """)

    print("✅ available_until checked/created")


    # ==========================================
    # SAVE CHANGES
    # ==========================================

    connection.commit()

    print()
    print("🎉 DATABASE UPDATE SUCCESSFUL!")
    print()


    # ==========================================
    # VERIFY COLUMNS
    # ==========================================

    cursor.execute("""
        SELECT
            column_name,
            data_type
        FROM information_schema.columns
        WHERE table_name = 'quizzes'
        ORDER BY ordinal_position;
    """)

    columns = cursor.fetchall()

    print("📋 CURRENT QUIZZES TABLE COLUMNS:")
    print("----------------------------------")

    for column in columns:

        print(
            f"{column[0]}  →  {column[1]}"
        )


except Exception as e:

    print()
    print("❌ DATABASE ERROR:")
    print(e)


finally:

    try:
        cursor.close()
        connection.close()
        print()
        print("🔒 Database connection closed.")

    except:
        pass