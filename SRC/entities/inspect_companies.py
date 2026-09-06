import sqlite3

from SRC.database import DATABASE_PATH


def inspect_companies():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id,
            canonical_name,
            resolution_status,
            confidence
        FROM companies
        ORDER BY canonical_name
    """)

    rows = cursor.fetchall()

    print("=" * 80)
    print("CANONICAL COMPANIES")
    print("=" * 80)
    print(f"Total companies: {len(rows)}")
    print()

    for company_id, name, status, confidence in rows:
        print(
            f"{company_id:3} | "
            f"{name:40} | "
            f"{status:30} | "
            f"{confidence}"
        )

    conn.close()


if __name__ == "__main__":
    inspect_companies()