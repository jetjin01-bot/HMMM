import re
import sqlite3

from rapidfuzz import fuzz

from SRC.database import DATABASE_PATH


LEGAL_SUFFIXES = {
    "limited",
    "ltd",
    "plc",
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "company",
    "co",
    "llc",
}


def normalize_company_name(name: str) -> str:
    name = name.lower().strip()

    # Normalize "&" and "and"
    name = name.replace("&", " and ")

    # Remove punctuation
    name = re.sub(r"[^\w\s]", " ", name)

    # Normalize whitespace
    name = re.sub(r"\s+", " ", name).strip()

    parts = name.split()

    # Remove legal suffixes from the end
    while parts and parts[-1] in LEGAL_SUFFIXES:
        parts.pop()

    return " ".join(parts)


def get_acronym(name: str) -> str:
    """
    Blenheim Foods Group -> BFG
    """
    normalized = normalize_company_name(name)

    words = [
        word
        for word in normalized.split()
        if word not in {"and", "the"}
    ]

    return "".join(word[0] for word in words if word)


def calculate_name_score(
    observed_name: str,
    canonical_name: str,
) -> float:
    observed = normalize_company_name(observed_name)
    canonical = normalize_company_name(canonical_name)

    # Strong deterministic match
    if observed == canonical:
        return 100.0

    ratio = fuzz.ratio(observed, canonical)
    token_ratio = fuzz.token_set_ratio(observed, canonical)

    score = max(ratio, token_ratio)

    # Acronym support
    observed_compact = re.sub(
        r"[^a-z0-9]",
        "",
        observed.lower(),
    )

    canonical_acronym = get_acronym(canonical_name).lower()

    if (
        observed_compact
        and canonical_acronym
        and observed_compact == canonical_acronym
    ):
        score = max(score, 95.0)

    return score


def inspect_company_candidates():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # Existing canonical companies
    cursor.execute("""
        SELECT
            id,
            canonical_name
        FROM companies
        ORDER BY canonical_name
    """)

    canonical_companies = cursor.fetchall()

    # Content-derived company mentions only
    cursor.execute("""
        SELECT DISTINCT
            em.observed_value,
            sf.folder_customer
        FROM entity_mentions em
        JOIN source_files sf
          ON sf.id = em.source_file_id
        WHERE em.entity_type = 'company'
          AND em.extraction_method = 'labelled_pdf_field'
        ORDER BY em.observed_value
    """)

    mentions = cursor.fetchall()

    print("=" * 100)
    print("COMPANY RESOLUTION CANDIDATES")
    print("=" * 100)

    for observed_value, folder_customer in mentions:

        candidates = []

        for company_id, canonical_name in canonical_companies:
            name_score = calculate_name_score(
                observed_value,
                canonical_name,
            )

            folder_match = False

            if folder_customer:
                folder_match = (
                    normalize_company_name(folder_customer)
                    ==
                    normalize_company_name(canonical_name)
                )

            candidates.append(
                {
                    "company_id": company_id,
                    "canonical_name": canonical_name,
                    "name_score": name_score,
                    "folder_match": folder_match,
                }
            )

        candidates.sort(
            key=lambda item: (
                item["folder_match"],
                item["name_score"],
            ),
            reverse=True,
        )

        best = candidates[0]
        second = candidates[1]

        margin = (
            best["name_score"]
            - second["name_score"]
        )

        print()
        print(f"Observed:       {observed_value}")
        print(f"Folder context: {folder_customer}")
        print(
            f"Best candidate: {best['canonical_name']}"
        )
        print(
            f"Name score:     {best['name_score']:.1f}"
        )
        print(
            f"Folder match:   {best['folder_match']}"
        )
        print(
            f"Runner-up:      {second['canonical_name']} "
            f"({second['name_score']:.1f})"
        )
        print(
            f"Score margin:   {margin:.1f}"
        )

    conn.close()


if __name__ == "__main__":
    inspect_company_candidates()