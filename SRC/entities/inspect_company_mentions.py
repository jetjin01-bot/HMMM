import sqlite3

from SRC.database import DATABASE_PATH


def inspect_company_mentions():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # Only inspect content-derived company mentions,
    # not folder-derived mentions.
    cursor.execute("""
        SELECT
            observed_value,
            role,
            COUNT(*) AS occurrence_count
        FROM entity_mentions
        WHERE entity_type = 'company'
          AND extraction_method = 'labelled_pdf_field'
        GROUP BY observed_value, role
        ORDER BY occurrence_count DESC, observed_value
    """)

    rows = cursor.fetchall()

    print("=" * 80)
    print("CONTENT-DERIVED COMPANY MENTIONS")
    print("=" * 80)

    print(f"Distinct mention/role combinations: {len(rows)}")
    print()

    for observed_value, role, count in rows[:100]:
        print(
            f"{count:4} | "
            f"{role:12} | "
            f"{observed_value}"
        )

    conn.close()


if __name__ == "__main__":
    inspect_company_mentions()