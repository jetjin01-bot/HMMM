"""Shared conflict persistence; callers own the transaction."""
import json

def insert_conflict(conn, source_file_id, conflict_type, description):
    # Acquire SQLite's writer lock before checking, including on legacy databases.
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    existing = conn.execute("SELECT id FROM conflicts WHERE source_file_id IS ? AND conflict_type=? AND description=?", (source_file_id, conflict_type, description)).fetchone()
    if existing:
        return False
    conn.execute("INSERT INTO conflicts (source_file_id, conflict_type, description, subject_type, subject_id, severity, status) VALUES (?, ?, ?, 'source_file', ?, 'warning', 'open')", (source_file_id, conflict_type, description, source_file_id))
    return True

def record_extraction_failure(conn, source_file_id, error):
    return insert_conflict(conn, source_file_id, 'extraction_failure', str(error))

def record_reference(conn, source_file_id, reference_type, reference_number, contexts):
    description = json.dumps(dict(reference_type=reference_type, reference_number=reference_number, contexts=sorted(set(contexts))), ensure_ascii=False, sort_keys=True)
    return insert_conflict(conn, source_file_id, 'unresolved_document_reference', description)

def audit_extractions():
    import sqlite3
    from pathlib import Path
    from SRC.database import DATABASE_PATH
    from SRC.Ingestion.extraction.content_extractor import extract_content, ExtractionFailure
    with sqlite3.connect(DATABASE_PATH) as conn:
        for source_id, path in conn.execute("SELECT id, file_path FROM source_files WHERE LOWER(extension) IN ('.pdf', '.eml') ORDER BY id").fetchall():
            try:
                extract_content(Path(path))
            except ExtractionFailure as exc:
                record_extraction_failure(conn, source_id, exc)
