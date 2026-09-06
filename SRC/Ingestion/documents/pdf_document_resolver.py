from SRC.data_quality import record_extraction_failure, record_reference
from SRC.Ingestion.extraction.content_extractor import ExtractionFailure
from SRC.Ingestion.extraction.conflict_detector import add_conflict
import sqlite3
from pathlib import Path

from SRC.database import DATABASE_PATH
from SRC.Ingestion.documents.document_builder import (
    infer_document_from_filename,
)
from SRC.Ingestion.extraction.content_extractor import (
    extract_pdf_text,
)
from SRC.Ingestion.extraction.document_parser import (
    extract_primary_document_identity,
)


# ============================================================
# HELPERS
# ============================================================

def normalise_document_number(value):
    if not value:
        return None

    return str(value).strip().upper().replace(" ", "-")


def get_or_create_document(
    conn,
    document_type,
    document_number,
    title=None,
    extraction_confidence=0.95,
    resolution_status="resolved",
):
    cursor = conn.cursor()

    document_number = normalise_document_number(
        document_number
    )

    canonical_key = (
        f"{document_type}:{document_number}".lower()
    )

    # First try canonical key
    cursor.execute(
        """
        SELECT id
        FROM documents
        WHERE LOWER(canonical_key) = ?
        LIMIT 1
        """,
        (canonical_key,),
    )

    row = cursor.fetchone()

    if row:
        return row[0], False

    # Backup lookup
    cursor.execute(
        """
        SELECT id
        FROM documents
        WHERE LOWER(document_type) = LOWER(?)
          AND UPPER(document_number) = UPPER(?)
        LIMIT 1
        """,
        (
            document_type,
            document_number,
        ),
    )

    row = cursor.fetchone()

    if row:
        return row[0], False

    cursor.execute(
        """
        INSERT INTO documents (
            document_type,
            document_number,
            title,
            canonical_key,
            extraction_confidence,
            resolution_status
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            document_type,
            document_number,
            title,
            canonical_key,
            extraction_confidence,
            resolution_status,
        ),
    )

    return cursor.lastrowid, True


def link_source_to_document(
    conn,
    source_file_id,
    document_id,
):
    cursor = conn.cursor()

    # A physical source file should represent one primary
    # logical document in this resolver.
    cursor.execute(
        """
        DELETE FROM document_sources
        WHERE source_file_id = ?
          AND document_id != ?
        """,
        (
            source_file_id,
            document_id,
        ),
    )

    cursor.execute(
        """
        SELECT 1
        FROM document_sources
        WHERE source_file_id = ?
          AND document_id = ?
        LIMIT 1
        """,
        (
            source_file_id,
            document_id,
        ),
    )

    if cursor.fetchone():
        return False

    cursor.execute(
        """
        INSERT INTO document_sources (
            document_id,
            source_file_id,
            source_role
        )
        VALUES (?, ?, ?)
        """,
        (
            document_id,
            source_file_id,
            "primary_representation",
        ),
    )

    return True


def add_filename_content_conflict(
    conn,
    source_file_id,
    filename_identity,
    content_identity,
):
    return add_conflict(conn, source_file_id,
                        normalise_document_number(filename_identity.get('document_number')),
                        normalise_document_number(content_identity.get('document_number')))


# ============================================================
# MAIN PDF DOCUMENT RESOLUTION
# ============================================================

def build_verified_pdf_documents():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            id,
            file_path,
            filename
        FROM source_files
        WHERE LOWER(extension) = '.pdf'
        ORDER BY id
        """
    )

    rows = cursor.fetchall()

    pdfs_checked = 0
    content_resolved = 0
    filename_resolved = 0
    documents_created = 0
    source_links_created = 0
    conflicts_recorded = 0
    unresolved = 0
    errors = 0

    examples = []

    for source_file_id, file_path_str, filename in rows:
        file_path = Path(file_path_str)

        try:
            pdfs_checked += 1

            # ------------------------------------------------
            # 1. Filename identity
            # ------------------------------------------------
            filename_identity = (
                infer_document_from_filename(filename)
            )

            if filename_identity:
                expected_type = filename_identity.get(
                    "document_type"
                )
            else:
                expected_type = None

            # ------------------------------------------------
            # 2. Content extraction
            # ------------------------------------------------
            result = extract_pdf_text(file_path)

            text = result.get("text", "") or ""

            content_identity = None

            if text.strip():
                content_identity = (
                    extract_primary_document_identity(
                        text,
                        expected_type=expected_type,
                    )
                )

            # ------------------------------------------------
            # 3. Decide final identity
            # ------------------------------------------------
            final_identity = None
            resolution_method = None
            confidence = None

            if content_identity:
                content_type = content_identity.get(
                    "document_type"
                )

                content_number = normalise_document_number(
                    content_identity.get(
                        "document_number"
                    )
                )

                extraction_method = (
                    content_identity.get(
                        "extraction_method"
                    )
                    or content_identity.get("method")
                    or ""
                )

                content_confidence = (
                    content_identity.get("confidence")
                    or 0.95
                )

                # Strong labelled content wins.
                if (
                    content_type
                    and content_number
                    and (
                        extraction_method
                        == "labelled_primary_field"
                        or content_confidence >= 0.95
                    )
                ):
                    final_identity = {
                        "document_type": content_type,
                        "document_number": content_number,
                    }

                    resolution_method = "content"
                    confidence = content_confidence

            # ------------------------------------------------
            # 4. If strong content unavailable, use filename
            # ------------------------------------------------
            if final_identity is None and filename_identity:

                filename_type = filename_identity.get(
                    "document_type"
                )

                filename_number = normalise_document_number(
                    filename_identity.get(
                        "document_number"
                    )
                )

                if filename_type and filename_number:
                    final_identity = {
                        "document_type": filename_type,
                        "document_number": filename_number,
                    }

                    resolution_method = "filename"
                    confidence = (
                        filename_identity.get(
                            "confidence"
                        )
                        or 0.85
                    )

            # ------------------------------------------------
            # 5. Still unresolved
            # ------------------------------------------------
            if final_identity is None:
                unresolved += 1
                continue

            final_type = final_identity[
                "document_type"
            ]

            final_number = final_identity[
                "document_number"
            ]

            # ------------------------------------------------
            # 6. Record filename/content disagreement
            # ------------------------------------------------
            if filename_identity and content_identity:

                filename_type = filename_identity.get(
                    "document_type"
                )

                filename_number = normalise_document_number(
                    filename_identity.get(
                        "document_number"
                    )
                )

                content_type = content_identity.get(
                    "document_type"
                )

                content_number = normalise_document_number(
                    content_identity.get(
                        "document_number"
                    )
                )

                if (
                    filename_type
                    and filename_number
                    and content_type
                    and content_number
                    and (
                        filename_type != content_type
                        or filename_number != content_number
                    )
                ):
                    recorded = (
                        add_filename_content_conflict(
                            conn,
                            source_file_id,
                            filename_identity,
                            content_identity,
                        )
                    )

                    if recorded:
                        conflicts_recorded += 1

            # ------------------------------------------------
            # 7. Create/fetch logical document
            # ------------------------------------------------
            document_id, created = get_or_create_document(
                conn=conn,
                document_type=final_type,
                document_number=final_number,
                title=filename,
                extraction_confidence=confidence,
                resolution_status=(
                    "content_verified"
                    if resolution_method == "content"
                    else "filename_resolved"
                ),
            )

            if created:
                documents_created += 1

            # ------------------------------------------------
            # 8. Link physical source → logical document
            # ------------------------------------------------
            linked = link_source_to_document(
                conn,
                source_file_id,
                document_id,
            )

            if linked:
                source_links_created += 1

            if resolution_method == "content":
                content_resolved += 1

            elif resolution_method == "filename":
                filename_resolved += 1

            # Keep a few useful examples
            if len(examples) < 20:
                examples.append(
                    (
                        filename,
                        final_type,
                        final_number,
                        resolution_method,
                    )
                )

        except Exception as exc:
            if isinstance(exc, ExtractionFailure):
                record_extraction_failure(conn, source_file_id, exc)
            elif isinstance(exc, sqlite3.Error):
                raise
            errors += 1

            print(
                f"[ERROR] {filename}: "
                f"{type(exc).__name__}: {exc}"
            )

    conn.commit()
    conn.close()

    print("=" * 70)
    print("VERIFIED PDF DOCUMENT RESOLUTION")
    print("=" * 70)

    print(
        f"PDFs checked:              {pdfs_checked}"
    )

    print(
        f"Resolved by content:       {content_resolved}"
    )

    print(
        f"Resolved by filename:      {filename_resolved}"
    )

    print(
        f"Logical documents created: {documents_created}"
    )

    print(
        f"Source links created:      {source_links_created}"
    )

    print(
        f"Conflicts recorded:        {conflicts_recorded}"
    )

    print(
        f"Unresolved PDFs:           {unresolved}"
    )

    print(
        f"Errors:                    {errors}"
    )

    if examples:
        print()
        print("RESOLUTION EXAMPLES")
        print("-" * 70)

        for (
            filename,
            document_type,
            document_number,
            method,
        ) in examples:

            print(
                f"{filename} | "
                f"{document_type} | "
                f"{document_number} | "
                f"{method}"
            )


if __name__ == "__main__":
    build_verified_pdf_documents()