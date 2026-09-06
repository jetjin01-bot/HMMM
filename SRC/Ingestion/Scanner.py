from pathlib import Path
import hashlib
import mimetypes
import sqlite3

from SRC.database import DATABASE_PATH

DATASET_ROOT = Path(
    r"C:\Users\Administrator\Downloads\takehome"
)

IGNORED_FILENAMES = {
    ".DS_Store",
    ".gitignore",
    ".env",
    ".env.example",
}

IGNORED_PREFIXES = (
    "._",
)

def should_ignore_file(file_path: Path) -> bool:
    """Return True for metadata or project-control files."""

    if file_path.name in IGNORED_FILENAMES:
        return True

    if file_path.name.startswith(IGNORED_PREFIXES):
        return True

    return False

def calculate_sha256(file_path: Path, chunk_size: int = 1024 * 1024) -> str:
    """
    Calculate SHA-256 hash for a file.

    Reading in chunks avoids loading large files fully into memory.
    """
    sha256 = hashlib.sha256()

    with file_path.open("rb") as file:
        while chunk := file.read(chunk_size):
            sha256.update(chunk)

    return sha256.hexdigest()


def infer_folder_context(file_path: Path) -> dict:
    """
    Extract lightweight context from the folder structure.

    Folder information is treated as evidence/context only,
    not as canonical truth.
    """

    try:
        relative_path = file_path.relative_to(DATASET_ROOT)
    except ValueError:
        relative_path = file_path

    parts = relative_path.parts

    context = {
        "folder_customer": None,
        "folder_project": None,
        "folder_category": None,
    }

    # Expected pattern for customer files:
    #
    # Customers/
    #   Falcon Aerospace Components Ltd/
    #       JOB-2026-0026 Control Panel Replacement/
    #           Invoices/
    #               INV-8357.pdf

    if len(parts) >= 2 and parts[0].lower() == "customers":
        context["folder_customer"] = parts[1]

    if len(parts) >= 3 and parts[0].lower() == "customers":
        if parts[2].upper().startswith("JOB-"):
            context["folder_project"] = parts[2]

    if len(parts) >= 4 and parts[0].lower() == "customers":
        context["folder_category"] = parts[3]

    return context


def scan_file(file_path: Path) -> dict:
    """
    Build a metadata record for one source file.
    """

    mime_type, _ = mimetypes.guess_type(file_path)

    folder_context = infer_folder_context(file_path)

    return {
        "file_path": str(file_path),
        "filename": file_path.name,
        "extension": file_path.suffix.lower(),
        "mime_type": mime_type,
        "sha256": calculate_sha256(file_path),
        "folder_customer": folder_context["folder_customer"],
        "folder_project": folder_context["folder_project"],
        "folder_category": folder_context["folder_category"],
        "ingestion_status": "scanned",
    }


def insert_source_file(conn: sqlite3.Connection, record: dict) -> bool:
    """
    Insert one source file into SQLite.

    Returns True if inserted.
    Returns False if the file path already exists.
    """

    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT OR IGNORE INTO source_files (
            file_path,
            filename,
            extension,
            mime_type,
            sha256,
            folder_customer,
            folder_project,
            folder_category,
            ingestion_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record["file_path"],
            record["filename"],
            record["extension"],
            record["mime_type"],
            record["sha256"],
            record["folder_customer"],
            record["folder_project"],
            record["folder_category"],
            record["ingestion_status"],
        ),
    )

    return cursor.rowcount > 0


def scan_dataset():
    """
    Scan the full dataset and write source-file metadata to SQLite.
    """

    if not DATASET_ROOT.exists():
        raise FileNotFoundError(
            f"Dataset folder does not exist: {DATASET_ROOT}"
        )

    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")

    scanned_count = 0
    inserted_count = 0
    skipped_count = 0
    error_count = 0

    files = [
        path
        for path in DATASET_ROOT.rglob("*")
        if path.is_file() and not should_ignore_file(path)
    ]

    print(f"Dataset root: {DATASET_ROOT}")
    print(f"Files discovered: {len(files)}")
    print("-" * 60)

    for index, file_path in enumerate(files, start=1):
        try:
            record = scan_file(file_path)

            inserted = insert_source_file(conn, record)

            scanned_count += 1

            if inserted:
                inserted_count += 1
            else:
                skipped_count += 1

            if index % 100 == 0:
                print(
                    f"Processed {index}/{len(files)} files..."
                )

        except Exception as exc:
            error_count += 1

            print(
                f"[ERROR] {file_path}: "
                f"{type(exc).__name__}: {exc}"
            )

    conn.commit()
    conn.close()

    print("-" * 60)
    print("Scan complete.")
    print(f"Scanned:  {scanned_count}")
    print(f"Inserted: {inserted_count}")
    print(f"Skipped:  {skipped_count}")
    print(f"Errors:   {error_count}")


if __name__ == "__main__":
    scan_dataset()