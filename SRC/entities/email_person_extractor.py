import sqlite3
from pathlib import Path
from email.utils import getaddresses

from SRC.database import DATABASE_PATH
from SRC.Ingestion.extraction.content_extractor import extract_eml_content
from SRC.entities.mention_extractor import insert_mention


def parse_people_from_header(header_value: str):
    """
    Parse names/emails from an email header.
    Returns list of (name, email).
    """
    if not header_value:
        return []

    return getaddresses([header_value])


def extract_email_person_mentions():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")

    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id,
            file_path
        FROM source_files
        WHERE LOWER(extension) = '.eml'
        ORDER BY id
    """)

    rows = cursor.fetchall()

    mention_count = 0
    email_count = 0
    errors = 0

    for source_file_id, file_path_str in rows:
        file_path = Path(file_path_str)

        try:
            email_data = extract_eml_content(file_path)

            header_roles = {
                "sender": email_data.get("sender"),
                "recipient": email_data.get("recipient"),
                "cc": email_data.get("cc"),
            }

            for role, header_value in header_roles.items():
                people = parse_people_from_header(header_value)

                for name, email in people:
                    name = (name or "").strip()
                    email = (email or "").strip().lower()

                    # Prefer display name if available.
                    # Otherwise retain the email address as the observed value.
                    observed_value = name if name else email

                    if not observed_value:
                        continue

                    insert_mention(
                        conn=conn,
                        entity_type="person",
                        observed_value=observed_value,
                        source_file_id=source_file_id,
                        role=role,
                        extraction_method="email_header",
                        confidence=0.99,
                        identifier_value=email if email else None,
                    )

                    mention_count += 1

                    if email:
                        email_count += 1

        except Exception as exc:
            errors += 1
            print(
                f"[ERROR] {file_path.name}: "
                f"{type(exc).__name__}: {exc}"
            )

    conn.commit()
    conn.close()

    print("=" * 70)
    print("EMAIL PERSON MENTION EXTRACTION")
    print("=" * 70)
    print(f"EML files checked:         {len(rows)}")
    print(f"Person mentions inserted:  {mention_count}")
    print(f"Mentions with email:       {email_count}")
    print(f"Errors:                    {errors}")


if __name__ == "__main__":
    extract_email_person_mentions()