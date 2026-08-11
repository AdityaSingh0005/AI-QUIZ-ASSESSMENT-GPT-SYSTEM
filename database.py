import os
import psycopg2


def get_db_connection():
    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        raise Exception("DATABASE_URL environment variable is not set")

    connection = psycopg2.connect(database_url)

    return connection