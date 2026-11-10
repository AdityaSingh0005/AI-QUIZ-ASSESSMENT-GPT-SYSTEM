from getpass import getpass
from werkzeug.security import generate_password_hash
from database import get_db_connection


def create_admin():
    print("\n===== CREATE ADMIN ACCOUNT =====\n")

    full_name = input("Enter Admin Name: ").strip()
    email = input("Enter Admin Email: ").strip().lower()

    if not full_name or not email:
        print("Name and email are required.")
        return

    password = getpass("Enter Admin Password: ")
    confirm_password = getpass("Confirm Admin Password: ")

    if len(password) < 10:
        print("Password must contain at least 10 characters.")
        return

    if password != confirm_password:
        print("Passwords do not match.")
        return

    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute(
            """
            SELECT admin_id
            FROM admin_users
            WHERE email = %s
            """,
            (email,)
        )

        if cursor.fetchone():
            print("An admin account with this email already exists.")
            return

        hashed_password = generate_password_hash(password)

        cursor.execute(
            """
            INSERT INTO admin_users (full_name, email, password)
            VALUES (%s, %s, %s)
            RETURNING admin_id
            """,
            (full_name, email, hashed_password)
        )

        admin_id = cursor.fetchone()[0]
        db.commit()

        print("\nAdmin account created successfully!")
        print(f"Admin ID: {admin_id}")
        print(f"Admin Name: {full_name}")
        print(f"Admin Email: {email}")

    except Exception as e:
        if db:
            db.rollback()
        print(f"Error creating admin: {e}")

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()


if __name__ == "__main__":
    create_admin()