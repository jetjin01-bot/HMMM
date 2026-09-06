from SRC.data_quality import insert_conflict
import sqlite3
from pathlib import Path

from SRC.database import DATABASE_PATH
from SRC.Ingestion.documents.document_builder import infer_document_from_filename
from SRC.Ingestion.extraction.document_parser import parse_document


def get_source_file_id(conn, file_path: str):
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id
        FROM source_files
        WHERE file_path = ?
        """,
        (file_path,),
    )

    row = cursor.fetchone()

    return row[0] if row else None


def get_or_create_document(
    conn,
    document_type: str,
    document_number: str,
    confidence: float = 0.95,
):
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
        return row[0]

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
            confidence,
            "content_verified",
        ),
    )

    return cursor.lastrowid


def add_conflict(
    conn,
    source_file_id: int,
    filename_number: str,
    content_number: str,
):
    description = (
        f"Filename suggests document number {filename_number}, "
        f"but extracted content indicates {content_number}."
    )
    return insert_conflict(conn, source_file_id, 'filename_content_mismatch', description)


def unlink_source_from_document(
    conn,
    source_file_id: int,
    document_id: int,
):
    cursor = conn.cursor()

    cursor.execute(
        """
        DELETE FROM document_sources
        WHERE source_file_id = ?
          AND document_id = ?
        """,
        (
            source_file_id,
            document_id,
        ),
    )


def link_source_to_document(
    conn,
    source_file_id: int,
    document_id: int,
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
            "content_verified",
        ),
    )


def resolve_filename_content_conflict(file_path: Path):
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")

    source_file_id = get_source_file_id(conn, str(file_path))

    if source_file_id is None:
        raise ValueError(
            f"Source file not found in database: {file_path}"
        )

    filename_identity = infer_document_from_filename(file_path.name)
    parsed = parse_document(file_path)
    content_identity = parsed["identity"]

    print("=" * 70)
    print("FILENAME / CONTENT COMPARISON")
    print("=" * 70)

    print("Filename identity:", filename_identity)
    print("Content identity:", content_identity)

    if not filename_identity or not content_identity:
        print("\nNot enough evidence to compare.")
        conn.close()
        return

    filename_number = filename_identity["document_number"]
    content_number = content_identity["document_number"]

    if filename_number == content_number:
        print("\nNo conflict detected.")
        conn.close()
        return

    print("\nCONFLICT DETECTED")

    add_conflict(
        conn,
        source_file_id,
        filename_number,
        content_number,
    )

    # Find the provisional filename-derived document
    cursor = conn.cursor()

    filename_key = (
        f"{filename_identity['document_type']}:{filename_number}".lower()
    )

    cursor.execute(
        """
        SELECT id
        FROM documents
        WHERE canonical_key = ?
        """,
        (filename_key,),
    )

    row = cursor.fetchone()

    if row:
        provisional_document_id = row[0]

        unlink_source_from_document(
            conn,
            source_file_id,
            provisional_document_id,
        )

    # Create or locate the content-verified document
    verified_document_id = get_or_create_document(
        conn,
        content_identity["document_type"],
        content_number,
        content_identity["confidence"],
    )

    link_source_to_document(
        conn,
        source_file_id,
        verified_document_id,
    )

    conn.commit()
    conn.close()

    print(
        f"\nSource file relinked from "
        f"{filename_number} to {content_number}"
    )


if __name__ == "__main__":
    test_path = Path(
        r"C:\Users\Administrator\Downloads\takehome\Customers\Falcon Aerospace Components Ltd\JOB-2026-0026 Control Panel Replacement\Invoices\INV-8189_v2.pdf"
    )

    resolve_filename_content_conflict(test_path)