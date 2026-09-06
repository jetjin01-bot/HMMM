from SRC.data_quality import record_extraction_failure, record_reference
from SRC.Ingestion.extraction.content_extractor import ExtractionFailure
import re
import sqlite3
from pathlib import Path

from SRC.database import DATABASE_PATH
from SRC.Ingestion.extraction.content_extractor import (
    extract_eml_content,
    extract_pdf_text,
)


# ============================================================
# PATTERNS
# ============================================================

JOB_PATTERN = re.compile(
    r"\b(JOB-\d{4}-\d+)\b",
    re.IGNORECASE,
)


REFERENCE_PATTERNS = [
    (
        "quotation",
        re.compile(
            r"\bQuote\s*Ref\s*[:\-]?\s*(QUO[- ]?\d+)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "quotation",
        re.compile(
            r"\bQuotation\s*Ref\s*[:\-]?\s*(QUO[- ]?\d+)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "purchase_order",
        re.compile(
            r"\bPO\s*Ref\s*[:\-]?\s*(PO[- ]?\d+)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "purchase_order",
        re.compile(
            r"\bPurchase\s*Order\s*Ref\s*[:\-]?\s*(PO[- ]?\d+)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "invoice",
        re.compile(
            r"\bInvoice\s*Ref\s*[:\-]?\s*(INV[- ]?\d+)\b",
            re.IGNORECASE,
        ),
    ),
]


# ============================================================
# GENERIC RELATIONSHIP INSERT
# ============================================================

def insert_relationship(
    conn,
    source_type: str,
    source_id: int,
    relationship_type: str,
    target_type: str,
    target_id: int,
    confidence: float,
    status: str = "observed",
):
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id
        FROM relationships
        WHERE source_type = ?
          AND source_id = ?
          AND relationship_type = ?
          AND target_type = ?
          AND target_id = ?
        """,
        (
            source_type,
            source_id,
            relationship_type,
            target_type,
            target_id,
        ),
    )

    existing = cursor.fetchone()

    if existing:
        return False

    cursor.execute(
        """
        INSERT INTO relationships (
            source_type,
            source_id,
            relationship_type,
            target_type,
            target_id,
            confidence,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_type,
            source_id,
            relationship_type,
            target_type,
            target_id,
            confidence,
            status,
        ),
    )

    return True


# ============================================================
# COMPANY → PROJECT
# ============================================================

def build_company_project_relationships():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT DISTINCT
            company_mention.resolved_entity_id AS company_id,
            project_mention.resolved_entity_id AS project_id
        FROM entity_mentions company_mention

        JOIN entity_mentions project_mention
          ON company_mention.source_file_id = project_mention.source_file_id

        WHERE company_mention.entity_type = 'company'
          AND project_mention.entity_type = 'project'

          AND company_mention.extraction_method = 'folder_context'
          AND project_mention.extraction_method = 'folder_context'

          AND company_mention.resolution_status = 'resolved'
          AND project_mention.resolution_status = 'resolved'

          AND company_mention.resolved_entity_id IS NOT NULL
          AND project_mention.resolved_entity_id IS NOT NULL
        """
    )

    rows = cursor.fetchall()

    created = 0

    for company_id, project_id in rows:
        inserted = insert_relationship(
            conn=conn,
            source_type="company",
            source_id=company_id,
            relationship_type="HAS_PROJECT",
            target_type="project",
            target_id=project_id,
            confidence=0.99,
            status="observed",
        )

        if inserted:
            created += 1

    conn.commit()
    conn.close()

    print("=" * 70)
    print("COMPANY → PROJECT RELATIONSHIPS")
    print("=" * 70)
    print(f"Relationship candidates: {len(rows)}")
    print(f"Relationships created:   {created}")


# ============================================================
# DOCUMENT → PROJECT
# ============================================================

def build_document_project_relationships():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT DISTINCT
            ds.document_id,
            project_mention.resolved_entity_id AS project_id
        FROM document_sources ds

        JOIN entity_mentions project_mention
          ON project_mention.source_file_id = ds.source_file_id

        WHERE project_mention.entity_type = 'project'
          AND project_mention.extraction_method = 'folder_context'
          AND project_mention.resolution_status = 'resolved'
          AND project_mention.resolved_entity_id IS NOT NULL
        """
    )

    rows = cursor.fetchall()

    created = 0

    for document_id, project_id in rows:
        inserted = insert_relationship(
            conn=conn,
            source_type="document",
            source_id=document_id,
            relationship_type="RELATES_TO_PROJECT",
            target_type="project",
            target_id=project_id,
            confidence=0.98,
            status="observed",
        )

        if inserted:
            created += 1

    conn.commit()
    conn.close()

    print("=" * 70)
    print("DOCUMENT → PROJECT RELATIONSHIPS")
    print("=" * 70)
    print(f"Relationship candidates: {len(rows)}")
    print(f"Relationships created:   {created}")


# ============================================================
# DOCUMENT → COMPANY
# ============================================================

def build_document_company_relationships():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT DISTINCT
            ds.document_id,
            company_mention.resolved_entity_id AS company_id,
            company_mention.role
        FROM document_sources ds

        JOIN entity_mentions company_mention
          ON company_mention.source_file_id = ds.source_file_id

        WHERE company_mention.entity_type = 'company'
          AND company_mention.resolution_status = 'resolved'
          AND company_mention.resolved_entity_id IS NOT NULL

          AND company_mention.extraction_method IN (
              'folder_context',
              'labelled_pdf_field'
          )
        """
    )

    rows = cursor.fetchall()

    created = 0

    for document_id, company_id, role in rows:

        if role == "bill_to":
            relationship_type = "BILL_TO"

        elif role == "customer":
            relationship_type = "CUSTOMER"

        elif role == "supplier":
            relationship_type = "SUPPLIER"

        elif role == "ship_to":
            relationship_type = "SHIP_TO"

        elif role == "issued_by":
            relationship_type = "ISSUED_BY"

        elif role == "folder_customer":
            relationship_type = "RELATES_TO_COMPANY"

        else:
            relationship_type = "RELATES_TO_COMPANY"

        inserted = insert_relationship(
            conn=conn,
            source_type="document",
            source_id=document_id,
            relationship_type=relationship_type,
            target_type="company",
            target_id=company_id,
            confidence=0.97,
            status="observed",
        )

        if inserted:
            created += 1

    conn.commit()
    conn.close()

    print("=" * 70)
    print("DOCUMENT → COMPANY RELATIONSHIPS")
    print("=" * 70)
    print(f"Relationship candidates: {len(rows)}")
    print(f"Relationships created:   {created}")


# ============================================================
# EMAIL DOCUMENTS + PERSON / PROJECT RELATIONSHIPS
# ============================================================

def build_email_documents_and_relationships():
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
        WHERE LOWER(extension) = '.eml'
        ORDER BY id
        """
    )

    rows = cursor.fetchall()

    documents_created = 0
    source_links_created = 0
    sender_relationships = 0
    recipient_relationships = 0
    cc_relationships = 0
    project_relationships = 0
    errors = 0

    for source_file_id, file_path_str, filename in rows:
        file_path = Path(file_path_str)

        try:
            email_data = extract_eml_content(file_path)

            subject = email_data.get("subject") or ""
            body = email_data.get("text") or ""

            canonical_key = f"correspondence:{source_file_id}"

            cursor.execute(
                """
                SELECT id
                FROM documents
                WHERE canonical_key = ?
                """,
                (canonical_key,),
            )

            existing_document = cursor.fetchone()

            if existing_document:
                document_id = existing_document[0]

            else:
                cursor.execute(
                    """
                    INSERT INTO documents (
                        document_type,
                        document_number,
                        title,
                        document_date,
                        canonical_key,
                        extraction_confidence,
                        resolution_status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "correspondence",
                        None,
                        subject if subject else filename,
                        email_data.get("date"),
                        canonical_key,
                        0.99,
                        "content_verified",
                    ),
                )

                document_id = cursor.lastrowid
                documents_created += 1

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
                    "email_source",
                ),
            )

            if cursor.rowcount > 0:
                source_links_created += 1

            cursor.execute(
                """
                SELECT
                    role,
                    resolved_entity_id
                FROM entity_mentions
                WHERE source_file_id = ?
                  AND entity_type = 'person'
                  AND resolution_status = 'resolved'
                  AND resolved_entity_id IS NOT NULL
                """,
                (source_file_id,),
            )

            person_mentions = cursor.fetchall()

            for role, person_id in person_mentions:

                if role == "sender":
                    relationship_type = "SENDER"

                elif role == "recipient":
                    relationship_type = "RECIPIENT"

                elif role == "cc":
                    relationship_type = "CC"

                else:
                    continue

                inserted = insert_relationship(
                    conn=conn,
                    source_type="document",
                    source_id=document_id,
                    relationship_type=relationship_type,
                    target_type="person",
                    target_id=person_id,
                    confidence=0.99,
                    status="observed",
                )

                if inserted:
                    if relationship_type == "SENDER":
                        sender_relationships += 1

                    elif relationship_type == "RECIPIENT":
                        recipient_relationships += 1

                    elif relationship_type == "CC":
                        cc_relationships += 1

            combined_text = f"{subject}\n{body}"

            job_numbers = {
                match.upper()
                for match in JOB_PATTERN.findall(combined_text)
            }

            for job_number in job_numbers:
                cursor.execute(
                    """
                    SELECT id
                    FROM projects
                    WHERE UPPER(job_number) = ?
                    """,
                    (job_number,),
                )

                project_row = cursor.fetchone()

                if not project_row:
                    continue

                project_id = project_row[0]

                inserted = insert_relationship(
                    conn=conn,
                    source_type="document",
                    source_id=document_id,
                    relationship_type="RELATES_TO_PROJECT",
                    target_type="project",
                    target_id=project_id,
                    confidence=0.98,
                    status="observed",
                )

                if inserted:
                    project_relationships += 1

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
    print("EMAIL DOCUMENTS + RELATIONSHIPS")
    print("=" * 70)
    print(f"EML files checked:               {len(rows)}")
    print(f"Correspondence docs created:     {documents_created}")
    print(f"Source links created:            {source_links_created}")
    print(f"Sender relationships created:    {sender_relationships}")
    print(f"Recipient relationships created: {recipient_relationships}")
    print(f"CC relationships created:        {cc_relationships}")
    print(f"Project relationships created:   {project_relationships}")
    print(f"Errors:                          {errors}")


# ============================================================
# DOCUMENT REFERENCE HELPERS
# ============================================================

def normalize_reference_number(value: str) -> str:
    value = value.upper().strip()
    value = re.sub(r"\s+", "-", value)
    return value


def get_source_document_id(cursor, source_file_id: int):
    cursor.execute(
        """
        SELECT document_id
        FROM document_sources
        WHERE source_file_id = ?
        ORDER BY document_id
        LIMIT 1
        """,
        (source_file_id,),
    )

    row = cursor.fetchone()

    if row:
        return row[0]

    return None


def get_target_document_id(
    cursor,
    reference_type: str,
    reference_number: str,
):
    cursor.execute(
        """
        SELECT id
        FROM documents
        WHERE LOWER(document_type) = LOWER(?)
          AND UPPER(document_number) = UPPER(?)
        ORDER BY id
        LIMIT 1
        """,
        (
            reference_type,
            reference_number,
        ),
    )

    row = cursor.fetchone()

    if row:
        return row[0]

    canonical_key = (
        f"{reference_type}:{reference_number}".lower()
    )

    cursor.execute(
        """
        SELECT id
        FROM documents
        WHERE LOWER(canonical_key) = ?
        ORDER BY id
        LIMIT 1
        """,
        (canonical_key,),
    )

    row = cursor.fetchone()

    if row:
        return row[0]

    return None


# ============================================================
# DOCUMENT → DOCUMENT REFERENCES
# ============================================================

def build_document_reference_relationships():
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
    references_found = 0
    relationships_created = 0

    source_documents_resolved = 0
    source_documents_unresolved = 0

    target_documents_resolved = 0
    target_documents_unresolved = 0

    self_references = 0
    already_existing = 0

    errors = 0

    self_reference_examples = []
    successful_examples = []
    unresolved_examples = []

    for source_file_id, file_path_str, filename in rows:
        file_path = Path(file_path_str)

        try:
            result = extract_pdf_text(file_path)
            text = result.get("text", "") or ""

            pdfs_checked += 1

            if not text.strip():
                continue

            observed_references = {}

            for reference_type, pattern in REFERENCE_PATTERNS:
                for match in pattern.finditer(text):
                    reference_number = normalize_reference_number(
                        match.group(1)
                    )

                    context = " ".join(text[max(0, match.start()-100):match.end()+100].split())
                    observed_references.setdefault((reference_type, reference_number), []).append(context)

            if not observed_references:
                continue

            source_document_id = get_source_document_id(
                cursor,
                source_file_id,
            )

            if source_document_id is None:
                source_documents_unresolved += 1
            else:
                source_documents_resolved += 1

            for reference_type, reference_number in sorted(observed_references):
                references_found += 1

                target_document_id = get_target_document_id(
                    cursor,
                    reference_type,
                    reference_number,
                )

                if target_document_id is None:
                    target_documents_unresolved += 1
                    record_reference(conn, source_file_id, reference_type, reference_number, observed_references[(reference_type, reference_number)])

                    if len(unresolved_examples) < 10:
                        unresolved_examples.append(
                            (
                                filename,
                                reference_number,
                                "target_not_resolved",
                            )
                        )

                    continue

                target_documents_resolved += 1

                if source_document_id is None:
                    if len(unresolved_examples) < 10:
                        unresolved_examples.append(
                            (
                                filename,
                                reference_number,
                                "source_not_resolved",
                            )
                        )

                    continue

                # ------------------------------------------------
                # Check self-reference
                # ------------------------------------------------
                if source_document_id == target_document_id:
                    self_references += 1

                    if len(self_reference_examples) < 20:
                        cursor.execute(
                            """
                            SELECT
                                document_type,
                                document_number
                            FROM documents
                            WHERE id = ?
                            """,
                            (source_document_id,),
                        )

                        source_info = cursor.fetchone()

                        self_reference_examples.append(
                            (
                                filename,
                                source_document_id,
                                source_info,
                                reference_type,
                                reference_number,
                            )
                        )

                    continue

                # ------------------------------------------------
                # Check whether relationship already exists
                # ------------------------------------------------
                cursor.execute(
                    """
                    SELECT id
                    FROM relationships
                    WHERE source_type = 'document'
                      AND source_id = ?
                      AND relationship_type = 'REFERENCES'
                      AND target_type = 'document'
                      AND target_id = ?
                    """,
                    (
                        source_document_id,
                        target_document_id,
                    ),
                )

                existing = cursor.fetchone()

                if existing:
                    already_existing += 1
                    continue

                # ------------------------------------------------
                # Create relationship
                # ------------------------------------------------
                inserted = insert_relationship(
                    conn=conn,
                    source_type="document",
                    source_id=source_document_id,
                    relationship_type="REFERENCES",
                    target_type="document",
                    target_id=target_document_id,
                    confidence=0.99,
                    status="observed",
                )

                if inserted:
                    relationships_created += 1

                    if len(successful_examples) < 20:
                        cursor.execute(
                            """
                            SELECT
                                document_type,
                                document_number
                            FROM documents
                            WHERE id = ?
                            """,
                            (source_document_id,),
                        )

                        source_info = cursor.fetchone()

                        cursor.execute(
                            """
                            SELECT
                                document_type,
                                document_number
                            FROM documents
                            WHERE id = ?
                            """,
                            (target_document_id,),
                        )

                        target_info = cursor.fetchone()

                        successful_examples.append(
                            (
                                filename,
                                source_info,
                                target_info,
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
    print("DOCUMENT → DOCUMENT REFERENCES")
    print("=" * 70)

    print(f"PDFs checked:                  {pdfs_checked}")
    print(f"References found:              {references_found}")
    print(f"Relationships created:         {relationships_created}")

    print(f"Source documents resolved:     {source_documents_resolved}")
    print(f"Source documents unresolved:   {source_documents_unresolved}")

    print(f"Target documents resolved:     {target_documents_resolved}")
    print(f"Target documents unresolved:   {target_documents_unresolved}")

    print(f"Self references skipped:       {self_references}")
    print(f"Already existing relations:    {already_existing}")

    print(f"Errors:                        {errors}")

    if self_reference_examples:
        print()
        print("SELF REFERENCE EXAMPLES")
        print("-" * 70)

        for example in self_reference_examples:
            print(example)

    if successful_examples:
        print()
        print("SUCCESSFUL REFERENCE EXAMPLES")
        print("-" * 70)

        for example in successful_examples:
            print(example)

    if unresolved_examples:
        print()
        print("UNRESOLVED EXAMPLES")
        print("-" * 70)

        for example in unresolved_examples:
            print(example)

# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    build_company_project_relationships()
    build_document_project_relationships()
    build_document_company_relationships()
    build_email_documents_and_relationships()
    build_document_reference_relationships()