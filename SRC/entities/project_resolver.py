import sqlite3
import re

from SRC.database import DATABASE_PATH


PROJECT_PATTERN = re.compile(
    r"^(JOB-\d{4}-\d+)\s+(.+)$",
    re.IGNORECASE,
)


def resolve_projects():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id,
            observed_value
        FROM entity_mentions
        WHERE entity_type = 'project'
        ORDER BY id
    """)

    rows = cursor.fetchall()

    groups = {}

    for mention_id, observed_value in rows:
        match = PROJECT_PATTERN.match(observed_value)

        if not match:
            continue

        job_number = match.group(1).upper()
        project_name = match.group(2).strip()

        groups.setdefault(
            job_number,
            {
                "project_names": [],
                "mentions": [],
            }
        )

        groups[job_number]["project_names"].append(project_name)
        groups[job_number]["mentions"].append(mention_id)

    project_count = 0

    for job_number, data in groups.items():

        # Most frequent project name
        frequency = {}

        for name in data["project_names"]:
            frequency[name] = frequency.get(name, 0) + 1

        canonical_name = max(
            frequency,
            key=frequency.get,
        )

        cursor.execute("""
            INSERT INTO projects (
                job_number,
                canonical_name,
                resolution_status,
                confidence
            )
            VALUES (?, ?, ?, ?)
        """, (
            job_number,
            canonical_name,
            "resolved_deterministically",
            0.99,
        ))

        project_id = cursor.lastrowid
        project_count += 1

        for mention_id in data["mentions"]:
            cursor.execute("""
                UPDATE entity_mentions
                SET
                    resolution_status = 'resolved',
                    resolved_entity_id = ?
                WHERE id = ?
            """, (
                project_id,
                mention_id,
            ))

    conn.commit()
    conn.close()

    print("=" * 70)
    print("PROJECT RESOLUTION")
    print("=" * 70)

    print(f"Project mentions processed: {len(rows)}")
    print(f"Canonical projects created: {project_count}")


if __name__ == "__main__":
    resolve_projects()