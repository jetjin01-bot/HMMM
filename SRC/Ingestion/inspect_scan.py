import sqlite3
from collections import Counter

from SRC.database import DATABASE_PATH


def inspect_scan_results():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # 1. 总文件数
    cursor.execute("""
        SELECT COUNT(*)
        FROM source_files
    """)
    total_files = cursor.fetchone()[0]

    # 2. 文件类型分布
    cursor.execute("""
        SELECT extension, COUNT(*)
        FROM source_files
        GROUP BY extension
        ORDER BY COUNT(*) DESC
    """)
    extension_counts = cursor.fetchall()

    # 3. 找 SHA-256 重复
    cursor.execute("""
        SELECT sha256, COUNT(*) AS file_count
        FROM source_files
        WHERE sha256 IS NOT NULL
        GROUP BY sha256
        HAVING COUNT(*) > 1
        ORDER BY file_count DESC
    """)
    duplicate_groups = cursor.fetchall()

    print("=" * 70)
    print("SCAN INSPECTION")
    print("=" * 70)

    print(f"\nTotal source files: {total_files}")

    print("\nTop file extensions:")
    for extension, count in extension_counts[:15]:
        print(f"{extension or '[no extension]':15} {count}")

    print("\nDuplicate hash groups:")
    print(f"Groups with duplicate content: {len(duplicate_groups)}")

    duplicate_file_count = sum(count for _, count in duplicate_groups)

    print(
        f"Files participating in duplicate groups: "
        f"{duplicate_file_count}"
    )

    print("\nTop duplicate groups:")

    for sha256, file_count in duplicate_groups[:10]:
        print("-" * 70)
        print(f"SHA-256: {sha256}")
        print(f"File count: {file_count}")

        cursor.execute("""
            SELECT file_path
            FROM source_files
            WHERE sha256 = ?
            ORDER BY file_path
        """, (sha256,))

        paths = cursor.fetchall()

        for path_tuple in paths:
            print(f"  {path_tuple[0]}")

    conn.close()


if __name__ == "__main__":
    inspect_scan_results()
