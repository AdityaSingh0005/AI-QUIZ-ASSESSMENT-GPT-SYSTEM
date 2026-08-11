import os
import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])

cursor = conn.cursor()

cursor.execute("""
    SELECT teacher_id, full_name, email
    FROM teachers
""")

teachers = cursor.fetchall()

print(teachers)

cursor.close()
conn.close()