import sqlite3
from collections import Counter

from SRC.database import DATABASE_PATH


def normalize_email(email: str) -> str:
    return email.strip().lower()


def resolve_people():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id,
            observed_value,
            identifier_value
        FROM entity_mentions
        WHERE entity_type = 'person'
          AND identifier_value IS NOT NULL
          AND TRIM(identifier_value) != ''
        ORDER BY id
    """)

    rows = cursor.fetchall()

    groups = {}

    for mention_id, observed_value, email in rows:
        normalized_email = normalize_email(email)

        groups.setdefault(
            normalized_email,
            {
                "names": [],
                "mentions": [],
            }
        )

        groups[normalized_email]["names"].append(observed_value)
        groups[normalized_email]["mentions"].append(mention_id)

    person_count = 0
    alias_count = 0

    for email, data in groups.items():

        name_counts = Counter(data["names"])
        canonical_name = name_counts.most_common(1)[0][0]

        cursor.execute("""
            INSERT INTO people (
                canonical_name,
                email,
                resolution_status,
                confidence
            )
            VALUES (?, ?, ?, ?)
        """, (
            canonical_name,
            email,
            "resolved_deterministically",
            0.99,
        ))

        person_id = cursor.lastrowid
        person_count += 1

        for mention_id in data["mentions"]:
            cursor.execute("""
                UPDATE entity_mentions
                SET
                    resolution_status = 'resolved',
                    resolved_entity_id = ?
                WHERE id = ?
            """, (
                person_id,
                mention_id,
            ))

        distinct_names = set(data["names"])

        for alias in distinct_names:
            cursor.execute("""
                INSERT INTO aliases (
                    entity_type,
                    entity_id,
                    alias,
                    normalized_alias,
                    confidence,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                "person",
                person_id,
                alias,
                alias.strip().lower(),
                0.99,
                "confirmed",
            ))

            alias_count += 1

    conn.commit()
    conn.close()

    print("=" * 70)
    print("PERSON RESOLUTION")
    print("=" * 70)

    print(f"Person mentions processed: {len(rows)}")
    print(f"Canonical people created:  {person_count}")
    print(f"Person aliases stored:      {alias_count}")


if __name__ == "__main__":
    resolve_people()