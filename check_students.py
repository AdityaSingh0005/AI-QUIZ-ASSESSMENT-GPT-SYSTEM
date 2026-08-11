import os
import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])

cursor = conn.cursor()

cursor.execute("""
    SELECT student_id, full_name, roll_number, email
    FROM students
    ORDER BY student_id
""")

students = cursor.fetchall()

print("Students in PostgreSQL:")
print(students)

cursor.close()
conn.close()