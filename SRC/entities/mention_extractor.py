import re
import sqlite3

from SRC.database import DATABASE_PATH


PROJECT_PATTERN = re.compile(
    r"^(JOB-\d{4}-\d+)\s+(.+)$",
    re.IGNORECASE,
)


def normalize_text(value: str) -> str:
    """
    Basic normalization for matching.
    Keep this conservative.
    """

    value = value.strip().lower()

    value = re.sub(
        r"[^\w\s]",
        " ",
        value,
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip()


def insert_mention(
    conn,
    entity_type: str,
    observed_value: str,
    source_file_id: int,
    role: str,
    extraction_method: str,
    confidence: float,
    identifier_value: str | None = None,
):
    cursor = conn.cursor()

    normalized_value = normalize_text(observed_value)

    cursor.execute(
        """
        INSERT INTO entity_mentions (
            entity_type,
            observed_value,
            normalized_value,
            identifier_value,
            source_file_id,
            role,
            extraction_method,
            confidence,
            resolution_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity_type,
            observed_value,
            normalized_value,
            identifier_value,
            source_file_id,
            role,
            extraction_method,
            confidence,
            "unresolved",
        ),
    )


def extract_folder_mentions():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")

    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id,
            folder_customer,
            folder_project
        FROM source_files
        WHERE folder_customer IS NOT NULL
           OR folder_project IS NOT NULL
    """)

    rows = cursor.fetchall()

    company_mentions = 0
    project_mentions = 0

    for (
        source_file_id,
        folder_customer,
        folder_project,
    ) in rows:

        # ----------------------------------------------------------
        # Company mention
        # ----------------------------------------------------------
        if folder_customer:
            insert_mention(
                conn=conn,
                entity_type="company",
                observed_value=folder_customer,
                source_file_id=source_file_id,
                role="folder_customer",
                extraction_method="folder_context",
                confidence=0.90,
            )

            company_mentions += 1

        # ----------------------------------------------------------
        # Project mention
        # ----------------------------------------------------------
        if folder_project:
            match = PROJECT_PATTERN.match(folder_project)

            if match:
                job_number = match.group(1)
                project_name = match.group(2)

                insert_mention(
                    conn=conn,
                    entity_type="project",
                    observed_value=folder_project,
                    source_file_id=source_file_id,
                    role="folder_project",
                    extraction_method="folder_context",
                    confidence=0.95,
                )

                project_mentions += 1

    conn.commit()
    conn.close()

    print("=" * 70)
    print("FOLDER ENTITY MENTION EXTRACTION")
    print("=" * 70)

    print(f"Company mentions inserted: {company_mentions}")
    print(f"Project mentions inserted: {project_mentions}")


if __name__ == "__main__":
    extract_folder_mentions()