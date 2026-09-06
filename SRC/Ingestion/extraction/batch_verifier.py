from SRC.data_quality import record_extraction_failure, record_reference
from SRC.Ingestion.extraction.content_extractor import ExtractionFailure
import sqlite3
from pathlib import Path

from SRC.database import DATABASE_PATH
from SRC.Ingestion.documents.document_builder import infer_document_from_filename
from SRC.Ingestion.extraction.document_parser import parse_document
from SRC.Ingestion.extraction.conflict_detector import (
    get_or_create_document,
    add_conflict,
    unlink_source_from_document,
    link_source_to_document,
)


def verify_filename_inferred_pdfs():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")

    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            sf.id,
            sf.file_path,
            sf.filename
        FROM source_files sf
        WHERE LOWER(sf.extension) = '.pdf'
        ORDER BY sf.id
    """)

    rows = cursor.fetchall()

    checked = 0
    matched = 0
    conflicts = 0
    review_count = 0
    no_filename_identity = 0
    no_content_identity = 0
    errors = 0

    for source_file_id, file_path_str, filename in rows:

        filename_identity = infer_document_from_filename(filename)

        if not filename_identity:
            no_filename_identity += 1
            continue

        checked += 1

        file_path = Path(file_path_str)

        try:
            parsed = parse_document(
                file_path,
                expected_type=filename_identity["document_type"],
            )

            content_identity = parsed["identity"]

            if not content_identity:
                no_content_identity += 1
                continue

            filename_number = filename_identity["document_number"]
            filename_type = filename_identity["document_type"]

            content_number = content_identity["document_number"]
            content_type = content_identity["document_type"]

            content_method = content_identity.get("method")
            content_confidence = content_identity.get("confidence", 0)

            # ----------------------------------------------------------
            # Case 1: Filename and content agree
            # ----------------------------------------------------------
            if (
                filename_number == content_number
                and filename_type == content_type
            ):
                matched += 1

                canonical_key = (
                    f"{content_type}:{content_number}".lower()
                )

                cursor.execute("""
                    UPDATE documents
                    SET
                        resolution_status = 'content_verified',
                        extraction_confidence = ?
                    WHERE canonical_key = ?
                """, (
                    content_confidence,
                    canonical_key,
                ))

                cursor.execute("""
                    UPDATE document_sources
                    SET source_role = 'content_verified'
                    WHERE source_file_id = ?
                """, (
                    source_file_id,
                ))

                print(
                    f"[MATCH] {filename}: "
                    f"{filename_number}"
                )

                continue

            # ----------------------------------------------------------
            # Case 2: Filename and content disagree,
            # but content evidence is weak
            # ----------------------------------------------------------
            if content_method != "labelled_primary_field":
                review_count += 1

                print(
                    f"[REVIEW] {filename}: "
                    f"filename={filename_number}, "
                    f"weak content candidate={content_number}, "
                    f"method={content_method}, "
                    f"confidence={content_confidence}"
                )

                continue

            # ----------------------------------------------------------
            # Case 3: Strong labelled content evidence disagrees
            # with filename -> real conflict
            # ----------------------------------------------------------
            conflicts += 1

            add_conflict(
                conn,
                source_file_id,
                filename_number,
                content_number,
            )

            filename_key = (
                f"{filename_type}:{filename_number}".lower()
            )

            cursor.execute("""
                SELECT id
                FROM documents
                WHERE canonical_key = ?
            """, (
                filename_key,
            ))

            row = cursor.fetchone()

            if row:
                provisional_document_id = row[0]

                unlink_source_from_document(
                    conn,
                    source_file_id,
                    provisional_document_id,
                )

            verified_document_id = get_or_create_document(
                conn,
                content_type,
                content_number,
                content_confidence,
            )

            link_source_to_document(
                conn,
                source_file_id,
                verified_document_id,
            )

            print(
                f"[CONFLICT] {filename}: "
                f"{filename_number} -> {content_number} "
                f"| method={content_method} "
                f"| confidence={content_confidence}"
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

    print()
    print("=" * 70)
    print("BATCH DOCUMENT VERIFICATION")
    print("=" * 70)

    print(f"PDFs checked:               {checked}")
    print(f"Filename/content matches:   {matched}")
    print(f"Strong conflicts detected:  {conflicts}")
    print(f"Needs review / weak match:  {review_count}")
    print(f"No filename identity:       {no_filename_identity}")
    print(f"No content identity:        {no_content_identity}")
    print(f"Errors:                     {errors}")


if __name__ == "__main__":
    verify_filename_inferred_pdfs()