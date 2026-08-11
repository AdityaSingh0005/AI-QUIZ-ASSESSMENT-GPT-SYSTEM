import os
import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])
conn.autocommit = True

cursor = conn.cursor()

cursor.execute("""
    INSERT INTO students
    (
        student_id,
        full_name,
        roll_number,
        department,
        semester,
        section,
        password
    )
    VALUES
    (
        1,
        'Aditya Singh',
        'MCA001',
        'MCA',
        '3',
        'A',
        '12345'
    )
    ON CONFLICT (student_id) DO NOTHING
""")

print("Student inserted successfully")

cursor.close()
conn.close()