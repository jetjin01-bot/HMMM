import re
import sqlite3
from pathlib import Path

from SRC.database import DATABASE_PATH
from SRC.Ingestion.extraction.content_extractor import extract_pdf_text
from SRC.entities.mention_extractor import insert_mention


LABEL_PATTERNS = {
    "customer": [
        re.compile(r"Customer\s*[:\-]\s*(.+)", re.IGNORECASE),
        re.compile(r"Customer\s+Name\s*[:\-]\s*(.+)", re.IGNORECASE),
    ],

    "bill_to": [
        re.compile(r"Bill\s+To\s*[:\-]?\s*(.+)", re.IGNORECASE),
    ],

    "ship_to": [
        re.compile(r"Ship\s+To\s*[:\-]?\s*(.+)", re.IGNORECASE),
    ],

    "supplier": [
        re.compile(r"Supplier\s*[:\-]\s*(.+)", re.IGNORECASE),
        re.compile(r"Vendor\s*[:\-]\s*(.+)", re.IGNORECASE),
    ],

    "issued_by": [
        re.compile(r"Issued\s+By\s*[:\-]?\s*(.+)", re.IGNORECASE),
    ],
}


def clean_company_candidate(value: str) -> str:
    """
    Clean a company-name candidate conservatively.
    """

    value = value.strip()

    # Keep only the first line.
    value = value.splitlines()[0].strip()

    # Remove excessive whitespace.
    value = re.sub(r"\s+", " ", value)

    return value


def extract_company_mentions_from_text(text: str):
    """
    Extract labelled company mentions from document text.
    """

    mentions = []

    if not text:
        return mentions

    for role, patterns in LABEL_PATTERNS.items():

        for pattern in patterns:
            for match in pattern.finditer(text):

                candidate = clean_company_candidate(
                    match.group(1)
                )

                if not candidate:
                    continue

                mentions.append(
                    {
                        "role": role,
                        "observed_value": candidate,
                        "confidence": 0.97,
                    }
                )

    return mentions


def extract_pdf_company_mentions():
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

    pdfs_checked = 0
    mentions_inserted = 0
    files_with_mentions = 0
    errors = 0

    for source_file_id, file_path_str, filename in rows:

        file_path = Path(file_path_str)

        try:
            result = extract_pdf_text(file_path)

            text = result.get("text", "")

            mentions = extract_company_mentions_from_text(text)

            pdfs_checked += 1

            if mentions:
                files_with_mentions += 1

            for mention in mentions:

                insert_mention(
                    conn=conn,
                    entity_type="company",
                    observed_value=mention["observed_value"],
                    source_file_id=source_file_id,
                    role=mention["role"],
                    extraction_method="labelled_pdf_field",
                    confidence=mention["confidence"],
                )

                mentions_inserted += 1

        except Exception as exc:
            errors += 1

            print(
                f"[ERROR] {filename}: "
                f"{type(exc).__name__}: {exc}"
            )

    conn.commit()
    conn.close()

    print("=" * 70)
    print("PDF COMPANY MENTION EXTRACTION")
    print("=" * 70)

    print(f"PDFs checked:            {pdfs_checked}")
    print(f"Files with mentions:     {files_with_mentions}")
    print(f"Company mentions added:  {mentions_inserted}")
    print(f"Errors:                  {errors}")


if __name__ == "__main__":
    extract_pdf_company_mentions()