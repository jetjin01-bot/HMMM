from pathlib import Path
import re
import sqlite3

from SRC.database import DATABASE_PATH


DOCUMENT_PATTERNS = [
    ("invoice", re.compile(r"\bINV[-_ ]?(\d+)\b", re.IGNORECASE)),
    ("quotation", re.compile(r"\bQUO[-_ ]?(\d+)\b", re.IGNORECASE)),
    ("purchase_order", re.compile(r"\bPO[-_ ]?(\d+)\b", re.IGNORECASE)),
    ("delivery_note", re.compile(r"\bDN[-_ ]?(\d+)\b", re.IGNORECASE)),
    ("drawing", re.compile(r"\bDWG[-_ ]?(\d+)\b", re.IGNORECASE)),
]


def infer_document_from_filename(filename: str):
    """
    Infer document type and document number from filename.

    This is filename evidence only, not final truth.
    """

    stem = Path(filename).stem

    for document_type, pattern in DOCUMENT_PATTERNS:
        match = pattern.search(stem)

        if match:
            prefix = {
                "invoice": "INV",
                "quotation": "QUO",
                "purchase_order": "PO",
                "delivery_note": "DN",
                "drawing": "DWG",
            }[document_type]

            document_number = f"{prefix}-{match.group(1)}"

            return {
                "document_type": document_type,
                "document_number": document_number,
                "confidence": 0.85,
            }

    return None


def get_or_create_document(
    conn: sqlite3.Connection,
    document_type: str,
    document_number: str,
):
    """
    Return an existing logical document if one already exists,
    otherwise create it.
    """

    cursor = conn.cursor()

    canonical_key = f"{document_type}:{document_number}".lower()

    cursor.execute(
        """
        SELECT id
        FROM documents
        WHERE canonical_key = ?
        """,
        (canonical_key,),
    )

    row = cursor.fetchone()

    if row:
        return row[0], False

    cursor.execute(
        """
        INSERT INTO documents (
            document_type,
            document_number,
            canonical_key,
            extraction_confidence,
            resolution_status
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            document_type,
            document_number,
            canonical_key,
            0.85,
            "filename_inferred",
        ),
    )

    return cursor.lastrowid, True


def link_source_to_document(
    conn: sqlite3.Connection,
    document_id: int,
    source_file_id: int,
):
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT OR IGNORE INTO document_sources (
            document_id,
            source_file_id,
            source_role
        )
        VALUES (?, ?, ?)
        """,
        (
            document_id,
            source_file_id,
            "filename_inferred",
        ),
    )


def add_filename_evidence(
    conn: sqlite3.Connection,
    document_id: int,
    source_file_id: int,
    document_number: str,
):
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO evidence (
            subject_type,
            subject_id,
            source_file_id,
            evidence_type,
            observed_value,
            context,
            confidence
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "document",
            document_id,
            source_file_id,
            "filename_document_number",
            document_number,
            "Document number inferred from filename.",
            0.85,
        ),
    )


def build_documents():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            id,
            filename,
            sha256
        FROM source_files
        ORDER BY id
        """
    )

    source_files = cursor.fetchall()

    inferred_count = 0
    created_count = 0
    linked_count = 0
    unknown_count = 0

    for source_file_id, filename, sha256 in source_files:
        result = infer_document_from_filename(filename)

        if not result:
            unknown_count += 1
            continue

        inferred_count += 1

        document_id, created = get_or_create_document(
            conn,
            result["document_type"],
            result["document_number"],
        )

        if created:
            created_count += 1

        link_source_to_document(
            conn,
            document_id,
            source_file_id,
        )

        add_filename_evidence(
            conn,
            document_id,
            source_file_id,
            result["document_number"],
        )

        linked_count += 1

    conn.commit()

    print("=" * 70)
    print("LOGICAL DOCUMENT BUILD")
    print("=" * 70)

    print(f"Source files checked:      {len(source_files)}")
    print(f"Filename-inferred docs:    {inferred_count}")
    print(f"Logical docs created:      {created_count}")
    print(f"Source-document links:     {linked_count}")
    print(f"No filename pattern:       {unknown_count}")

    conn.close()

def inspect_unmatched_filenames(limit=100):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT filename, extension
        FROM source_files
        ORDER BY filename
    """)

    rows = cursor.fetchall()

    unmatched = []

    for filename, extension in rows:
        if not infer_document_from_filename(filename):
            unmatched.append((filename, extension))

    print("=" * 70)
    print("UNMATCHED FILENAME SAMPLE")
    print("=" * 70)

    print(f"Total unmatched: {len(unmatched)}")
    print()

    for filename, extension in unmatched[:limit]:
        print(f"{extension or '[none]':8} | {filename}")

    conn.close()





def inspect_unmatched_filenames(limit=100):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    cursor.execute("""
         SELECT filename, extension
         FROM source_files
         ORDER BY filename
     """)

    rows = cursor.fetchall()

    unmatched = []

    for filename, extension in rows:
        if not infer_document_from_filename(filename):
            unmatched.append((filename, extension))

    print("=" * 70)
    print("UNMATCHED FILENAME SAMPLE")
    print("=" * 70)

    print(f"Total unmatched: {len(unmatched)}")
    print()

    for filename, extension in unmatched[:limit]:
        print(f"{extension or '[none]':8} | {filename}")

    conn.close()

if __name__ == "__main__":
    build_documents()