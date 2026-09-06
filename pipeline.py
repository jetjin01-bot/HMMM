from SRC.data_quality import audit_extractions
from SRC.Ingestion.documents.pdf_document_resolver import build_verified_pdf_documents
from SRC.relationships.relationship_builder import (build_document_project_relationships, build_document_company_relationships, build_email_documents_and_relationships, build_document_reference_relationships)
from SRC.database import DATABASE_PATH
from SRC.database import initialize_database

from SRC.Ingestion.Scanner import scan_dataset
from SRC.Ingestion.documents.document_builder import build_documents
from SRC.Ingestion.extraction.batch_verifier import verify_filename_inferred_pdfs

from SRC.entities.mention_extractor import extract_folder_mentions
from SRC.entities.email_person_extractor import extract_email_person_mentions
from SRC.entities.company_mention_extractor import extract_pdf_company_mentions

from SRC.entities.company_resolver import resolve_companies
from SRC.entities.project_resolver import resolve_projects
from SRC.entities.person_resolver import resolve_people
from SRC.entities.company_resolution_engine import resolve_company_mentions

from SRC.relationships.relationship_builder import (
    build_company_project_relationships,
)


def run_pipeline():

    print("=" * 80)
    print("ENTITY RESOLUTION PIPELINE")
    print("=" * 80)

    # ----------------------------------------------------------
    # Reset derived database
    # ----------------------------------------------------------
    if DATABASE_PATH.exists():
        print("\nResetting existing knowledge database...")
        DATABASE_PATH.unlink()

    # ----------------------------------------------------------
    # 1. Database
    # ----------------------------------------------------------
    print("\n[1/11] Initialising database...")
    initialize_database()

    # ----------------------------------------------------------
    # 2. Source file ingestion
    # ----------------------------------------------------------
    print("\n[2/11] Scanning source files...")
    scan_dataset()
    audit_extractions()

    # ----------------------------------------------------------
    # 3. Logical documents
    # ----------------------------------------------------------
    print("\n[3/11] Building logical documents...")
    build_documents()

    # ----------------------------------------------------------
    # 4. Document verification
    # ----------------------------------------------------------
    print("\n[4/11] Verifying document identities...")
    verify_filename_inferred_pdfs()
    build_verified_pdf_documents()

    # ----------------------------------------------------------
    # 5. Trusted folder entity mentions
    # ----------------------------------------------------------
    print("\n[5/11] Extracting trusted folder entity mentions...")
    extract_folder_mentions()

    # ----------------------------------------------------------
    # 6. Canonical companies and projects
    # ----------------------------------------------------------
    print("\n[6/11] Resolving canonical companies and projects...")
    resolve_companies()
    resolve_projects()

    # ----------------------------------------------------------
    # 7. Email person mentions
    # ----------------------------------------------------------
    print("\n[7/11] Extracting email person mentions...")
    extract_email_person_mentions()

    # ----------------------------------------------------------
    # 8. Canonical people
    # ----------------------------------------------------------
    print("\n[8/11] Resolving people...")
    resolve_people()

    # ----------------------------------------------------------
    # 9. PDF company aliases
    # ----------------------------------------------------------
    print("\n[9/11] Extracting PDF company aliases...")
    extract_pdf_company_mentions()

    # ----------------------------------------------------------
    # 10. Resolve content-derived company mentions
    # ----------------------------------------------------------
    print("\n[10/11] Resolving content-derived company aliases...")
    resolve_company_mentions()

    # ----------------------------------------------------------
    # 11. Relationships
    # ----------------------------------------------------------
    print("\n[11/11] Building company-project relationships...")
    build_company_project_relationships()
    build_document_project_relationships()
    build_document_company_relationships()
    build_email_documents_and_relationships()
    build_document_reference_relationships()

    # ----------------------------------------------------------
    # Complete
    # ----------------------------------------------------------
    print()
    print("=" * 80)
    print("PIPELINE COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    run_pipeline()