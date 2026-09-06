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

    name = name.replace("&", " and ")

    name = re.sub(r"[^\w\s]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()

    parts = name.split()

    while parts and parts[-1] in LEGAL_SUFFIXES:
        parts.pop()

    return " ".join(parts)


def get_acronym(name: str) -> str:
    normalized = normalize_company_name(name)

    words = [
        word
        for word in normalized.split()
        if word not in {"and", "the"}
    ]

    return "".join(
        word[0]
        for word in words
        if word
    )


def calculate_name_score(
    observed_name: str,
    canonical_name: str,
) -> float:

    observed = normalize_company_name(observed_name)
    canonical = normalize_company_name(canonical_name)

    if observed == canonical:
        return 100.0

    ratio = fuzz.ratio(
        observed,
        canonical,
    )

    token_ratio = fuzz.token_set_ratio(
        observed,
        canonical,
    )

    score = max(
        ratio,
        token_ratio,
    )

    # Acronym support:
    # BFG -> Blenheim Foods Group
    observed_compact = re.sub(
        r"[^a-z0-9]",
        "",
        observed,
    )

    canonical_acronym = get_acronym(
        canonical_name
    ).lower()

    if (
        observed_compact
        and canonical_acronym
        and observed_compact == canonical_acronym
    ):
        score = max(
            score,
            95.0,
        )

    return score


def determine_resolution_decision(
    observed_name: str,
    canonical_name: str,
    name_score: float,
    runner_up_score: float,
    folder_match: bool,
):

    normalized_observed = normalize_company_name(
        observed_name
    )

    normalized_canonical = normalize_company_name(
        canonical_name
    )

    margin = (
        name_score
        - runner_up_score
    )

    # ----------------------------------------------------------
    # Rule 1: deterministic normalized exact match
    # ----------------------------------------------------------
    if normalized_observed == normalized_canonical:
        return (
            "auto_resolved",
            "Normalized company names match exactly.",
        )

    # ----------------------------------------------------------
    # Rule 2: strong name evidence + supporting folder context
    # ----------------------------------------------------------
    if (
            folder_match
            and name_score >= 70
            and margin >= 20
    ):
        return (
            "auto_resolved",
            (
                "Company name similarity is supported by "
                "matching customer folder context and a clear "
                "margin over the next-best candidate."
            ),
        )

    # ----------------------------------------------------------
    # Rule 3: very strong isolated name match
    # ----------------------------------------------------------
    if (
        name_score >= 92
        and margin >= 15
    ):
        return (
            "probable",
            (
                "Very strong name similarity with a clear "
                "margin over the next-best candidate, but "
                "without supporting folder evidence."
            ),
        )

    # ----------------------------------------------------------
    # Otherwise require review
    # ----------------------------------------------------------
    return (
        "review",
        (
            "Available evidence is insufficient for an "
            "automatic entity merge."
        ),
    )


def resolve_company_mentions():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")

    cursor = conn.cursor()

    # ----------------------------------------------------------
    # Existing canonical companies
    # ----------------------------------------------------------
    cursor.execute("""
        SELECT
            id,
            canonical_name
        FROM companies
        ORDER BY id
    """)

    canonical_companies = cursor.fetchall()

    # ----------------------------------------------------------
    # Only unresolved content-derived company mentions
    # ----------------------------------------------------------
    cursor.execute("""
        SELECT
            em.id,
            em.observed_value,
            em.source_file_id,
            sf.folder_customer
        FROM entity_mentions em
        JOIN source_files sf
          ON sf.id = em.source_file_id
        WHERE em.entity_type = 'company'
          AND em.extraction_method = 'labelled_pdf_field'
          AND em.resolution_status = 'unresolved'
        ORDER BY em.id
    """)

    mentions = cursor.fetchall()

    auto_count = 0
    probable_count = 0
    review_count = 0

    for (
        mention_id,
        observed_value,
        source_file_id,
        folder_customer,
    ) in mentions:

        candidates = []

        for company_id, canonical_name in canonical_companies:

            name_score = calculate_name_score(
                observed_value,
                canonical_name,
            )

            folder_match = False

            if folder_customer:
                folder_match = (
                    normalize_company_name(
                        folder_customer
                    )
                    ==
                    normalize_company_name(
                        canonical_name
                    )
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
            key=lambda candidate: candidate["name_score"],
            reverse=True,
        )

        best = candidates[0]
        second = candidates[1]

        runner_up_score = second["name_score"]

        score_margin = (
            best["name_score"]
            - runner_up_score
        )

        decision, reason = determine_resolution_decision(
            observed_name=observed_value,
            canonical_name=best["canonical_name"],
            name_score=best["name_score"],
            runner_up_score=runner_up_score,
            folder_match=best["folder_match"],
        )

        # ------------------------------------------------------
        # Store explainable resolution candidate
        # ------------------------------------------------------
        cursor.execute("""
            INSERT INTO resolution_candidates (
                mention_id,
                candidate_entity_type,
                candidate_entity_id,
                name_score,
                runner_up_score,
                score_margin,
                folder_match,
                decision,
                decision_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            mention_id,
            "company",
            best["company_id"],
            best["name_score"],
            runner_up_score,
            score_margin,
            int(best["folder_match"]),
            decision,
            reason,
        ))

        # ------------------------------------------------------
        # Auto-resolved mentions
        # ------------------------------------------------------
        if decision == "auto_resolved":

            cursor.execute("""
                UPDATE entity_mentions
                SET
                    resolution_status = 'resolved',
                    resolved_entity_id = ?
                WHERE id = ?
            """, (
                best["company_id"],
                mention_id,
            ))

            # Store observed form as alias
            cursor.execute("""
                INSERT INTO aliases (
                    entity_type,
                    entity_id,
                    alias,
                    normalized_alias,
                    source_file_id,
                    confidence,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                "company",
                best["company_id"],
                observed_value,
                normalize_company_name(
                    observed_value
                ),
                source_file_id,
                best["name_score"] / 100,
                "confirmed",
            ))

            auto_count += 1

        elif decision == "probable":

            cursor.execute("""
                UPDATE entity_mentions
                SET resolution_status = 'probable'
                WHERE id = ?
            """, (
                mention_id,
            ))

            probable_count += 1

        else:

            cursor.execute("""
                UPDATE entity_mentions
                SET resolution_status = 'review'
                WHERE id = ?
            """, (
                mention_id,
            ))

            review_count += 1

    conn.commit()
    conn.close()

    print("=" * 70)
    print("COMPANY ENTITY RESOLUTION")
    print("=" * 70)

    print(f"Mentions evaluated:       {len(mentions)}")
    print(f"Auto-resolved:            {auto_count}")
    print(f"Probable:                 {probable_count}")
    print(f"Needs review:             {review_count}")


if __name__ == "__main__":
    resolve_company_mentions()