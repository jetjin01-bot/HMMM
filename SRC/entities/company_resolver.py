import sqlite3
import re

from SRC.database import DATABASE_PATH


LEGAL_SUFFIXES = {
    "limited",
    "ltd",
    "plc",
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "company",
    "co",
    "llc",
}


def normalize_company_name(name: str) -> str:
    """
    Conservative company-name normalization.

    Removes punctuation, lowercases, normalizes whitespace,
    and strips common legal suffixes from the end.
    """

    name = name.lower().strip()

    name = re.sub(r"[^\w\s]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()

    parts = name.split()

    while parts and parts[-1] in LEGAL_SUFFIXES:
        parts.pop()

    return " ".join(parts)


def resolve_companies():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id,
            observed_value,
            normalized_value,
            source_file_id
        FROM entity_mentions
        WHERE entity_type = 'company'
          AND extraction_method = 'folder_context'
        ORDER BY id
    """)
    rows = cursor.fetchall()

    groups = {}

    for mention_id, observed_value, _, source_file_id in rows:
        normalized = normalize_company_name(observed_value)

        groups.setdefault(normalized, []).append(
            {
                "mention_id": mention_id,
                "observed_value": observed_value,
                "source_file_id": source_file_id,
            }
        )

    canonical_count = 0
    alias_count = 0

    for normalized_name, mentions in groups.items():
        if not normalized_name:
            continue

        # Choose the most frequent observed form as canonical display name
        frequency = {}

        for mention in mentions:
            value = mention["observed_value"]

            frequency[value] = frequency.get(value, 0) + 1

        canonical_name = max(
            frequency,
            key=frequency.get,
        )

        cursor.execute("""
            SELECT id
            FROM companies
            WHERE canonical_name = ?
        """, (
            canonical_name,
        ))

        existing = cursor.fetchone()

        if existing:
            company_id = existing[0]

        else:
            cursor.execute("""
                INSERT INTO companies (
                    canonical_name,
                    resolution_status,
                    confidence
                )
                VALUES (?, ?, ?)
            """, (
                canonical_name,
                "resolved_deterministically",
                0.95,
            ))

            company_id = cursor.lastrowid
            canonical_count += 1

        # Link mentions to canonical company
        for mention in mentions:
            cursor.execute("""
                UPDATE entity_mentions
                SET
                    normalized_value = ?,
                    resolution_status = 'resolved',
                    resolved_entity_id = ?
                WHERE id = ?
            """, (
                normalized_name,
                company_id,
                mention["mention_id"],
            ))

        # Store distinct observed forms as aliases
        distinct_aliases = set(
            mention["observed_value"]
            for mention in mentions
        )

        for alias in distinct_aliases:
            cursor.execute("""
                INSERT INTO aliases (
                    entity_type,
                    entity_id,
                    alias,
                    normalized_alias,
                    source_file_id,
                    confidence,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                "company",
                company_id,
                alias,
                normalize_company_name(alias),
                mentions[0]["source_file_id"],
                0.95,
                "confirmed",
            ))

            alias_count += 1

    conn.commit()
    conn.close()

    print("=" * 70)
    print("COMPANY RESOLUTION")
    print("=" * 70)

    print(f"Company mentions processed: {len(rows)}")
    print(f"Canonical companies created: {canonical_count}")
    print(f"Aliases stored:              {alias_count}")


if __name__ == "__main__":
    resolve_companies()