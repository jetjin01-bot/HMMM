from pathlib import Path
import sqlite3


PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATABASE_PATH = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "knowledge.db"
)

DATABASE_PATH.parent.mkdir(
    parents=True,
    exist_ok=True,
)


def get_connection():
    """Create and return a SQLite database connection."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")

    return conn


def initialize_database():
    """Create the database tables required by the application."""

    conn = get_connection()
    cursor = conn.cursor()

    # ------------------------------------------------------------------
    # 1. Physical source files
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS source_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            file_path TEXT NOT NULL UNIQUE,
            filename TEXT NOT NULL,
            extension TEXT,
            mime_type TEXT,
            sha256 TEXT,

            folder_customer TEXT,
            folder_project TEXT,
            folder_category TEXT,

            ingestion_status TEXT NOT NULL DEFAULT 'pending',

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # ------------------------------------------------------------------
    # 2. Logical documents
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            document_type TEXT NOT NULL,
            document_number TEXT,
            title TEXT,
            document_date TEXT,

            canonical_key TEXT,

            extraction_confidence REAL,
            resolution_status TEXT NOT NULL DEFAULT 'unresolved',

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # ------------------------------------------------------------------
    # 3. Connect physical files to logical documents
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS document_sources (
            document_id INTEGER NOT NULL,
            source_file_id INTEGER NOT NULL,

            source_role TEXT NOT NULL DEFAULT 'representation',

            PRIMARY KEY (document_id, source_file_id),

            FOREIGN KEY (document_id)
                REFERENCES documents(id),

            FOREIGN KEY (source_file_id)
                REFERENCES source_files(id)
        );
    """)

    # ------------------------------------------------------------------
    # 4. Companies
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            canonical_name TEXT NOT NULL,

            resolution_status TEXT NOT NULL DEFAULT 'resolved',
            confidence REAL,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # ------------------------------------------------------------------
    # 5. People
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            canonical_name TEXT NOT NULL,
            email TEXT,

            resolution_status TEXT NOT NULL DEFAULT 'resolved',
            confidence REAL,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # ------------------------------------------------------------------
    # 6. Projects
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            job_number TEXT,
            canonical_name TEXT NOT NULL,

            resolution_status TEXT NOT NULL DEFAULT 'resolved',
            confidence REAL,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # ------------------------------------------------------------------
    # 6.5. Entity mentions
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entity_mentions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            entity_type TEXT NOT NULL,
            observed_value TEXT NOT NULL,
            normalized_value TEXT,
            identifier_value TEXT,

            source_file_id INTEGER NOT NULL,

            role TEXT,
            extraction_method TEXT,
            confidence REAL,

            resolution_status TEXT NOT NULL DEFAULT 'unresolved',
            resolved_entity_id INTEGER,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (source_file_id)
                REFERENCES source_files(id)
        );
    """)

    # ------------------------------------------------------------------
    # 7. Entity aliases
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,

            alias TEXT NOT NULL,
            normalized_alias TEXT,

            source_file_id INTEGER,

            confidence REAL,
            status TEXT NOT NULL DEFAULT 'observed',

            FOREIGN KEY (source_file_id)
                REFERENCES source_files(id)
        );
    """)

    # ------------------------------------------------------------------
    # 8.1. Resolution candidates
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS resolution_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            mention_id INTEGER NOT NULL,

            candidate_entity_type TEXT NOT NULL,
            candidate_entity_id INTEGER NOT NULL,

            name_score REAL,
            runner_up_score REAL,
            score_margin REAL,

            folder_match INTEGER NOT NULL DEFAULT 0,

            decision TEXT NOT NULL,
            decision_reason TEXT,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (mention_id)
                REFERENCES entity_mentions(id)
        );
    """)

    # ------------------------------------------------------------------
    # 8.2. Relationships between resolved entities
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            source_type TEXT NOT NULL,
            source_id INTEGER NOT NULL,

            relationship_type TEXT NOT NULL,

            target_type TEXT NOT NULL,
            target_id INTEGER NOT NULL,

            confidence REAL,
            status TEXT NOT NULL DEFAULT 'observed',

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # ------------------------------------------------------------------
    # 9. Evidence supporting extracted/resolved knowledge
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            subject_type TEXT NOT NULL,
            subject_id INTEGER NOT NULL,

            source_file_id INTEGER NOT NULL,

            evidence_type TEXT NOT NULL,
            observed_value TEXT,
            context TEXT,

            confidence REAL,

            FOREIGN KEY (source_file_id)
                REFERENCES source_files(id)
        );
    """)

    # ------------------------------------------------------------------
    # 10. Conflicts and data-quality issues
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            conflict_type TEXT NOT NULL,

            subject_type TEXT,
            subject_id INTEGER,

            source_file_id INTEGER,

            description TEXT NOT NULL,

            severity TEXT NOT NULL DEFAULT 'warning',
            status TEXT NOT NULL DEFAULT 'open',

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (source_file_id)
                REFERENCES source_files(id)
        );
    """)

    conn.commit()
    conn.close()

    print(f"Database initialized successfully: {DATABASE_PATH}")


if __name__ == "__main__":
    initialize_database()