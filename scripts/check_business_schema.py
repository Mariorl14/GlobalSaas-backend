import urllib.parse

import psycopg2


def load_database_url(env_path: str) -> str:
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("DATABASE_URL not found in .env")


def main() -> None:
    database_url = load_database_url(".env")
    parsed = urllib.parse.urlparse(database_url)

    conn = psycopg2.connect(
        dbname=parsed.path.lstrip("/"),
        user=parsed.username,
        password=parsed.password,
        host=parsed.hostname,
        port=parsed.port or 5432,
    )

    cur = conn.cursor()

    cur.execute(
        """
        select exists(
          select 1
          from information_schema.columns
          where table_name=%s and column_name=%s
        )
        """,
        ("business", "plan_id"),
    )
    plan_id_exists = cur.fetchone()[0]

    cur.execute(
        """
        select exists(
          select 1
          from information_schema.tables
          where table_name=%s
        )
        """,
        ("plan",),
    )
    plan_table_exists = cur.fetchone()[0]

    cur.execute(
        """
        select column_name, data_type
        from information_schema.columns
        where table_name=%s
        order by ordinal_position
        """,
        ("business",),
    )
    business_columns = cur.fetchall()

    print(f"business.plan_id exists: {plan_id_exists}")
    print(f"plan table exists: {plan_table_exists}")
    print("business columns:")
    for name, dtype in business_columns:
        print(f"  - {name}: {dtype}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()

