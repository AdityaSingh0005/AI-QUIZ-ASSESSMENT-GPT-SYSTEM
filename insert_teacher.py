import os
import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])
conn.autocommit = True

cursor = conn.cursor()

cursor.execute("""
    INSERT INTO teachers
    (teacher_id, full_name, email, password)
    VALUES
    (1, 'Admin Teacher', 'teacher@gmail.com', '12345')
    ON CONFLICT (teacher_id) DO NOTHING
""")

print("Teacher inserted successfully")

cursor.close()
conn.close()