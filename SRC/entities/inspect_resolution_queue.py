import sqlite3

from SRC.database import DATABASE_PATH


def inspect_resolution_queue():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            em.observed_value,
            c.canonical_name,
            MAX(rc.name_score) AS name_score,
            MAX(rc.runner_up_score) AS runner_up_score,
            MAX(rc.score_margin) AS score_margin,
            MAX(rc.folder_match) AS folder_match,
            rc.decision,
            rc.decision_reason,
            COUNT(*) AS occurrence_count
        FROM resolution_candidates rc

        JOIN entity_mentions em
          ON em.id = rc.mention_id

        JOIN companies c
          ON c.id = rc.candidate_entity_id

        WHERE rc.candidate_entity_type = 'company'
          AND rc.decision IN ('probable', 'review')

        GROUP BY
            em.observed_value,
            c.canonical_name,
            rc.decision,
            rc.decision_reason

        ORDER BY
            rc.decision,
            occurrence_count DESC
    """)

    rows = cursor.fetchall()

    probable_count = 0
    review_count = 0

    print("=" * 100)
    print("COMPANY RESOLUTION REVIEW QUEUE")
    print("=" * 100)

    for (
            observed_value,
            canonical_name,
            name_score,
            runner_up_score,
            score_margin,
            folder_match,
            decision,
            decision_reason,
            occurrence_count,
    ) in rows:

        if decision == "probable":
            probable_count += 1

        elif decision == "review":
            review_count += 1

        print()
        print("-" * 100)

        print(f"Decision:         {decision.upper()}")
        print(f"Observed value:   {observed_value}")
        print(f"Best candidate:   {canonical_name}")
        print(f"Name score:       {name_score:.1f}")
        print(f"Runner-up score:  {runner_up_score:.1f}")
        print(f"Score margin:     {score_margin:.1f}")
        print(f"Folder match:     {bool(folder_match)}")
        print(f"Reason:           {decision_reason}")
        print(f"Occurrences:      {occurrence_count}")
    print()
    print("=" * 100)
    print("SUMMARY")
    print("=" * 100)

    print(f"Probable matches: {probable_count}")
    print(f"Needs review:     {review_count}")
    print(f"Total queued:     {len(rows)}")

    conn.close()


if __name__ == "__main__":
    inspect_resolution_queue()