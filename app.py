import sqlite3
import tempfile
from pathlib import Path

import networkx as nx
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from pyvis.network import Network

from SRC.database import DATABASE_PATH


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="Elgoog",
    page_icon="🧭",
    layout="wide",
)


# ============================================================
# DISPLAY MAPPINGS
# ============================================================

DECISION_LABELS = {
    "auto_resolved": "Automatically Resolved",
    "probable": "Probable Match",
    "review": "Manual Review Required",
}

STATUS_LABELS = {
    "resolved": "Resolved",
    "auto_resolved": "Automatically Resolved",
    "content_verified": "Content Verified",
    "filename_resolved": "Resolved from Filename",
    "unresolved": "Unresolved",
    "observed": "Observed",
    "open": "Open",
}

EXTRACTION_METHOD_LABELS = {
    "folder_context": "Folder Context",
    "labelled_pdf_field": "Labelled PDF Field",
    "email_header": "Email Header",
    "pymupdf_text": "PDF Text Extraction",
}

RELATIONSHIP_LABELS = {
    "HAS_PROJECT": "Has Project",
    "RELATES_TO_PROJECT": "Relates to Project",
    "RELATES_TO_COMPANY": "Relates to Company",
    "BILL_TO": "Bill To",
    "CUSTOMER": "Customer",
    "SUPPLIER": "Supplier",
    "SHIP_TO": "Ship To",
    "ISSUED_BY": "Issued By",
    "SENDER": "Sender",
    "RECIPIENT": "Recipient",
    "CC": "CC",
    "REFERENCES": "References",
}


# ============================================================
# GENERAL HELPERS
# ============================================================

def pretty_label(value, mapping=None):
    if value is None:
        return "-"

    if pd.isna(value):
        return "-"

    if mapping and value in mapping:
        return mapping[value]

    return str(value).replace("_", " ").title()


def display_status(value):
    return pretty_label(
        value,
        STATUS_LABELS,
    )


def confidence_display(value):
    if value is None or pd.isna(value):
        return "-"

    return f"{float(value):.1%}"


def shorten_text(value, max_length=36):
    if value is None or pd.isna(value):
        return "-"

    value = str(value).strip()

    if len(value) <= max_length:
        return value

    return value[: max_length - 1] + "…"


# ============================================================
# DATABASE HELPERS
# ============================================================

@st.cache_data
def run_query(query, params=None):
    conn = sqlite3.connect(DATABASE_PATH)

    try:
        df = pd.read_sql_query(
            query,
            conn,
            params=params or (),
        )

    finally:
        conn.close()

    return df


def get_scalar(query, params=None):
    df = run_query(
        query,
        params,
    )

    if df.empty:
        return 0

    return df.iloc[0, 0]



ENTITY_TABLES = {
    "company": "companies", "project": "projects",
    "person": "people", "document": "documents",
    "source_file": "source_files",
}


def text_value(value, default=""):
    return default if value is None or pd.isna(value) else str(value).strip()


def entity_record_label(entity_type, row):
    name = text_value(row.get("canonical_name"))
    if entity_type == "company":
        return name or "Unnamed Company"
    if entity_type == "project":
        return " — ".join(filter(None, [text_value(row.get("job_number")), name])) or "Unnamed Project"
    if entity_type == "person":
        email = text_value(row.get("email"))
        return f"{name} ({email})" if name and email else name or email or "Unnamed Person"
    if entity_type == "source_file":
        return text_value(row.get("filename"), "Unnamed Source File")
    number = text_value(row.get("document_number"))
    if number:
        return f"{pretty_label(row.get('document_type'))} {number}"
    return text_value(row.get("title")) or "Untitled Document"


def get_entity_display_name(entity_type, entity_id):
    entity_type = text_value(entity_type).lower()
    table = ENTITY_TABLES.get(entity_type)
    if entity_id is None or pd.isna(entity_id):
        return f"Unresolved {pretty_label(entity_type)}"
    if table is None:
        return f"Unknown {pretty_label(entity_type)}"
    rows = run_query(f'SELECT * FROM "{table}" WHERE id = ?', (int(entity_id),))
    if rows.empty:
        return f"Missing {pretty_label(entity_type)}"
    return entity_record_label(entity_type, rows.iloc[0])


def format_relationships(rows):
    columns = ["Source Type", "Source Entity", "Relationship", "Target Type",
               "Target Entity", "Confidence", "Status"]
    # Resolve names in batches, avoiding a database query for every endpoint.
    names = {}
    for side in ("source", "target"):
        for kind, group in rows.groupby(f"{side}_type"):
            table = ENTITY_TABLES.get(kind)
            if table is None:
                continue
            ids = list({int(value) for value in group[f"{side}_id"].dropna()})
            for offset in range(0, len(ids), 500):
                batch = ids[offset:offset + 500]
                marks = ",".join("?" for _ in batch)
                records = run_query(f'SELECT * FROM "{table}" WHERE id IN ({marks})', tuple(batch))
                for _, record in records.iterrows():
                    names[(kind, int(record["id"]))] = entity_record_label(kind, record)

    def endpoint(kind, value):
        if value is None or pd.isna(value):
            return f"Unresolved {pretty_label(kind)}"
        return names.get((kind, int(value)), f"Missing {pretty_label(kind)}")

    return pd.DataFrame([
        {"Source Type": pretty_label(r.source_type),
         "Source Entity": endpoint(r.source_type, r.source_id),
         "Relationship": pretty_label(r.relationship_type, RELATIONSHIP_LABELS),
         "Target Type": pretty_label(r.target_type),
         "Target Entity": endpoint(r.target_type, r.target_id),
         "Confidence": confidence_display(r.confidence),
         "Status": display_status(r.status)}
        for r in rows.itertuples(index=False)
    ], columns=columns)


def read_quality_tables():
    available = set(run_query("SELECT name FROM sqlite_master WHERE type = 'table'")["name"])
    return {name: run_query(f'SELECT * FROM "{name}"') if name in available else pd.DataFrame()
            for name in ("conflicts", "source_files", "documents", "relationships")}


def build_quality_groups(tables):
    """Classify recorded issues; never infer extraction failure from confidence alone.

    Each category counts records, not unique affected files. A source-file status
    already represented by a conflict in that category is not counted twice.
    Optional columns are inspected rather than assumed to exist.
    """
    groups = {name: [] for name in ("Extraction Failures", "Unresolved References", "Filename/Content Conflicts")}
    conflicts = tables["conflicts"]
    files = tables["source_files"]
    filenames = {r["id"]: text_value(r.get("filename")) for _, r in files.iterrows()}
    represented_files = {name: set() for name in groups}
    represented_subjects = {name: set() for name in groups}
    register = []
    extraction_types = {"extraction_failure", "extraction_failed", "extraction_error", "text_extraction_failure", "pdf_extraction_failure", "parse_error", "parsing_failure"}
    reference_types = {"unresolved_reference", "unresolved_document_reference", "missing_reference", "missing_referenced_document", "reference_not_found", "unresolved_document"}
    filename_types = {"filename_content_mismatch", "filename_content_conflict"}

    for _, r in conflicts.iterrows():
        kind = text_value(r.get("conflict_type")).lower()
        subject_type = text_value(r.get("subject_type"))
        subject_id = r.get("subject_id")
        detail = {"Conflict Type": pretty_label(kind),
                  "Subject Type": pretty_label(subject_type),
                  "Subject": get_entity_display_name(subject_type, subject_id) if subject_type else "-",
                  "Source File": filenames.get(r.get("source_file_id"), "-"),
                  "Description": text_value(r.get("description")),
                  "Severity": pretty_label(r.get("severity")),
                  "Status": display_status(r.get("status"))}
        register.append(detail)
        category = ("Extraction Failures" if kind in extraction_types else
                    "Unresolved References" if kind in reference_types else
                    "Filename/Content Conflicts" if kind in filename_types else None)
        if category:
            groups[category].append(detail)
            represented_files[category].add(r.get("source_file_id"))
            represented_subjects[category].add((subject_type, subject_id))

    failed_states = {"failed", "failure", "error", "extraction_failed", "extraction_failure", "parse_error"}
    for table, entity_type in (("source_files", "source_file"), ("documents", "document")):
        frame = tables[table]
        for _, r in frame.iterrows():
            states = [c for c in ("extraction_status", "parse_status", "processing_status", "status")
                      if c in frame and text_value(r.get(c)).lower() in failed_states]
            errors = [c for c in ("extraction_error", "parse_error", "error_message")
                      if c in frame and text_value(r.get(c))]
            if not states and not errors:
                continue
            category = "Extraction Failures"
            if (entity_type, r.get("id")) in represented_subjects[category]:
                continue
            if entity_type == "source_file" and r.get("id") in represented_files[category]:
                continue
            groups[category].append({"Subject Type": pretty_label(entity_type),
                "Subject": entity_record_label(entity_type, r),
                "Source File": text_value(r.get("filename"), "-"),
                "Description": "; ".join(f"{pretty_label(c)}: {r[c]}" for c in states + errors),
                "Status": display_status(r.get(states[0])) if states else "Error Recorded"})

    for _, r in tables["documents"].iterrows():
        if text_value(r.get("resolution_status")).lower() != "unresolved":
            continue
        if ("document", r.get("id")) in represented_subjects["Unresolved References"]:
            continue
        groups["Unresolved References"].append({"Subject Type": "Document",
            "Subject": entity_record_label("document", r),
            "Description": "Document resolution is explicitly marked unresolved.", "Status": "Unresolved"})

    for _, r in tables["relationships"].iterrows():
        if text_value(r.get("relationship_type")).upper() != "REFERENCES":
            continue
        status = text_value(r.get("status")).lower()
        target_id = r.get("target_id")
        target = get_entity_display_name(r.get("target_type"), target_id)
        if status not in {"unresolved", "missing", "not_found", "broken"} and not target.startswith(("Missing ", "Unresolved ")):
            continue
        if ("relationship", r.get("id")) in represented_subjects["Unresolved References"]:
            continue
        groups["Unresolved References"].append({"Subject Type": "Reference",
            "Subject": get_entity_display_name(r.get("source_type"), r.get("source_id")),
            "Target Entity": target, "Description": "Reference is unresolved or its target is unavailable.",
            "Status": display_status(status) if status else "Unresolved"})
    return {name: pd.DataFrame(rows) for name, rows in groups.items()}, pd.DataFrame(register)


def render_quality_summary():
    tables = read_quality_tables()
    groups, register = build_quality_groups(tables)
    st.caption("Counts represent recorded issues, including resolved history. Unresolved documents and reference links are listed separately when both are recorded.")
    cols = st.columns(3)
    for col, (name, rows) in zip(cols, groups.items()):
        col.metric(name, len(rows))
    tabs = st.tabs(list(groups))
    for tab, (name, rows) in zip(tabs, groups.items()):
        with tab:
            if rows.empty:
                st.info("No matching issues were found in the available records.")
            else:
                st.dataframe(rows, use_container_width=True, hide_index=True)
    st.caption("Extraction failures use explicit failure statuses/error fields or recorded extraction conflicts. Filename/content conflicts use recorded mismatch conflicts; files are not re-extracted here.")
    st.subheader("Conflict Register")
    st.metric("Recorded Conflicts", len(register))
    if register.empty:
        st.info("No conflicts are recorded.")
    else:
        st.dataframe(register, use_container_width=True, hide_index=True)



def get_relationship_display_table(
    entity_type,
    entity_id,
):
    relationships = run_query(
        """
        SELECT
            source_type,
            source_id,
            relationship_type,
            target_type,
            target_id,
            confidence,
            status
        FROM relationships

        WHERE (
            source_type = ?
            AND source_id = ?
        )
        OR (
            target_type = ?
            AND target_id = ?
        )

        ORDER BY relationship_type
        """,
        (
            entity_type,
            entity_id,
            entity_type,
            entity_id,
        ),
    )

    rows = []

    for _, relationship in relationships.iterrows():

        source_type = relationship["source_type"]
        source_id = relationship["source_id"]

        target_type = relationship["target_type"]
        target_id = relationship["target_id"]

        # Determine relationship direction
        if (
            source_type == entity_type
            and source_id == entity_id
        ):
            direction = "Outgoing"

            related_type = target_type
            related_id = target_id

        else:
            direction = "Incoming"

            related_type = source_type
            related_id = source_id

        rows.append(
            {
                "Direction": direction,
                "Relationship": pretty_label(
                    relationship[
                        "relationship_type"
                    ],
                    RELATIONSHIP_LABELS,
                ),
                "Related Entity Type": pretty_label(
                    related_type
                ),
                "Related Entity": get_entity_display_name(
                    related_type,
                    related_id,
                ),
                "Confidence": confidence_display(
                    relationship[
                        "confidence"
                    ]
                ),
                "Status": display_status(
                    relationship[
                        "status"
                    ]
                ),
            }
        )

    return pd.DataFrame(rows)

def render_evidence_panel(
    entity_type,
    entity_id,
):
    st.divider()

    st.subheader(
        "Evidence & Provenance"
    )

    st.caption(
        "Why this entity exists, where the evidence came from, "
        "and what relationships or conflicts were observed."
    )

    tab1, tab2, tab3, tab4 = st.tabs(
        [
            "Evidence",
            "Source Files",
            "Relationships",
            "Conflicts",
        ]
    )

    # ========================================================
    # EVIDENCE
    # ========================================================

    with tab1:

        evidence_df = run_query(
            """
            SELECT
                e.evidence_type,
                e.observed_value,
                e.context,
                e.confidence,
                sf.filename
            FROM evidence e

            LEFT JOIN source_files sf
              ON sf.id = e.source_file_id

            WHERE e.subject_type = ?
              AND e.subject_id = ?

            ORDER BY
                e.confidence DESC,
                e.id
            """,
            (
                entity_type,
                entity_id,
            ),
        )

        # Entity mentions are also evidence.
        mention_df = run_query(
            """
            SELECT
                em.observed_value,
                em.role,
                em.extraction_method,
                em.confidence,
                em.resolution_status,
                sf.filename
            FROM entity_mentions em

            LEFT JOIN source_files sf
              ON sf.id = em.source_file_id

            WHERE em.entity_type = ?
              AND em.resolved_entity_id = ?

            ORDER BY
                em.confidence DESC,
                em.id
            """,
            (
                entity_type,
                entity_id,
            ),
        )

        if not evidence_df.empty:

            evidence_display = (
                evidence_df.copy()
            )

            evidence_display[
                "evidence_type"
            ] = evidence_display[
                "evidence_type"
            ].apply(
                pretty_label
            )

            evidence_display[
                "confidence"
            ] = evidence_display[
                "confidence"
            ].apply(
                confidence_display
            )

            evidence_display = (
                evidence_display.rename(
                    columns={
                        "evidence_type":
                            "Evidence Type",
                        "observed_value":
                            "Observed Value",
                        "context":
                            "Context",
                        "confidence":
                            "Confidence",
                        "filename":
                            "Source File",
                    }
                )
            )

            st.markdown(
                "**Recorded Evidence**"
            )

            st.dataframe(
                evidence_display,
                use_container_width=True,
                hide_index=True,
            )

        if not mention_df.empty:

            mention_display = (
                mention_df.copy()
            )

            mention_display[
                "role"
            ] = mention_display[
                "role"
            ].apply(
                pretty_label
            )

            mention_display[
                "extraction_method"
            ] = mention_display[
                "extraction_method"
            ].apply(
                lambda value: pretty_label(
                    value,
                    EXTRACTION_METHOD_LABELS,
                )
            )

            mention_display[
                "confidence"
            ] = mention_display[
                "confidence"
            ].apply(
                confidence_display
            )

            mention_display[
                "resolution_status"
            ] = mention_display[
                "resolution_status"
            ].apply(
                display_status
            )

            mention_display = (
                mention_display.rename(
                    columns={
                        "observed_value":
                            "Observed Value",
                        "role":
                            "Role",
                        "extraction_method":
                            "Extraction Method",
                        "confidence":
                            "Confidence",
                        "resolution_status":
                            "Resolution Status",
                        "filename":
                            "Source File",
                    }
                )
            )

            st.markdown(
                "**Resolved Mentions**"
            )

            st.dataframe(
                mention_display,
                use_container_width=True,
                hide_index=True,
            )

        if (
            evidence_df.empty
            and mention_df.empty
        ):
            st.info(
                "No direct evidence records are available "
                "for this entity."
            )

    # ========================================================
    # SOURCE FILES
    # ========================================================

    with tab2:

        if entity_type == "document":

            source_files = run_query(
                """
                SELECT DISTINCT
                    sf.filename,
                    sf.extension,
                    sf.folder_customer,
                    sf.folder_project,
                    sf.folder_category,
                    ds.source_role,
                    sf.file_path
                FROM document_sources ds

                JOIN source_files sf
                  ON sf.id = ds.source_file_id

                WHERE ds.document_id = ?

                ORDER BY sf.filename
                """,
                (
                    entity_id,
                ),
            )

        else:

            source_files = run_query(
                """
                SELECT DISTINCT
                    sf.filename,
                    sf.extension,
                    sf.folder_customer,
                    sf.folder_project,
                    sf.folder_category,
                    sf.file_path
                FROM entity_mentions em

                JOIN source_files sf
                  ON sf.id = em.source_file_id

                WHERE em.entity_type = ?
                  AND em.resolved_entity_id = ?

                ORDER BY sf.filename
                """,
                (
                    entity_type,
                    entity_id,
                ),
            )

        if not source_files.empty:

            source_display = (
                source_files.copy()
            )

            source_display = (
                source_display.rename(
                    columns={
                        "filename":
                            "Filename",
                        "extension":
                            "Type",
                        "folder_customer":
                            "Folder Customer",
                        "folder_project":
                            "Folder Project",
                        "folder_category":
                            "Folder Category",
                        "source_role":
                            "Source Role",
                        "file_path":
                            "File Path",
                    }
                )
            )

            if (
                "Source Role"
                in source_display.columns
            ):
                source_display[
                    "Source Role"
                ] = source_display[
                    "Source Role"
                ].apply(
                    pretty_label
                )

            st.metric(
                "Supporting Source Files",
                len(source_display),
            )

            st.dataframe(
                source_display,
                use_container_width=True,
                hide_index=True,
            )

        else:

            st.info(
                "No source files are linked to this entity."
            )

    # ========================================================
    # RELATIONSHIPS
    # ========================================================

    with tab3:

        relationship_df = (
            get_relationship_display_table(
                entity_type,
                entity_id,
            )
        )

        if not relationship_df.empty:

            st.metric(
                "Relationships",
                len(
                    relationship_df
                ),
            )

            st.dataframe(
                relationship_df,
                use_container_width=True,
                hide_index=True,
            )

        else:

            st.info(
                "No relationships are currently recorded "
                "for this entity."
            )

    # ========================================================
    # CONFLICTS
    # ========================================================

    with tab4:

        conflicts_df = run_query(
            """
            SELECT
                conflict_type,
                description,
                severity,
                status,
                source_file_id
            FROM conflicts

            WHERE subject_type = ?
              AND subject_id = ?

            ORDER BY id DESC
            """,
            (
                entity_type,
                entity_id,
            ),
        )

        # Documents can also inherit conflicts from their
        # physical source files.
        if entity_type == "document":

            source_conflicts = run_query(
                """
                SELECT
                    c.conflict_type,
                    c.description,
                    c.severity,
                    c.status,
                    c.source_file_id
                FROM conflicts c

                JOIN document_sources ds
                  ON ds.source_file_id =
                     c.source_file_id

                WHERE ds.document_id = ?

                ORDER BY c.id DESC
                """,
                (
                    entity_id,
                ),
            )

            conflicts_df = pd.concat(
                [
                    conflicts_df,
                    source_conflicts,
                ],
                ignore_index=True,
            ).drop_duplicates()

        if not conflicts_df.empty:

            conflict_display = (
                conflicts_df.copy()
            )

            conflict_display[
                "conflict_type"
            ] = conflict_display[
                "conflict_type"
            ].apply(
                pretty_label
            )

            conflict_display[
                "severity"
            ] = conflict_display[
                "severity"
            ].apply(
                pretty_label
            )

            conflict_display[
                "status"
            ] = conflict_display[
                "status"
            ].apply(
                display_status
            )

            conflict_display = (
                conflict_display.rename(
                    columns={
                        "conflict_type":
                            "Conflict Type",
                        "description":
                            "Description",
                        "severity":
                            "Severity",
                        "status":
                            "Status",
                        "source_file_id":
                            "Source File ID",
                    }
                )
            )

            st.warning(
                f"{len(conflict_display)} conflict record(s) "
                "are associated with this entity or its sources."
            )

            st.dataframe(
                conflict_display,
                use_container_width=True,
                hide_index=True,
            )

        else:

            st.success(
                "No recorded conflicts for this entity."
            )

# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.title(
    "Elgoog"
)

page = st.sidebar.radio(
    "Navigation",
    [
        "Overview",
        "Entity Explorer",
        "Graph Explorer",
        "Resolution Review",
        "Data Quality",
        "Relationships",
        "Ask the Knowledge Model",
    ],
)

st.sidebar.divider()

st.sidebar.caption(
    "Entity resolution and relationship exploration "
    "across fragmented source files."
)


# ============================================================
# OVERVIEW
# ============================================================

if page == "Overview":

    st.title(
        "Knowledge Resolution Overview"
    )

    st.caption(
        "Physical source files are resolved into canonical entities, "
        "logical documents and evidence-backed relationships."
    )

    source_file_count = get_scalar(
        "SELECT COUNT(*) FROM source_files"
    )

    document_count = get_scalar(
        "SELECT COUNT(*) FROM documents"
    )

    company_count = get_scalar(
        "SELECT COUNT(*) FROM companies"
    )

    project_count = get_scalar(
        "SELECT COUNT(*) FROM projects"
    )

    person_count = get_scalar(
        "SELECT COUNT(*) FROM people"
    )

    relationship_count = get_scalar(
        "SELECT COUNT(*) FROM relationships"
    )

    conflict_count = get_scalar(
        """
        SELECT COUNT(*)
        FROM conflicts
        WHERE status = 'open'
        """
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "Source Files",
            source_file_count,
        )

        st.metric(
            "Logical Documents",
            document_count,
        )

    with col2:
        st.metric(
            "Companies",
            company_count,
        )

        st.metric(
            "Projects",
            project_count,
        )

    with col3:
        st.metric(
            "People",
            person_count,
        )

        st.metric(
            "Relationships",
            relationship_count,
        )

    st.metric(
        "Open Conflicts",
        conflict_count,
    )

    st.divider()

    left, right = st.columns(2)

    # --------------------------------------------------------
    # Documents by Type
    # --------------------------------------------------------

    with left:

        st.subheader(
            "Documents by Type"
        )

        document_types = run_query(
            """
            SELECT
                document_type,
                COUNT(*) AS document_count
            FROM documents
            GROUP BY document_type
            ORDER BY document_count DESC
            """
        )

        if not document_types.empty:

            document_types[
                "document_type"
            ] = document_types[
                "document_type"
            ].apply(
                pretty_label
            )

            st.bar_chart(
                document_types.set_index(
                    "document_type"
                )
            )

        else:
            st.info(
                "No document data available."
            )

    # --------------------------------------------------------
    # Relationships by Type
    # --------------------------------------------------------

    with right:

        st.subheader(
            "Relationships by Type"
        )

        relationship_types = run_query(
            """
            SELECT
                relationship_type,
                COUNT(*) AS relationship_count
            FROM relationships
            GROUP BY relationship_type
            ORDER BY relationship_count DESC
            """
        )

        if not relationship_types.empty:

            relationship_types[
                "relationship_type"
            ] = relationship_types[
                "relationship_type"
            ].apply(
                lambda value: pretty_label(
                    value,
                    RELATIONSHIP_LABELS,
                )
            )

            st.bar_chart(
                relationship_types.set_index(
                    "relationship_type"
                )
            )

        else:
            st.info(
                "No relationship data available."
            )

    st.divider()

    # --------------------------------------------------------
    # Resolution Queue
    # --------------------------------------------------------

    st.subheader(
        "Resolution Queue"
    )

    queue_summary = run_query(
        """
        SELECT
            decision,
            COUNT(*) AS mention_count
        FROM resolution_candidates
        GROUP BY decision
        ORDER BY mention_count DESC
        """
    )

    if not queue_summary.empty:

        queue_summary[
            "decision"
        ] = queue_summary[
            "decision"
        ].map(
            DECISION_LABELS
        ).fillna(
            queue_summary[
                "decision"
            ]
        )

        queue_summary = (
            queue_summary.rename(
                columns={
                    "decision":
                        "Resolution Status",
                    "mention_count":
                        "Mentions",
                }
            )
        )

        st.dataframe(
            queue_summary,
            use_container_width=True,
            hide_index=True,
        )

    else:
        st.info(
            "No resolution candidate data available."
        )


# ============================================================
# ENTITY EXPLORER
# ============================================================

elif page == "Entity Explorer":

    st.title(
        "Entity Explorer"
    )

    entity_type = st.selectbox(
        "Entity Type",
        [
            "Company",
            "Project",
            "Person",
            "Document",
        ],
    )

    search_term = st.text_input(
        "Search",
        placeholder=(
            "Search canonical name, job number, "
            "email or document number..."
        ),
    )

    # ========================================================
    # COMPANY
    # ========================================================

    if entity_type == "Company":

        companies = run_query(
            """
            SELECT
                id,
                canonical_name,
                resolution_status,
                confidence
            FROM companies
            WHERE canonical_name LIKE ?
            ORDER BY canonical_name
            """,
            (
                f"%{search_term}%",
            ),
        )

        company_display = (
            companies.copy()
        )

        if not company_display.empty:

            company_display[
                "resolution_status"
            ] = company_display[
                "resolution_status"
            ].apply(
                display_status
            )

            company_display[
                "confidence"
            ] = company_display[
                "confidence"
            ].apply(
                confidence_display
            )

            company_display = (
                company_display.rename(
                    columns={
                        "canonical_name":
                            "Canonical Name",
                        "resolution_status":
                            "Resolution Status",
                        "confidence":
                            "Confidence",
                    }
                )
            )

        st.dataframe(
            company_display,
            use_container_width=True,
            hide_index=True,
        )

        if not companies.empty:

            selected_company_name = (
                st.selectbox(
                    "Select Company",
                    companies[
                        "canonical_name"
                    ].tolist(),
                )
            )

            selected_company = companies[
                companies[
                    "canonical_name"
                ]
                == selected_company_name
            ].iloc[0]

            company_id = int(
                selected_company["id"]
            )

            st.divider()

            left, right = st.columns(
                [1, 2]
            )

            with left:

                st.subheader(
                    selected_company_name
                )

                st.write(
                    "**Resolution Status:**",
                    display_status(
                        selected_company[
                            "resolution_status"
                        ]
                    ),
                )

                st.write(
                    "**Confidence:**",
                    confidence_display(
                        selected_company[
                            "confidence"
                        ]
                    ),
                )

            with right:

                st.subheader(
                    "Aliases"
                )

                aliases = run_query(
                    """
                    SELECT DISTINCT
                        alias,
                        confidence,
                        status
                    FROM aliases
                    WHERE entity_type = 'company'
                      AND entity_id = ?
                    ORDER BY alias
                    """,
                    (
                        company_id,
                    ),
                )

                if not aliases.empty:

                    aliases[
                        "confidence"
                    ] = aliases[
                        "confidence"
                    ].apply(
                        confidence_display
                    )

                    aliases[
                        "status"
                    ] = aliases[
                        "status"
                    ].apply(
                        display_status
                    )

                    aliases = aliases.rename(
                        columns={
                            "alias":
                                "Observed Alias",
                            "confidence":
                                "Confidence",
                            "status":
                                "Status",
                        }
                    )

                st.dataframe(
                    aliases,
                    use_container_width=True,
                    hide_index=True,
                )

            st.subheader(
                "Projects"
            )

            projects = run_query(
                """
                SELECT
                    p.job_number,
                    p.canonical_name,
                    r.confidence,
                    r.status
                FROM relationships r

                JOIN projects p
                  ON p.id = r.target_id

                WHERE r.source_type = 'company'
                  AND r.source_id = ?
                  AND r.relationship_type = 'HAS_PROJECT'
                  AND r.target_type = 'project'

                ORDER BY p.job_number
                """,
                (
                    company_id,
                ),
            )

            if not projects.empty:

                projects[
                    "confidence"
                ] = projects[
                    "confidence"
                ].apply(
                    confidence_display
                )

                projects[
                    "status"
                ] = projects[
                    "status"
                ].apply(
                    display_status
                )

                projects = projects.rename(
                    columns={
                        "job_number":
                            "Job Number",
                        "canonical_name":
                            "Project Name",
                        "confidence":
                            "Confidence",
                        "status":
                            "Status",
                    }
                )

            st.dataframe(
                projects,
                use_container_width=True,
                hide_index=True,
            )

            render_evidence_panel("company", company_id)

    # ========================================================
    # PROJECT
    # ========================================================

    elif entity_type == "Project":

        projects = run_query(
            """
            SELECT
                id,
                job_number,
                canonical_name,
                resolution_status,
                confidence
            FROM projects
            WHERE job_number LIKE ?
               OR canonical_name LIKE ?
            ORDER BY job_number
            """,
            (
                f"%{search_term}%",
                f"%{search_term}%",
            ),
        )

        project_display = (
            projects.copy()
        )

        if not project_display.empty:

            project_display[
                "resolution_status"
            ] = project_display[
                "resolution_status"
            ].apply(
                display_status
            )

            project_display[
                "confidence"
            ] = project_display[
                "confidence"
            ].apply(
                confidence_display
            )

            project_display = (
                project_display.rename(
                    columns={
                        "job_number":
                            "Job Number",
                        "canonical_name":
                            "Project Name",
                        "resolution_status":
                            "Resolution Status",
                        "confidence":
                            "Confidence",
                    }
                )
            )

        st.dataframe(
            project_display,
            use_container_width=True,
            hide_index=True,
        )


        if not projects.empty:

            display_values = (
                projects[
                    "job_number"
                ].fillna("")
                + " — "
                + projects[
                    "canonical_name"
                ].fillna("")
            ).tolist()

            selected_display = (
                st.selectbox(
                    "Select Project",
                    display_values,
                )
            )

            selected_index = (
                display_values.index(
                    selected_display
                )
            )

            selected_project = (
                projects.iloc[
                    selected_index
                ]
            )

            project_id = int(
                selected_project["id"]
            )

            st.divider()

            st.subheader(
                selected_display
            )

            # Representative document:
            # one document per type.
            documents = run_query(
                """
                WITH ranked_documents AS (
                    SELECT
                        d.id,
                        d.document_type,
                        d.document_number,
                        d.title,
                        ROW_NUMBER() OVER (
                            PARTITION BY d.document_type
                            ORDER BY
                                d.document_number,
                                d.id
                        ) AS type_rank

                    FROM relationships r

                    JOIN documents d
                      ON d.id = r.source_id

                    WHERE r.source_type = 'document'
                      AND r.relationship_type =
                          'RELATES_TO_PROJECT'
                      AND r.target_type = 'project'
                      AND r.target_id = ?
                )

                SELECT
                    id,
                    document_type,
                    document_number,
                    title
                FROM ranked_documents

                WHERE type_rank = 1

                ORDER BY
                    CASE document_type
                        WHEN 'quotation' THEN 1
                        WHEN 'purchase_order' THEN 2
                        WHEN 'delivery_note' THEN 3
                        WHEN 'invoice' THEN 4
                        WHEN 'drawing' THEN 5
                        WHEN 'correspondence' THEN 6
                        ELSE 7
                    END

                LIMIT 6
                """,
                (
                    project_id,
                ),
            )

            if not documents.empty:

                documents[
                    "document_type"
                ] = documents[
                    "document_type"
                ].apply(
                    pretty_label
                )

                documents = documents.rename(
                    columns={
                        "document_type":
                            "Document Type",
                        "document_number":
                            "Document Number",
                        "title":
                            "Title",
                    }
                )

            st.subheader(
                "Representative Documents"
            )

            st.dataframe(
                documents,
                use_container_width=True,
                hide_index=True,
            )

            render_evidence_panel("project", project_id)

    # ========================================================
    # PERSON
    # ========================================================

    elif entity_type == "Person":

        people = run_query(
            """
            SELECT
                id,
                canonical_name,
                email,
                resolution_status,
                confidence
            FROM people
            WHERE canonical_name LIKE ?
               OR email LIKE ?
            ORDER BY canonical_name
            """,
            (
                f"%{search_term}%",
                f"%{search_term}%",
            ),
        )

        people_display = (
            people.copy()
        )

        if not people_display.empty:

            people_display[
                "resolution_status"
            ] = people_display[
                "resolution_status"
            ].apply(
                display_status
            )

            people_display[
                "confidence"
            ] = people_display[
                "confidence"
            ].apply(
                confidence_display
            )

            people_display = (
                people_display.rename(
                    columns={
                        "canonical_name":
                            "Canonical Name",
                        "email":
                            "Email",
                        "resolution_status":
                            "Resolution Status",
                        "confidence":
                            "Confidence",
                    }
                )
            )

        st.dataframe(
            people_display,
            use_container_width=True,
            hide_index=True,
        )


        if not people.empty:

            names = (
                people[
                    "canonical_name"
                ].fillna("")
                + " — "
                + people[
                    "email"
                ].fillna("")
            ).tolist()

            selected_person_display = (
                st.selectbox(
                    "Select Person",
                    names,
                )
            )

            selected_index = (
                names.index(
                    selected_person_display
                )
            )

            selected_person = (
                people.iloc[
                    selected_index
                ]
            )

            person_id = int(
                selected_person["id"]
            )

            st.divider()

            st.subheader(
                selected_person_display
            )

            communications = run_query(
                """
                SELECT
                    d.title,
                    d.document_date,
                    r.relationship_type,
                    r.confidence
                FROM relationships r

                JOIN documents d
                  ON d.id = r.source_id

                WHERE r.source_type = 'document'
                  AND r.target_type = 'person'
                  AND r.target_id = ?

                ORDER BY d.document_date DESC
                """,
                (
                    person_id,
                ),
            )

            if not communications.empty:

                communications[
                    "relationship_type"
                ] = communications[
                    "relationship_type"
                ].apply(
                    lambda value: pretty_label(
                        value,
                        RELATIONSHIP_LABELS,
                    )
                )

                communications[
                    "confidence"
                ] = communications[
                    "confidence"
                ].apply(
                    confidence_display
                )

                communications = (
                    communications.rename(
                        columns={
                            "title":
                                "Subject",
                            "document_date":
                                "Date",
                            "relationship_type":
                                "Role",
                            "confidence":
                                "Confidence",
                        }
                    )
                )

            st.subheader(
                "Correspondence"
            )

            st.dataframe(
                communications,
                use_container_width=True,
                hide_index=True,
            )

            render_evidence_panel("person", person_id)

    # ========================================================
    # DOCUMENT
    # ========================================================

    elif entity_type == "Document":

        documents = run_query(
            """
            SELECT
                id,
                document_type,
                document_number,
                title,
                document_date,
                resolution_status,
                extraction_confidence
            FROM documents
            WHERE document_number LIKE ?
               OR title LIKE ?
               OR document_type LIKE ?
            ORDER BY document_type,
                     document_number
            LIMIT 500
            """,
            (
                f"%{search_term}%",
                f"%{search_term}%",
                f"%{search_term}%",
            ),
        )

        document_display = (
            documents.copy()
        )

        if not document_display.empty:

            document_display[
                "document_type"
            ] = document_display[
                "document_type"
            ].apply(
                pretty_label
            )

            document_display[
                "resolution_status"
            ] = document_display[
                "resolution_status"
            ].apply(
                display_status
            )

            document_display[
                "extraction_confidence"
            ] = document_display[
                "extraction_confidence"
            ].apply(
                confidence_display
            )

            document_display = (
                document_display.rename(
                    columns={
                        "document_type":
                            "Document Type",
                        "document_number":
                            "Document Number",
                        "title":
                            "Title",
                        "document_date":
                            "Date",
                        "resolution_status":
                            "Resolution Status",
                        "extraction_confidence":
                            "Confidence",
                    }
                )
            )

        st.dataframe(
            document_display,
            use_container_width=True,
            hide_index=True,
        )


        if not documents.empty:

            document_labels = []

            for _, row in (
                documents.iterrows()
            ):

                number = (
                    row["document_number"]
                    if pd.notna(
                        row["document_number"]
                    )
                    else ""
                )

                if number:

                    label = (
                        f"{pretty_label(row['document_type'])} "
                        f"{number}"
                    )

                elif pd.notna(row["title"]):

                    label = (
                        f"{pretty_label(row['document_type'])} — "
                        f"{shorten_text(row['title'], 55)}"
                    )

                else:

                    label = (
                        f"Document {row['id']}"
                    )

                document_labels.append(
                    label
                )

            selected_label = (
                st.selectbox(
                    "Select Document",
                    document_labels,
                )
            )

            selected_index = (
                document_labels.index(
                    selected_label
                )
            )

            selected_document = (
                documents.iloc[
                    selected_index
                ]
            )

            document_id = int(
                selected_document["id"]
            )

            st.divider()

            st.subheader(
                selected_label
            )

            col1, col2, col3 = (
                st.columns(3)
            )

            with col1:

                st.write(
                    "**Type:**",
                    pretty_label(
                        selected_document[
                            "document_type"
                        ]
                    ),
                )

            with col2:

                st.write(
                    "**Status:**",
                    display_status(
                        selected_document[
                            "resolution_status"
                        ]
                    ),
                )

            with col3:

                st.write(
                    "**Confidence:**",
                    confidence_display(
                        selected_document[
                            "extraction_confidence"
                        ]
                    ),
                )

            # ------------------------------------------------
            # Outgoing Relationships
            # ------------------------------------------------

            st.subheader(
                "Outgoing Relationships"
            )

            outgoing = run_query(
                """
                SELECT
                    relationship_type,
                    target_type,
                    target_id,
                    confidence,
                    status
                FROM relationships
                WHERE source_type = 'document'
                  AND source_id = ?
                ORDER BY relationship_type
                """,
                (
                    document_id,
                ),
            )

            if not outgoing.empty:
                outgoing["Target Entity"] = [
                    get_entity_display_name(row.target_type, row.target_id)
                    for row in outgoing.itertuples(index=False)
                ]
                outgoing = outgoing.drop(columns=["target_id"])

                outgoing[
                    "relationship_type"
                ] = outgoing[
                    "relationship_type"
                ].apply(
                    lambda value: pretty_label(
                        value,
                        RELATIONSHIP_LABELS,
                    )
                )

                outgoing[
                    "target_type"
                ] = outgoing[
                    "target_type"
                ].apply(
                    pretty_label
                )

                outgoing[
                    "confidence"
                ] = outgoing[
                    "confidence"
                ].apply(
                    confidence_display
                )

                outgoing[
                    "status"
                ] = outgoing[
                    "status"
                ].apply(
                    display_status
                )

                outgoing = outgoing.rename(
                    columns={
                        "relationship_type":
                            "Relationship",
                        "target_type":
                            "Target Type",
                        "target_id":
                            "Target ID",
                        "confidence":
                            "Confidence",
                        "status":
                            "Status",
                    }
                )

            st.dataframe(
                outgoing,
                use_container_width=True,
                hide_index=True,
            )

            # ------------------------------------------------
            # Source Files
            # ------------------------------------------------

            st.subheader(
                "Source Files"
            )

            sources = run_query(
                """
                SELECT
                    sf.filename,
                    sf.file_path,
                    sf.folder_customer,
                    sf.folder_project,
                    sf.folder_category,
                    ds.source_role
                FROM document_sources ds

                JOIN source_files sf
                  ON sf.id = ds.source_file_id

                WHERE ds.document_id = ?

                ORDER BY sf.filename
                """,
                (
                    document_id,
                ),
            )

            if not sources.empty:

                sources[
                    "source_role"
                ] = sources[
                    "source_role"
                ].apply(
                    pretty_label
                )

                sources = sources.rename(
                    columns={
                        "filename":
                            "Filename",
                        "file_path":
                            "File Path",
                        "folder_customer":
                            "Folder Customer",
                        "folder_project":
                            "Folder Project",
                        "folder_category":
                            "Folder Category",
                        "source_role":
                            "Source Role",
                    }
                )

            st.dataframe(
                sources,
                use_container_width=True,
                hide_index=True,
            )

            render_evidence_panel("document", document_id)


# ============================================================
# GRAPH EXPLORER
# ============================================================

elif page == "Graph Explorer":

    st.title(
        "Graph Explorer"
    )

    st.caption(
        "Explore the resolved knowledge model through focused "
        "business, document and communication views."
    )

    # ========================================================
    # GRAPH LABEL HELPERS
    # ========================================================

    def get_entity_label(
        entity_type,
        entity_id,
    ):

        if entity_type == "company":

            result = run_query(
                """
                SELECT canonical_name
                FROM companies
                WHERE id = ?
                """,
                (
                    entity_id,
                ),
            )

            if not result.empty:
                return str(
                    result.iloc[0][
                        "canonical_name"
                    ]
                )

        elif entity_type == "project":

            result = run_query(
                """
                SELECT
                    job_number,
                    canonical_name
                FROM projects
                WHERE id = ?
                """,
                (
                    entity_id,
                ),
            )

            if not result.empty:

                row = result.iloc[0]

                if pd.notna(
                    row["job_number"]
                ):
                    return str(
                        row["job_number"]
                    )

                return shorten_text(
                    row["canonical_name"],
                    32,
                )

        elif entity_type == "person":

            result = run_query(
                """
                SELECT
                    canonical_name,
                    email
                FROM people
                WHERE id = ?
                """,
                (
                    entity_id,
                ),
            )

            if not result.empty:

                row = result.iloc[0]

                if pd.notna(
                    row["canonical_name"]
                ):
                    return shorten_text(
                        row[
                            "canonical_name"
                        ],
                        30,
                    )

                return shorten_text(
                    row["email"],
                    30,
                )

        elif entity_type == "document":

            result = run_query(
                """
                SELECT
                    document_type,
                    document_number,
                    title
                FROM documents
                WHERE id = ?
                """,
                (
                    entity_id,
                ),
            )

            if not result.empty:

                row = result.iloc[0]

                document_type = (
                    row[
                        "document_type"
                    ]
                )

                if pd.notna(
                    row[
                        "document_number"
                    ]
                ):

                    return str(
                        row[
                            "document_number"
                        ]
                    )

                if (
                    document_type
                    == "correspondence"
                ):

                    return (
                        "Email: "
                        + shorten_text(
                            row["title"],
                            28,
                        )
                    )

                if pd.notna(
                    row["title"]
                ):

                    return shorten_text(
                        row["title"],
                        32,
                    )

        return (
            f"{entity_type} "
            f"#{entity_id}"
        )


    def get_entity_title(
        entity_type,
        entity_id,
    ):

        if entity_type == "company":

            result = run_query(
                """
                SELECT
                    canonical_name,
                    resolution_status,
                    confidence
                FROM companies
                WHERE id = ?
                """,
                (
                    entity_id,
                ),
            )

            if not result.empty:

                row = result.iloc[0]

                return (
                    f"<b>{row['canonical_name']}</b>"
                    f"<br>Type: Company"
                    f"<br>Status: "
                    f"{display_status(row['resolution_status'])}"
                    f"<br>Confidence: "
                    f"{confidence_display(row['confidence'])}"
                )

        elif entity_type == "project":

            result = run_query(
                """
                SELECT
                    job_number,
                    canonical_name,
                    resolution_status,
                    confidence
                FROM projects
                WHERE id = ?
                """,
                (
                    entity_id,
                ),
            )

            if not result.empty:

                row = result.iloc[0]

                return (
                    f"<b>{row['job_number']}</b>"
                    f"<br>{row['canonical_name']}"
                    f"<br>Type: Project"
                    f"<br>Status: "
                    f"{display_status(row['resolution_status'])}"
                    f"<br>Confidence: "
                    f"{confidence_display(row['confidence'])}"
                )

        elif entity_type == "person":

            result = run_query(
                """
                SELECT
                    canonical_name,
                    email,
                    resolution_status,
                    confidence
                FROM people
                WHERE id = ?
                """,
                (
                    entity_id,
                ),
            )

            if not result.empty:

                row = result.iloc[0]

                return (
                    f"<b>{row['canonical_name']}</b>"
                    f"<br>{row['email']}"
                    f"<br>Type: Person"
                    f"<br>Status: "
                    f"{display_status(row['resolution_status'])}"
                )

        elif entity_type == "document":

            result = run_query(
                """
                SELECT
                    document_type,
                    document_number,
                    title,
                    document_date,
                    resolution_status,
                    extraction_confidence
                FROM documents
                WHERE id = ?
                """,
                (
                    entity_id,
                ),
            )

            if not result.empty:

                row = result.iloc[0]

                if pd.notna(
                    row[
                        "document_number"
                    ]
                ):
                    heading = (
                        row[
                            "document_number"
                        ]
                    )
                else:
                    heading = (
                        row["title"]
                    )

                return (
                    f"<b>{heading}</b>"
                    f"<br>Type: "
                    f"{pretty_label(row['document_type'])}"
                    f"<br>Status: "
                    f"{display_status(row['resolution_status'])}"
                    f"<br>Confidence: "
                    f"{confidence_display(row['extraction_confidence'])}"
                )

        return (
            f"{entity_type} "
            f"#{entity_id}"
        )


    # ========================================================
    # START ENTITY
    # ========================================================

    entity_type = st.selectbox(
        "Start From",
        [
            "Company",
            "Project",
            "Person",
            "Document",
        ],
    )

    search_term = st.text_input(
        "Search Entity",
        placeholder=(
            "e.g. Falcon, JOB-2026-0026, "
            "Marcus, PO-3167"
        ),
    )

    selected_entity = None

    # --------------------------------------------------------
    # Company Search
    # --------------------------------------------------------

    if entity_type == "Company":

        entities = run_query(
            """
            SELECT
                id,
                canonical_name AS label
            FROM companies
            WHERE canonical_name LIKE ?
            ORDER BY canonical_name
            LIMIT 50
            """,
            (
                f"%{search_term}%",
            ),
        )

    # --------------------------------------------------------
    # Project Search
    # --------------------------------------------------------

    elif entity_type == "Project":

        entities = run_query(
            """
            SELECT
                id,
                job_number || ' — ' ||
                canonical_name AS label
            FROM projects
            WHERE job_number LIKE ?
               OR canonical_name LIKE ?
            ORDER BY job_number
            LIMIT 50
            """,
            (
                f"%{search_term}%",
                f"%{search_term}%",
            ),
        )

    # --------------------------------------------------------
    # Person Search
    # --------------------------------------------------------

    elif entity_type == "Person":

        entities = run_query(
            """
            SELECT
                id,
                canonical_name ||
                CASE
                    WHEN email IS NOT NULL
                    THEN ' — ' || email
                    ELSE ''
                END AS label
            FROM people
            WHERE canonical_name LIKE ?
               OR email LIKE ?
            ORDER BY canonical_name
            LIMIT 50
            """,
            (
                f"%{search_term}%",
                f"%{search_term}%",
            ),
        )

    # --------------------------------------------------------
    # Document Search
    # --------------------------------------------------------

    else:

        entities = run_query(
            """
            SELECT
                id,
                CASE
                    WHEN document_number IS NOT NULL
                    THEN document_type || ' — ' ||
                         document_number
                    ELSE document_type || ' — ' ||
                         COALESCE(title, '')
                END AS label
            FROM documents
            WHERE document_number LIKE ?
               OR title LIKE ?
               OR document_type LIKE ?
            ORDER BY document_type,
                     document_number
            LIMIT 50
            """,
            (
                f"%{search_term}%",
                f"%{search_term}%",
                f"%{search_term}%",
            ),
        )

    if entities.empty:

        st.info(
            "No matching entities found."
        )

    else:

        selected_label = st.selectbox(
            "Select Entity",
            entities[
                "label"
            ].tolist(),
        )

        selected_row = entities[
            entities[
                "label"
            ]
            == selected_label
        ].iloc[0]

        selected_entity = {
            "type":
                entity_type.lower(),
            "id":
                int(
                    selected_row[
                        "id"
                    ]
                ),
            "label":
                selected_label,
        }

    # ========================================================
    # GRAPH
    # ========================================================

    if selected_entity:

        st.divider()

        col1, col2, col3 = (
            st.columns(3)
        )

        with col1:

            graph_view = st.selectbox(
                "View",
                [
                    "Business Structure",
                    "Documents",
                    "Communications",
                    "Direct Relationships",
                ],
            )

        with col2:

            max_nodes = st.slider(
                "Maximum Nodes",
                min_value=10,
                max_value=80,
                value=25,
                step=5,
            )

        with col3:

            show_edge_labels = (
                st.checkbox(
                    "Show Edge Labels",
                    value=False,
                )
            )

        G = nx.DiGraph()

        start_key = (
            f"{selected_entity['type']}:"
            f"{selected_entity['id']}"
        )

        G.add_node(
            start_key,
            label=get_entity_label(
                selected_entity[
                    "type"
                ],
                selected_entity[
                    "id"
                ],
            ),
            entity_type=(
                selected_entity[
                    "type"
                ]
            ),
            title=get_entity_title(
                selected_entity[
                    "type"
                ],
                selected_entity[
                    "id"
                ],
            ),
            start_node=True,
        )

        # ====================================================
        # BUSINESS STRUCTURE
        # ====================================================

        if (
            graph_view
            == "Business Structure"
        ):

            # ------------------------------------------------
            # Company
            # ------------------------------------------------

            if (
                selected_entity[
                    "type"
                ]
                == "company"
            ):

                company_id = (
                    selected_entity[
                        "id"
                    ]
                )

                projects = run_query(
                    """
                    SELECT
                        p.id,
                        p.job_number,
                        p.canonical_name
                    FROM relationships r

                    JOIN projects p
                      ON p.id = r.target_id

                    WHERE r.source_type = 'company'
                      AND r.source_id = ?
                      AND r.relationship_type = 'HAS_PROJECT'
                      AND r.target_type = 'project'

                    ORDER BY p.job_number
                    """,
                    (
                        company_id,
                    ),
                )

                remaining_nodes = (
                    max_nodes - 1
                )

                for _, project in (
                    projects.iterrows()
                ):

                    if (
                        remaining_nodes
                        <= 0
                    ):
                        break

                    project_id = int(
                        project["id"]
                    )

                    project_key = (
                        f"project:"
                        f"{project_id}"
                    )

                    G.add_node(
                        project_key,
                        label=get_entity_label(
                            "project",
                            project_id,
                        ),
                        entity_type=(
                            "project"
                        ),
                        title=get_entity_title(
                            "project",
                            project_id,
                        ),
                    )

                    G.add_edge(
                        start_key,
                        project_key,
                        label="Has Project",
                        relationship_type=(
                            "HAS_PROJECT"
                        ),
                    )

                    remaining_nodes -= 1

                    if (
                        remaining_nodes
                        <= 0
                    ):
                        break

                    # ----------------------------------------
                    # One representative document per type
                    # ----------------------------------------

                    documents = run_query(
                        """
                        WITH ranked_documents AS (
                            SELECT
                                d.id,
                                d.document_type,
                                d.document_number,
                                d.title,

                                ROW_NUMBER() OVER (
                                    PARTITION BY d.document_type
                                    ORDER BY
                                        d.document_number,
                                        d.id
                                ) AS type_rank

                            FROM relationships r

                            JOIN documents d
                              ON d.id = r.source_id

                            WHERE r.source_type = 'document'
                              AND r.relationship_type =
                                  'RELATES_TO_PROJECT'
                              AND r.target_type = 'project'
                              AND r.target_id = ?
                        )

                        SELECT
                            id,
                            document_type,
                            document_number,
                            title

                        FROM ranked_documents

                        WHERE type_rank = 1

                        ORDER BY
                            CASE document_type
                                WHEN 'quotation' THEN 1
                                WHEN 'purchase_order' THEN 2
                                WHEN 'delivery_note' THEN 3
                                WHEN 'invoice' THEN 4
                                WHEN 'drawing' THEN 5
                                WHEN 'correspondence' THEN 6
                                ELSE 7
                            END

                        LIMIT 6
                        """,
                        (
                            project_id,
                        ),
                    )

                    for _, document in (
                        documents.iterrows()
                    ):

                        if (
                            remaining_nodes
                            <= 0
                        ):
                            break

                        document_id = int(
                            document["id"]
                        )

                        document_key = (
                            f"document:"
                            f"{document_id}"
                        )

                        G.add_node(
                            document_key,
                            label=(
                                get_entity_label(
                                    "document",
                                    document_id,
                                )
                            ),
                            entity_type=(
                                "document"
                            ),
                            title=(
                                get_entity_title(
                                    "document",
                                    document_id,
                                )
                            ),
                        )

                        G.add_edge(
                            project_key,
                            document_key,
                            label=(
                                "Related Document"
                            ),
                            relationship_type=(
                                "RELATES_TO_PROJECT"
                            ),
                        )

                        remaining_nodes -= 1

            # ------------------------------------------------
            # Project
            # ------------------------------------------------

            elif (
                selected_entity[
                    "type"
                ]
                == "project"
            ):

                project_id = (
                    selected_entity[
                        "id"
                    ]
                )

                remaining_nodes = (
                    max_nodes - 1
                )

                companies = run_query(
                    """
                    SELECT
                        c.id,
                        c.canonical_name
                    FROM relationships r

                    JOIN companies c
                      ON c.id = r.source_id

                    WHERE r.source_type = 'company'
                      AND r.relationship_type = 'HAS_PROJECT'
                      AND r.target_type = 'project'
                      AND r.target_id = ?
                    """,
                    (
                        project_id,
                    ),
                )

                for _, company in (
                    companies.iterrows()
                ):

                    if (
                        remaining_nodes
                        <= 0
                    ):
                        break

                    company_id = int(
                        company["id"]
                    )

                    company_key = (
                        f"company:"
                        f"{company_id}"
                    )

                    G.add_node(
                        company_key,
                        label=get_entity_label(
                            "company",
                            company_id,
                        ),
                        entity_type=(
                            "company"
                        ),
                        title=get_entity_title(
                            "company",
                            company_id,
                        ),
                    )

                    G.add_edge(
                        company_key,
                        start_key,
                        label=(
                            "Has Project"
                        ),
                        relationship_type=(
                            "HAS_PROJECT"
                        ),
                    )

                    remaining_nodes -= 1

                # One per document type
                documents = run_query(
                    """
                    WITH ranked_documents AS (
                        SELECT
                            d.id,
                            d.document_type,
                            d.document_number,
                            d.title,

                            ROW_NUMBER() OVER (
                                PARTITION BY d.document_type
                                ORDER BY
                                    d.document_number,
                                    d.id
                            ) AS type_rank

                        FROM relationships r

                        JOIN documents d
                          ON d.id = r.source_id

                        WHERE r.source_type = 'document'
                          AND r.relationship_type =
                              'RELATES_TO_PROJECT'
                          AND r.target_type = 'project'
                          AND r.target_id = ?
                    )

                    SELECT
                        id,
                        document_type,
                        document_number,
                        title

                    FROM ranked_documents

                    WHERE type_rank = 1

                    ORDER BY
                        CASE document_type
                            WHEN 'quotation' THEN 1
                            WHEN 'purchase_order' THEN 2
                            WHEN 'delivery_note' THEN 3
                            WHEN 'invoice' THEN 4
                            WHEN 'drawing' THEN 5
                            WHEN 'correspondence' THEN 6
                            ELSE 7
                        END

                    LIMIT ?
                    """,
                    (
                        project_id,
                        remaining_nodes,
                    ),
                )

                for _, document in (
                    documents.iterrows()
                ):

                    if (
                        remaining_nodes
                        <= 0
                    ):
                        break

                    document_id = int(
                        document["id"]
                    )

                    document_key = (
                        f"document:"
                        f"{document_id}"
                    )

                    G.add_node(
                        document_key,
                        label=get_entity_label(
                            "document",
                            document_id,
                        ),
                        entity_type=(
                            "document"
                        ),
                        title=get_entity_title(
                            "document",
                            document_id,
                        ),
                    )

                    G.add_edge(
                        start_key,
                        document_key,
                        label=(
                            "Related Document"
                        ),
                        relationship_type=(
                            "RELATES_TO_PROJECT"
                        ),
                    )

                    remaining_nodes -= 1

            else:

                st.info(
                    "Business Structure is most useful "
                    "for Company and Project entities."
                )

        # ====================================================
        # DOCUMENTS VIEW
        # ====================================================

        elif graph_view == "Documents":

            relationships = run_query(
                """
                SELECT
                    source_type,
                    source_id,
                    relationship_type,
                    target_type,
                    target_id,
                    confidence
                FROM relationships

                WHERE (
                    source_type = ?
                    AND source_id = ?
                )

                OR (
                    target_type = ?
                    AND target_id = ?
                )

                ORDER BY relationship_type

                LIMIT ?
                """,
                (
                    selected_entity[
                        "type"
                    ],
                    selected_entity[
                        "id"
                    ],
                    selected_entity[
                        "type"
                    ],
                    selected_entity[
                        "id"
                    ],
                    max_nodes * 4,
                ),
            )

            for _, row in (
                relationships.iterrows()
            ):

                if (
                    G.number_of_nodes()
                    >= max_nodes
                ):
                    break

                # Only relationships involving
                # at least one document.
                if (
                    row["source_type"]
                    != "document"
                    and row["target_type"]
                    != "document"
                ):
                    continue

                source_key = (
                    f"{row['source_type']}:"
                    f"{row['source_id']}"
                )

                target_key = (
                    f"{row['target_type']}:"
                    f"{row['target_id']}"
                )

                G.add_node(
                    source_key,
                    label=get_entity_label(
                        row[
                            "source_type"
                        ],
                        int(
                            row[
                                "source_id"
                            ]
                        ),
                    ),
                    entity_type=(
                        row[
                            "source_type"
                        ]
                    ),
                    title=get_entity_title(
                        row[
                            "source_type"
                        ],
                        int(
                            row[
                                "source_id"
                            ]
                        ),
                    ),
                )

                G.add_node(
                    target_key,
                    label=get_entity_label(
                        row[
                            "target_type"
                        ],
                        int(
                            row[
                                "target_id"
                            ]
                        ),
                    ),
                    entity_type=(
                        row[
                            "target_type"
                        ]
                    ),
                    title=get_entity_title(
                        row[
                            "target_type"
                        ],
                        int(
                            row[
                                "target_id"
                            ]
                        ),
                    ),
                )

                G.add_edge(
                    source_key,
                    target_key,
                    label=pretty_label(
                        row[
                            "relationship_type"
                        ],
                        RELATIONSHIP_LABELS,
                    ),
                    relationship_type=(
                        row[
                            "relationship_type"
                        ]
                    ),
                )

        # ====================================================
        # COMMUNICATIONS VIEW
        # ====================================================

        elif (
            graph_view
            == "Communications"
        ):

            remaining_nodes = (
                max_nodes - 1
            )

            # ------------------------------------------------
            # COMPANY COMMUNICATIONS
            # ------------------------------------------------

            if (
                selected_entity[
                    "type"
                ]
                == "company"
            ):

                company_id = (
                    selected_entity[
                        "id"
                    ]
                )

                projects = run_query(
                    """
                    SELECT DISTINCT
                        p.id,
                        p.job_number
                    FROM relationships r

                    JOIN projects p
                      ON p.id = r.target_id

                    WHERE r.source_type = 'company'
                      AND r.source_id = ?
                      AND r.relationship_type = 'HAS_PROJECT'
                      AND r.target_type = 'project'

                    ORDER BY p.job_number
                    """,
                    (
                        company_id,
                    ),
                )

                for _, project in (
                    projects.iterrows()
                ):

                    if (
                        remaining_nodes
                        <= 0
                    ):
                        break

                    project_id = int(
                        project["id"]
                    )

                    project_key = (
                        f"project:"
                        f"{project_id}"
                    )

                    G.add_node(
                        project_key,
                        label=get_entity_label(
                            "project",
                            project_id,
                        ),
                        entity_type=(
                            "project"
                        ),
                        title=get_entity_title(
                            "project",
                            project_id,
                        ),
                    )

                    G.add_edge(
                        start_key,
                        project_key,
                        label=(
                            "Has Project"
                        ),
                        relationship_type=(
                            "HAS_PROJECT"
                        ),
                    )

                    remaining_nodes -= 1

                    if (
                        remaining_nodes
                        <= 0
                    ):
                        break

                    # Maximum 2 communications per project
                    # keeps the graph readable.
                    emails = run_query(
                        """
                        SELECT DISTINCT
                            d.id,
                            d.title,
                            d.document_date
                        FROM relationships r

                        JOIN documents d
                          ON d.id = r.source_id

                        WHERE r.source_type = 'document'
                          AND r.relationship_type =
                              'RELATES_TO_PROJECT'
                          AND r.target_type = 'project'
                          AND r.target_id = ?
                          AND d.document_type =
                              'correspondence'

                        ORDER BY d.document_date DESC

                        LIMIT 2
                        """,
                        (
                            project_id,
                        ),
                    )

                    for _, email in (
                        emails.iterrows()
                    ):

                        if (
                            remaining_nodes
                            <= 0
                        ):
                            break

                        email_id = int(
                            email["id"]
                        )

                        email_key = (
                            f"document:"
                            f"{email_id}"
                        )

                        G.add_node(
                            email_key,
                            label=(
                                get_entity_label(
                                    "document",
                                    email_id,
                                )
                            ),
                            entity_type=(
                                "document"
                            ),
                            title=(
                                get_entity_title(
                                    "document",
                                    email_id,
                                )
                            ),
                        )

                        G.add_edge(
                            project_key,
                            email_key,
                            label=(
                                "Communication"
                            ),
                            relationship_type=(
                                "RELATES_TO_PROJECT"
                            ),
                        )

                        remaining_nodes -= 1

                        if (
                            remaining_nodes
                            <= 0
                        ):
                            break

                        people = run_query(
                            """
                            SELECT
                                r.relationship_type,
                                p.id
                            FROM relationships r

                            JOIN people p
                              ON p.id = r.target_id

                            WHERE r.source_type =
                                  'document'
                              AND r.source_id = ?
                              AND r.target_type =
                                  'person'
                              AND r.relationship_type IN (
                                  'SENDER',
                                  'RECIPIENT',
                                  'CC'
                              )

                            ORDER BY
                                CASE r.relationship_type
                                    WHEN 'SENDER' THEN 1
                                    WHEN 'RECIPIENT' THEN 2
                                    WHEN 'CC' THEN 3
                                    ELSE 4
                                END

                            LIMIT 3
                            """,
                            (
                                email_id,
                            ),
                        )

                        for _, person in (
                            people.iterrows()
                        ):

                            if (
                                remaining_nodes
                                <= 0
                            ):
                                break

                            person_id = int(
                                person[
                                    "id"
                                ]
                            )

                            person_key = (
                                f"person:"
                                f"{person_id}"
                            )

                            G.add_node(
                                person_key,
                                label=(
                                    get_entity_label(
                                        "person",
                                        person_id,
                                    )
                                ),
                                entity_type=(
                                    "person"
                                ),
                                title=(
                                    get_entity_title(
                                        "person",
                                        person_id,
                                    )
                                ),
                            )

                            G.add_edge(
                                email_key,
                                person_key,
                                label=(
                                    pretty_label(
                                        person[
                                            "relationship_type"
                                        ],
                                        RELATIONSHIP_LABELS,
                                    )
                                ),
                                relationship_type=(
                                    person[
                                        "relationship_type"
                                    ]
                                ),
                            )

                            remaining_nodes -= 1

            # ------------------------------------------------
            # PROJECT COMMUNICATIONS
            # ------------------------------------------------

            elif (
                selected_entity[
                    "type"
                ]
                == "project"
            ):

                project_id = (
                    selected_entity[
                        "id"
                    ]
                )

                emails = run_query(
                    """
                    SELECT DISTINCT
                        d.id,
                        d.document_date
                    FROM relationships r

                    JOIN documents d
                      ON d.id = r.source_id

                    WHERE r.source_type = 'document'
                      AND r.relationship_type =
                          'RELATES_TO_PROJECT'
                      AND r.target_type = 'project'
                      AND r.target_id = ?
                      AND d.document_type =
                          'correspondence'

                    ORDER BY d.document_date DESC

                    LIMIT 8
                    """,
                    (
                        project_id,
                    ),
                )

                for _, email in (
                    emails.iterrows()
                ):

                    if (
                        remaining_nodes
                        <= 0
                    ):
                        break

                    email_id = int(
                        email["id"]
                    )

                    email_key = (
                        f"document:"
                        f"{email_id}"
                    )

                    G.add_node(
                        email_key,
                        label=get_entity_label(
                            "document",
                            email_id,
                        ),
                        entity_type=(
                            "document"
                        ),
                        title=get_entity_title(
                            "document",
                            email_id,
                        ),
                    )

                    G.add_edge(
                        start_key,
                        email_key,
                        label=(
                            "Communication"
                        ),
                        relationship_type=(
                            "RELATES_TO_PROJECT"
                        ),
                    )

                    remaining_nodes -= 1

                    if (
                        remaining_nodes
                        <= 0
                    ):
                        break

                    people = run_query(
                        """
                        SELECT
                            relationship_type,
                            target_id
                        FROM relationships

                        WHERE source_type =
                              'document'
                          AND source_id = ?
                          AND target_type =
                              'person'
                          AND relationship_type IN (
                              'SENDER',
                              'RECIPIENT',
                              'CC'
                          )

                        LIMIT 3
                        """,
                        (
                            email_id,
                        ),
                    )

                    for _, person in (
                        people.iterrows()
                    ):

                        if (
                            remaining_nodes
                            <= 0
                        ):
                            break

                        person_id = int(
                            person[
                                "target_id"
                            ]
                        )

                        person_key = (
                            f"person:"
                            f"{person_id}"
                        )

                        G.add_node(
                            person_key,
                            label=get_entity_label(
                                "person",
                                person_id,
                            ),
                            entity_type=(
                                "person"
                            ),
                            title=get_entity_title(
                                "person",
                                person_id,
                            ),
                        )

                        G.add_edge(
                            email_key,
                            person_key,
                            label=pretty_label(
                                person[
                                    "relationship_type"
                                ],
                                RELATIONSHIP_LABELS,
                            ),
                            relationship_type=(
                                person[
                                    "relationship_type"
                                ]
                            ),
                        )

                        remaining_nodes -= 1

            # ------------------------------------------------
            # PERSON COMMUNICATIONS
            # ------------------------------------------------

            elif (
                selected_entity[
                    "type"
                ]
                == "person"
            ):

                person_id = (
                    selected_entity[
                        "id"
                    ]
                )

                communications = run_query(
                    """
                    SELECT
                        r.source_id AS document_id,
                        r.relationship_type
                    FROM relationships r

                    JOIN documents d
                      ON d.id = r.source_id

                    WHERE r.source_type = 'document'
                      AND r.target_type = 'person'
                      AND r.target_id = ?
                      AND d.document_type =
                          'correspondence'
                      AND r.relationship_type IN (
                          'SENDER',
                          'RECIPIENT',
                          'CC'
                      )

                    ORDER BY d.document_date DESC

                    LIMIT ?
                    """,
                    (
                        person_id,
                        remaining_nodes,
                    ),
                )

                for _, communication in (
                    communications.iterrows()
                ):

                    if (
                        remaining_nodes
                        <= 0
                    ):
                        break

                    email_id = int(
                        communication[
                            "document_id"
                        ]
                    )

                    email_key = (
                        f"document:"
                        f"{email_id}"
                    )

                    G.add_node(
                        email_key,
                        label=get_entity_label(
                            "document",
                            email_id,
                        ),
                        entity_type=(
                            "document"
                        ),
                        title=get_entity_title(
                            "document",
                            email_id,
                        ),
                    )

                    G.add_edge(
                        email_key,
                        start_key,
                        label=pretty_label(
                            communication[
                                "relationship_type"
                            ],
                            RELATIONSHIP_LABELS,
                        ),
                        relationship_type=(
                            communication[
                                "relationship_type"
                            ]
                        ),
                    )

                    remaining_nodes -= 1

            else:

                st.info(
                    "Communications view is most useful "
                    "for Company, Project and Person entities."
                )

        # ====================================================
        # DIRECT RELATIONSHIPS
        # ====================================================

        elif (
            graph_view
            == "Direct Relationships"
        ):

            outgoing = run_query(
                """
                SELECT
                    source_type,
                    source_id,
                    relationship_type,
                    target_type,
                    target_id,
                    confidence
                FROM relationships
                WHERE source_type = ?
                  AND source_id = ?

                ORDER BY relationship_type

                LIMIT ?
                """,
                (
                    selected_entity[
                        "type"
                    ],
                    selected_entity[
                        "id"
                    ],
                    max_nodes,
                ),
            )

            incoming = run_query(
                """
                SELECT
                    source_type,
                    source_id,
                    relationship_type,
                    target_type,
                    target_id,
                    confidence
                FROM relationships
                WHERE target_type = ?
                  AND target_id = ?

                ORDER BY relationship_type

                LIMIT ?
                """,
                (
                    selected_entity[
                        "type"
                    ],
                    selected_entity[
                        "id"
                    ],
                    max_nodes,
                ),
            )

            relationships = pd.concat(
                [
                    outgoing,
                    incoming,
                ],
                ignore_index=True,
            ).drop_duplicates()

            for _, row in (
                relationships.iterrows()
            ):

                if (
                    G.number_of_nodes()
                    >= max_nodes
                ):
                    break

                source_key = (
                    f"{row['source_type']}:"
                    f"{row['source_id']}"
                )

                target_key = (
                    f"{row['target_type']}:"
                    f"{row['target_id']}"
                )

                G.add_node(
                    source_key,
                    label=get_entity_label(
                        row[
                            "source_type"
                        ],
                        int(
                            row[
                                "source_id"
                            ]
                        ),
                    ),
                    entity_type=(
                        row[
                            "source_type"
                        ]
                    ),
                    title=get_entity_title(
                        row[
                            "source_type"
                        ],
                        int(
                            row[
                                "source_id"
                            ]
                        ),
                    ),
                )

                G.add_node(
                    target_key,
                    label=get_entity_label(
                        row[
                            "target_type"
                        ],
                        int(
                            row[
                                "target_id"
                            ]
                        ),
                    ),
                    entity_type=(
                        row[
                            "target_type"
                        ]
                    ),
                    title=get_entity_title(
                        row[
                            "target_type"
                        ],
                        int(
                            row[
                                "target_id"
                            ]
                        ),
                    ),
                )

                G.add_edge(
                    source_key,
                    target_key,
                    label=pretty_label(
                        row[
                            "relationship_type"
                        ],
                        RELATIONSHIP_LABELS,
                    ),
                    relationship_type=(
                        row[
                            "relationship_type"
                        ]
                    ),
                )

        # ====================================================
        # PYVIS GRAPH
        # ====================================================

        net = Network(
            height="700px",
            width="100%",
            directed=True,
            bgcolor="#0E1117",
            font_color="white",
        )

        net.barnes_hut(
            gravity=-12000,
            central_gravity=0.15,
            spring_length=220,
            spring_strength=0.03,
            damping=0.12,
        )

        for node_id, attrs in (
            G.nodes(
                data=True
            )
        ):

            entity_type_value = (
                attrs.get(
                    "entity_type"
                )
            )

            is_start = attrs.get(
                "start_node",
                False,
            )

            if (
                entity_type_value
                == "company"
            ):

                shape = "dot"
                size = (
                    34
                    if is_start
                    else 28
                )

            elif (
                entity_type_value
                == "project"
            ):

                shape = "square"
                size = 26

            elif (
                entity_type_value
                == "person"
            ):

                shape = "ellipse"
                size = 22

            else:

                shape = "box"
                size = 18

            net.add_node(
                node_id,
                label=attrs.get(
                    "label"
                ),
                title=attrs.get(
                    "title"
                ),
                shape=shape,
                size=size,
            )

        for source, target, attrs in (
            G.edges(
                data=True
            )
        ):

            edge_label = (
                attrs.get(
                    "label"
                )
                if show_edge_labels
                else ""
            )

            net.add_edge(
                source,
                target,
                label=edge_label,
                title=attrs.get(
                    "label"
                ),
                arrows="to",
            )

        net.set_options(
            """
            {
              "interaction": {
                "hover": true,
                "navigationButtons": true,
                "keyboard": true,
                "multiselect": false
              },
              "physics": {
                "enabled": true,
                "stabilization": {
                  "enabled": true,
                  "iterations": 300
                }
              },
              "edges": {
                "smooth": {
                  "enabled": true,
                  "type": "dynamic"
                },
                "font": {
                  "size": 11,
                  "align": "middle"
                }
              },
              "nodes": {
                "font": {
                  "size": 14
                }
              }
            }
            """
        )

        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=".html",
            mode="w",
            encoding="utf-8",
        ) as tmp_file:

            net.save_graph(
                tmp_file.name
            )

            graph_html = Path(
                tmp_file.name
            ).read_text(
                encoding="utf-8"
            )

        components.html(
            graph_html,
            height=720,
            scrolling=False,
        )

        st.caption(
            f"{graph_view} · "
            f"{G.number_of_nodes()} nodes · "
            f"{G.number_of_edges()} relationships"
        )


# ============================================================
# RESOLUTION REVIEW
# ============================================================

# ============================================================
# RESOLUTION REVIEW
# ============================================================

elif page == "Resolution Review":

    st.title(
        "Resolution Review"
    )

    st.caption(
        "Review ambiguous company aliases, inspect supporting evidence, "
        "and understand why a candidate match was classified as probable "
        "or requires manual review."
    )

    # ========================================================
    # FILTER
    # ========================================================

    display_decisions = [
        "Probable Match",
        "Manual Review Required",
    ]

    selected_display_decisions = st.multiselect(
        "Resolution Status",
        display_decisions,
        default=display_decisions,
    )

    reverse_decision_labels = {
        label: value
        for value, label in DECISION_LABELS.items()
    }

    decision_filter = [
        reverse_decision_labels[value]
        for value in selected_display_decisions
    ]

    if not decision_filter:

        st.info(
            "Select at least one resolution status."
        )

    else:

        placeholders = ",".join(
            ["?"] * len(decision_filter)
        )

        # ====================================================
        # AGGREGATED REVIEW QUEUE
        # ====================================================

        query = f"""
            SELECT
                em.observed_value,
                c.id AS candidate_company_id,
                c.canonical_name AS candidate_company,
                rc.name_score,
                rc.runner_up_score,
                rc.score_margin,
                rc.folder_match,
                rc.decision,
                rc.decision_reason,
                COUNT(*) AS occurrence_count

            FROM resolution_candidates rc

            JOIN entity_mentions em
              ON em.id = rc.mention_id

            LEFT JOIN companies c
              ON c.id = rc.candidate_entity_id

            WHERE rc.decision IN (
                {placeholders}
            )

            GROUP BY
                em.observed_value,
                c.id,
                c.canonical_name,
                rc.name_score,
                rc.runner_up_score,
                rc.score_margin,
                rc.folder_match,
                rc.decision,
                rc.decision_reason

            ORDER BY
                CASE rc.decision
                    WHEN 'review' THEN 1
                    WHEN 'probable' THEN 2
                    ELSE 3
                END,
                rc.name_score DESC,
                em.observed_value
        """

        review_df = run_query(
            query,
            tuple(decision_filter),
        )

        if review_df.empty:

            st.info(
                "No matching resolution candidates were found."
            )

        else:

            # Keep raw data for detail panel
            review_raw = review_df.copy()

            # =================================================
            # REVIEW QUEUE TABLE
            # =================================================

            review_display = review_df.copy()

            review_display["decision"] = (
                review_display["decision"]
                .map(DECISION_LABELS)
                .fillna(review_display["decision"])
            )

            review_display["folder_match"] = (
                review_display["folder_match"]
                .apply(
                    lambda value:
                        "Yes"
                        if bool(value)
                        else "No"
                )
            )

            review_display = review_display.rename(
                columns={
                    "observed_value":
                        "Observed Alias",
                    "candidate_company":
                        "Candidate Company",
                    "name_score":
                        "Match Score",
                    "runner_up_score":
                        "Runner-up Score",
                    "score_margin":
                        "Score Margin",
                    "folder_match":
                        "Folder Support",
                    "decision":
                        "Resolution Status",
                    "decision_reason":
                        "Decision Reason",
                    "occurrence_count":
                        "Occurrences",
                }
            )

            if (
                "candidate_company_id"
                in review_display.columns
            ):
                review_display = review_display.drop(
                    columns=["candidate_company_id"]
                )

            st.dataframe(
                review_display,
                use_container_width=True,
                hide_index=True,
            )

            st.caption(
                "The database retains mention-level evidence. "
                "This queue aggregates repeated aliases into "
                "reviewable candidate matches."
            )

            # =================================================
            # REVIEW DETAIL
            # =================================================

            st.divider()

            st.subheader(
                "Review Detail"
            )

            detail_labels = []

            for _, row in review_raw.iterrows():

                candidate_name = (
                    row["candidate_company"]
                    if pd.notna(
                        row["candidate_company"]
                    )
                    else "No Candidate"
                )

                decision_name = (
                    DECISION_LABELS.get(
                        row["decision"],
                        pretty_label(
                            row["decision"]
                        ),
                    )
                )

                detail_labels.append(
                    f"{row['observed_value']} "
                    f"→ {candidate_name} "
                    f"[{decision_name}]"
                )

            selected_detail_label = st.selectbox(
                "Select Candidate Review",
                detail_labels,
            )

            selected_index = (
                detail_labels.index(
                    selected_detail_label
                )
            )

            selected_review = (
                review_raw.iloc[
                    selected_index
                ]
            )

            observed_alias = (
                selected_review[
                    "observed_value"
                ]
            )

            candidate_company_id = (
                selected_review[
                    "candidate_company_id"
                ]
            )

            candidate_company_name = (
                selected_review[
                    "candidate_company"
                ]
                if pd.notna(
                    selected_review[
                        "candidate_company"
                    ]
                )
                else "No Candidate"
            )

            # =================================================
            # SUMMARY
            # =================================================

            left, right = st.columns(
                [2, 1]
            )

            with left:

                st.markdown(
                    f"### {observed_alias}"
                )

                st.write(
                    "**Candidate Company:**",
                    candidate_company_name,
                )

                st.write(
                    "**Resolution Status:**",
                    DECISION_LABELS.get(
                        selected_review[
                            "decision"
                        ],
                        pretty_label(
                            selected_review[
                                "decision"
                            ]
                        ),
                    ),
                )

                st.write(
                    "**Decision Reason:**",
                    (
                        selected_review[
                            "decision_reason"
                        ]
                        if pd.notna(
                            selected_review[
                                "decision_reason"
                            ]
                        )
                        else "-"
                    ),
                )

            with right:

                st.metric(
                    "Occurrences",
                    int(
                        selected_review[
                            "occurrence_count"
                        ]
                    ),
                )

            # =================================================
            # MATCH SCORES
            # =================================================

            col1, col2, col3, col4 = (
                st.columns(4)
            )

            with col1:

                st.metric(
                    "Match Score",
                    (
                        f"{float(selected_review['name_score']):.1f}"
                        if pd.notna(
                            selected_review[
                                "name_score"
                            ]
                        )
                        else "-"
                    ),
                )

            with col2:

                st.metric(
                    "Runner-up Score",
                    (
                        f"{float(selected_review['runner_up_score']):.1f}"
                        if pd.notna(
                            selected_review[
                                "runner_up_score"
                            ]
                        )
                        else "-"
                    ),
                )

            with col3:

                st.metric(
                    "Score Margin",
                    (
                        f"{float(selected_review['score_margin']):.1f}"
                        if pd.notna(
                            selected_review[
                                "score_margin"
                            ]
                        )
                        else "-"
                    ),
                )

            with col4:

                st.metric(
                    "Folder Support",
                    (
                        "Yes"
                        if bool(
                            selected_review[
                                "folder_match"
                            ]
                        )
                        else "No"
                    ),
                )

            # =================================================
            # DETAIL TABS
            # =================================================

            tab1, tab2, tab3 = st.tabs(
                [
                    "Supporting Source Files",
                    "Observed Mentions",
                    "Candidate Aliases",
                ]
            )

            # =================================================
            # TAB 1 - SOURCE FILES
            # =================================================

            with tab1:

                source_files = run_query(
                    """
                    SELECT DISTINCT
                        sf.filename,
                        sf.extension,
                        sf.folder_customer,
                        sf.folder_project,
                        sf.folder_category,
                        em.role,
                        em.extraction_method,
                        em.confidence,
                        em.resolution_status

                    FROM entity_mentions em

                    JOIN source_files sf
                      ON sf.id =
                         em.source_file_id

                    JOIN resolution_candidates rc
                      ON rc.mention_id =
                         em.id

                    WHERE em.entity_type =
                          'company'
                      AND em.observed_value = ?
                      AND rc.decision = ?

                    ORDER BY
                        sf.filename
                    """,
                    (
                        observed_alias,
                        selected_review[
                            "decision"
                        ],
                    ),
                )

                if source_files.empty:

                    st.info(
                        "No supporting source files were found "
                        "for this aggregated review item."
                    )

                else:

                    source_display = (
                        source_files.copy()
                    )

                    source_display["role"] = (
                        source_display["role"]
                        .apply(
                            pretty_label
                        )
                    )

                    source_display[
                        "extraction_method"
                    ] = (
                        source_display[
                            "extraction_method"
                        ]
                        .apply(
                            lambda value:
                                pretty_label(
                                    value,
                                    EXTRACTION_METHOD_LABELS,
                                )
                        )
                    )

                    source_display[
                        "confidence"
                    ] = (
                        source_display[
                            "confidence"
                        ]
                        .apply(
                            confidence_display
                        )
                    )

                    source_display[
                        "resolution_status"
                    ] = (
                        source_display[
                            "resolution_status"
                        ]
                        .apply(
                            display_status
                        )
                    )

                    source_display = (
                        source_display.rename(
                            columns={
                                "filename":
                                    "Source File",
                                "extension":
                                    "Type",
                                "folder_customer":
                                    "Folder Customer",
                                "folder_project":
                                    "Folder Project",
                                "folder_category":
                                    "Folder Category",
                                "role":
                                    "Role",
                                "extraction_method":
                                    "Extraction Method",
                                "confidence":
                                    "Confidence",
                                "resolution_status":
                                    "Mention Status",
                            }
                        )
                    )

                    st.metric(
                        "Supporting Files",
                        len(source_display),
                    )

                    st.dataframe(
                        source_display,
                        use_container_width=True,
                        hide_index=True,
                    )

            # =================================================
            # TAB 2 - OBSERVED MENTIONS
            # =================================================

            with tab2:

                mentions = run_query(
                    """
                    SELECT
                        em.observed_value,
                        em.normalized_value,
                        em.role,
                        em.extraction_method,
                        em.confidence,
                        em.resolution_status,
                        sf.filename,
                        rc.name_score,
                        rc.runner_up_score,
                        rc.score_margin,
                        rc.folder_match,
                        rc.decision,
                        rc.decision_reason

                    FROM resolution_candidates rc

                    JOIN entity_mentions em
                      ON em.id =
                         rc.mention_id

                    LEFT JOIN source_files sf
                      ON sf.id =
                         em.source_file_id

                    WHERE em.entity_type =
                          'company'
                      AND em.observed_value = ?
                      AND rc.decision = ?

                    ORDER BY
                        sf.filename,
                        em.id
                    """,
                    (
                        observed_alias,
                        selected_review[
                            "decision"
                        ],
                    ),
                )

                if mentions.empty:

                    st.info(
                        "No mention-level evidence was found."
                    )

                else:

                    mention_display = (
                        mentions.copy()
                    )

                    mention_display["role"] = (
                        mention_display["role"]
                        .apply(
                            pretty_label
                        )
                    )

                    mention_display[
                        "extraction_method"
                    ] = (
                        mention_display[
                            "extraction_method"
                        ]
                        .apply(
                            lambda value:
                                pretty_label(
                                    value,
                                    EXTRACTION_METHOD_LABELS,
                                )
                        )
                    )

                    mention_display[
                        "confidence"
                    ] = (
                        mention_display[
                            "confidence"
                        ]
                        .apply(
                            confidence_display
                        )
                    )

                    mention_display[
                        "resolution_status"
                    ] = (
                        mention_display[
                            "resolution_status"
                        ]
                        .apply(
                            display_status
                        )
                    )

                    mention_display[
                        "decision"
                    ] = (
                        mention_display[
                            "decision"
                        ]
                        .map(
                            DECISION_LABELS
                        )
                        .fillna(
                            mention_display[
                                "decision"
                            ]
                        )
                    )

                    mention_display[
                        "folder_match"
                    ] = (
                        mention_display[
                            "folder_match"
                        ]
                        .apply(
                            lambda value:
                                "Yes"
                                if bool(value)
                                else "No"
                        )
                    )

                    mention_display = (
                        mention_display.rename(
                            columns={
                                "observed_value":
                                    "Observed Value",
                                "normalized_value":
                                    "Normalized Value",
                                "role":
                                    "Role",
                                "extraction_method":
                                    "Extraction Method",
                                "confidence":
                                    "Mention Confidence",
                                "resolution_status":
                                    "Mention Status",
                                "filename":
                                    "Source File",
                                "name_score":
                                    "Match Score",
                                "runner_up_score":
                                    "Runner-up Score",
                                "score_margin":
                                    "Score Margin",
                                "folder_match":
                                    "Folder Support",
                                "decision":
                                    "Resolution Status",
                                "decision_reason":
                                    "Decision Reason",
                            }
                        )
                    )

                    st.dataframe(
                        mention_display,
                        use_container_width=True,
                        hide_index=True,
                    )

            # =================================================
            # TAB 3 - CANDIDATE ALIASES
            # =================================================

            with tab3:

                if pd.isna(
                    candidate_company_id
                ):

                    st.info(
                        "No candidate company is linked "
                        "to this review item."
                    )

                else:

                    candidate_aliases = run_query(
                        """
                        SELECT DISTINCT
                            alias,
                            confidence,
                            status

                        FROM aliases

                        WHERE entity_type =
                              'company'
                          AND entity_id = ?

                        ORDER BY alias
                        """,
                        (
                            int(
                                candidate_company_id
                            ),
                        ),
                    )

                    if candidate_aliases.empty:

                        st.info(
                            "No recorded aliases were found "
                            "for the candidate company."
                        )

                    else:

                        alias_display = (
                            candidate_aliases.copy()
                        )

                        alias_display[
                            "confidence"
                        ] = (
                            alias_display[
                                "confidence"
                            ]
                            .apply(
                                confidence_display
                            )
                        )

                        alias_display[
                            "status"
                        ] = (
                            alias_display[
                                "status"
                            ]
                            .apply(
                                display_status
                            )
                        )

                        alias_display = (
                            alias_display.rename(
                                columns={
                                    "alias":
                                        "Known Alias",
                                    "confidence":
                                        "Confidence",
                                    "status":
                                        "Status",
                                }
                            )
                        )

                        st.metric(
                            "Known Candidate Aliases",
                            len(
                                alias_display
                            ),
                        )

                        st.dataframe(
                            alias_display,
                            use_container_width=True,
                            hide_index=True,
                        )
# ============================================================
# DATA QUALITY
# ============================================================

elif page == "Data Quality":
    st.title("Data Quality & Conflicts")
    render_quality_summary()
    st.divider()

    # --------------------------------------------------------
    # Unresolved Mentions
    # --------------------------------------------------------

    st.subheader(
        "Unresolved Entity Mentions"
    )

    unresolved_mentions = run_query(
        """
        SELECT
            entity_type,
            observed_value,
            role,
            extraction_method,
            confidence,
            resolution_status

        FROM entity_mentions

        WHERE resolution_status IS NULL OR resolution_status NOT IN (
            'resolved',
            'auto_resolved'
        )

        ORDER BY
            entity_type,
            observed_value

        LIMIT 500
        """
    )

    if not unresolved_mentions.empty:

        unresolved_mentions[
            "entity_type"
        ] = unresolved_mentions[
            "entity_type"
        ].apply(
            pretty_label
        )

        unresolved_mentions[
            "role"
        ] = unresolved_mentions[
            "role"
        ].apply(
            pretty_label
        )

        unresolved_mentions[
            "extraction_method"
        ] = unresolved_mentions[
            "extraction_method"
        ].apply(
            lambda value: pretty_label(
                value,
                EXTRACTION_METHOD_LABELS,
            )
        )

        unresolved_mentions[
            "confidence"
        ] = unresolved_mentions[
            "confidence"
        ].apply(
            confidence_display
        )

        unresolved_mentions[
            "resolution_status"
        ] = unresolved_mentions[
            "resolution_status"
        ].apply(
            display_status
        )

        unresolved_mentions = (
            unresolved_mentions.rename(
                columns={
                    "entity_type":
                        "Entity Type",
                    "observed_value":
                        "Observed Value",
                    "role":
                        "Role",
                    "extraction_method":
                        "Extraction Method",
                    "confidence":
                        "Confidence",
                    "resolution_status":
                        "Resolution Status",
                }
            )
        )

    st.dataframe(
        unresolved_mentions,
        use_container_width=True,
        hide_index=True,
    )

# ============================================================
# ASK THE KNOWLEDGE MODEL
# ============================================================

elif page == "Ask the Knowledge Model":

    st.title(
        "Ask the Knowledge Model"
    )

    st.caption(
        "Query the resolved business model using canonical entities, "
        "relationships, provenance and conflict records."
    )

    st.info(
        "This assistant queries the structured knowledge model directly. "
        "It does not use a language model or re-read the original files."
    )

    # ========================================================
    # SEARCH INPUT
    # ========================================================

    query_text = st.text_input(
        "Search the Knowledge Model",
        placeholder=(
            "e.g. JOB-2026-0026, PO-3167, Falcon Aerospace, "
            "Marcus Chandra"
        ),
    )

    if not query_text.strip():

        st.markdown(
            """
            **Try searching for:**

            - `JOB-2026-0026`
            - `PO-3167`
            - `Falcon Aerospace`
            - `Marcus Chandra`
            """
        )

    else:

        search_value = query_text.strip()

        # ====================================================
        # ENTITY SEARCH
        # ====================================================

        company_matches = run_query(
            """
            SELECT
                id,
                canonical_name,
                resolution_status,
                confidence
            FROM companies

            WHERE canonical_name LIKE ?

            ORDER BY
                CASE
                    WHEN LOWER(canonical_name) = LOWER(?)
                    THEN 1
                    ELSE 2
                END,
                canonical_name

            LIMIT 10
            """,
            (
                f"%{search_value}%",
                search_value,
            ),
        )

        project_matches = run_query(
            """
            SELECT
                id,
                job_number,
                canonical_name,
                resolution_status,
                confidence
            FROM projects

            WHERE job_number LIKE ?
               OR canonical_name LIKE ?

            ORDER BY
                CASE
                    WHEN LOWER(job_number) = LOWER(?)
                    THEN 1
                    ELSE 2
                END,
                job_number

            LIMIT 10
            """,
            (
                f"%{search_value}%",
                f"%{search_value}%",
                search_value,
            ),
        )

        person_matches = run_query(
            """
            SELECT
                id,
                canonical_name,
                email,
                resolution_status,
                confidence
            FROM people

            WHERE canonical_name LIKE ?
               OR email LIKE ?

            ORDER BY
                CASE
                    WHEN LOWER(canonical_name) = LOWER(?)
                    THEN 1
                    ELSE 2
                END,
                canonical_name

            LIMIT 10
            """,
            (
                f"%{search_value}%",
                f"%{search_value}%",
                search_value,
            ),
        )

        document_matches = run_query(
            """
            SELECT
                id,
                document_type,
                document_number,
                title,
                document_date,
                resolution_status,
                extraction_confidence
            FROM documents

            WHERE document_number LIKE ?
               OR title LIKE ?

            ORDER BY
                CASE
                    WHEN LOWER(document_number) = LOWER(?)
                    THEN 1
                    ELSE 2
                END,
                document_number

            LIMIT 10
            """,
            (
                f"%{search_value}%",
                f"%{search_value}%",
                search_value,
            ),
        )

        total_matches = (
            len(company_matches)
            + len(project_matches)
            + len(person_matches)
            + len(document_matches)
        )

        # ====================================================
        # NO MATCH
        # ====================================================

        if total_matches == 0:

            st.warning(
                "No matching canonical entity or logical document "
                "was found in the knowledge model."
            )

        else:

            # =================================================
            # BUILD RESULT OPTIONS
            # =================================================

            result_options = []

            for _, row in company_matches.iterrows():

                result_options.append(
                    {
                        "type": "company",
                        "id": int(row["id"]),
                        "label": (
                            f"Company — "
                            f"{row['canonical_name']}"
                        ),
                    }
                )

            for _, row in project_matches.iterrows():

                project_name = (
                    row["canonical_name"]
                    if pd.notna(
                        row["canonical_name"]
                    )
                    else ""
                )

                result_options.append(
                    {
                        "type": "project",
                        "id": int(row["id"]),
                        "label": (
                            f"Project — "
                            f"{row['job_number']} — "
                            f"{project_name}"
                        ),
                    }
                )

            for _, row in person_matches.iterrows():

                email = (
                    row["email"]
                    if pd.notna(
                        row["email"]
                    )
                    else ""
                )

                result_options.append(
                    {
                        "type": "person",
                        "id": int(row["id"]),
                        "label": (
                            f"Person — "
                            f"{row['canonical_name']}"
                            + (
                                f" — {email}"
                                if email
                                else ""
                            )
                        ),
                    }
                )

            for _, row in document_matches.iterrows():

                number = (
                    row["document_number"]
                    if pd.notna(
                        row["document_number"]
                    )
                    else ""
                )

                title = (
                    row["title"]
                    if pd.notna(
                        row["title"]
                    )
                    else ""
                )

                document_label = (
                    number
                    if number
                    else shorten_text(
                        title,
                        50,
                    )
                )

                result_options.append(
                    {
                        "type": "document",
                        "id": int(row["id"]),
                        "label": (
                            f"{pretty_label(row['document_type'])} — "
                            f"{document_label}"
                        ),
                    }
                )

            st.success(
                f"{total_matches} matching record(s) found."
            )

            selected_result_label = st.selectbox(
                "Select Result",
                [
                    result["label"]
                    for result
                    in result_options
                ],
            )

            selected_result = next(
                result
                for result
                in result_options
                if result["label"]
                == selected_result_label
            )

            selected_type = (
                selected_result["type"]
            )

            selected_id = (
                selected_result["id"]
            )

            st.divider()

            # =================================================
            # COMPANY SUMMARY
            # =================================================

            if selected_type == "company":

                company = run_query(
                    """
                    SELECT
                        id,
                        canonical_name,
                        resolution_status,
                        confidence
                    FROM companies

                    WHERE id = ?
                    """,
                    (
                        selected_id,
                    ),
                )

                if not company.empty:

                    row = company.iloc[0]

                    st.header(
                        row["canonical_name"]
                    )

                    col1, col2, col3 = st.columns(
                        3
                    )

                    with col1:

                        st.metric(
                            "Entity Type",
                            "Company",
                        )

                    with col2:

                        st.metric(
                            "Resolution Status",
                            display_status(
                                row[
                                    "resolution_status"
                                ]
                            ),
                        )

                    with col3:

                        st.metric(
                            "Confidence",
                            confidence_display(
                                row[
                                    "confidence"
                                ]
                            ),
                        )

                # ---------------------------------------------
                # Projects
                # ---------------------------------------------

                st.subheader(
                    "Projects"
                )

                company_projects = run_query(
                    """
                    SELECT
                        p.id,
                        p.job_number,
                        p.canonical_name,
                        r.confidence,
                        r.status

                    FROM relationships r

                    JOIN projects p
                      ON p.id = r.target_id

                    WHERE r.source_type =
                          'company'
                      AND r.source_id = ?
                      AND r.relationship_type =
                          'HAS_PROJECT'
                      AND r.target_type =
                          'project'

                    ORDER BY
                        p.job_number
                    """,
                    (
                        selected_id,
                    ),
                )

                if company_projects.empty:

                    st.info(
                        "No projects are linked to this company."
                    )

                else:

                    company_projects[
                        "confidence"
                    ] = (
                        company_projects[
                            "confidence"
                        ]
                        .apply(
                            confidence_display
                        )
                    )

                    company_projects[
                        "status"
                    ] = (
                        company_projects[
                            "status"
                        ]
                        .apply(
                            display_status
                        )
                    )

                    company_projects = (
                        company_projects.rename(
                            columns={
                                "job_number":
                                    "Job Number",
                                "canonical_name":
                                    "Project Name",
                                "confidence":
                                    "Confidence",
                                "status":
                                    "Status",
                            }
                        )
                    )

                    company_projects = (
                        company_projects.drop(
                            columns=["id"]
                        )
                    )

                    st.dataframe(
                        company_projects,
                        use_container_width=True,
                        hide_index=True,
                    )

                # ---------------------------------------------
                # Aliases
                # ---------------------------------------------

                st.subheader(
                    "Known Aliases"
                )

                aliases = run_query(
                    """
                    SELECT DISTINCT
                        alias,
                        confidence,
                        status

                    FROM aliases

                    WHERE entity_type =
                          'company'
                      AND entity_id = ?

                    ORDER BY alias
                    """,
                    (
                        selected_id,
                    ),
                )

                if aliases.empty:

                    st.info(
                        "No aliases are recorded."
                    )

                else:

                    aliases[
                        "confidence"
                    ] = aliases[
                        "confidence"
                    ].apply(
                        confidence_display
                    )

                    aliases[
                        "status"
                    ] = aliases[
                        "status"
                    ].apply(
                        display_status
                    )

                    aliases = aliases.rename(
                        columns={
                            "alias":
                                "Alias",
                            "confidence":
                                "Confidence",
                            "status":
                                "Status",
                        }
                    )

                    st.dataframe(
                        aliases,
                        use_container_width=True,
                        hide_index=True,
                    )

                # ---------------------------------------------
                # Conflicts
                # ---------------------------------------------

                st.subheader(
                    "Conflicts"
                )

                company_conflicts = run_query(
                    """
                    SELECT
                        conflict_type,
                        description,
                        severity,
                        status

                    FROM conflicts

                    WHERE subject_type =
                          'company'
                      AND subject_id = ?

                    ORDER BY
                        id DESC
                    """,
                    (
                        selected_id,
                    ),
                )

                if company_conflicts.empty:

                    st.success(
                        "No direct conflicts are recorded "
                        "for this company."
                    )

                else:

                    company_conflicts[
                        "conflict_type"
                    ] = (
                        company_conflicts[
                            "conflict_type"
                        ]
                        .apply(
                            pretty_label
                        )
                    )

                    company_conflicts[
                        "severity"
                    ] = (
                        company_conflicts[
                            "severity"
                        ]
                        .apply(
                            pretty_label
                        )
                    )

                    company_conflicts[
                        "status"
                    ] = (
                        company_conflicts[
                            "status"
                        ]
                        .apply(
                            display_status
                        )
                    )

                    company_conflicts = (
                        company_conflicts.rename(
                            columns={
                                "conflict_type":
                                    "Conflict Type",
                                "description":
                                    "Description",
                                "severity":
                                    "Severity",
                                "status":
                                    "Status",
                            }
                        )
                    )

                    st.dataframe(
                        company_conflicts,
                        use_container_width=True,
                        hide_index=True,
                    )

                render_evidence_panel(
                    "company",
                    selected_id,
                )

            # =================================================
            # PROJECT SUMMARY
            # =================================================

            elif selected_type == "project":

                project = run_query(
                    """
                    SELECT
                        id,
                        job_number,
                        canonical_name,
                        resolution_status,
                        confidence

                    FROM projects

                    WHERE id = ?
                    """,
                    (
                        selected_id,
                    ),
                )

                if not project.empty:

                    row = project.iloc[0]

                    heading = (
                        f"{row['job_number']} — "
                        f"{row['canonical_name']}"
                    )

                    st.header(
                        heading
                    )

                    col1, col2, col3 = (
                        st.columns(3)
                    )

                    with col1:

                        st.metric(
                            "Entity Type",
                            "Project",
                        )

                    with col2:

                        st.metric(
                            "Resolution Status",
                            display_status(
                                row[
                                    "resolution_status"
                                ]
                            ),
                        )

                    with col3:

                        st.metric(
                            "Confidence",
                            confidence_display(
                                row[
                                    "confidence"
                                ]
                            ),
                        )

                # ---------------------------------------------
                # Company
                # ---------------------------------------------

                st.subheader(
                    "Company"
                )

                project_company = run_query(
                    """
                    SELECT
                        c.canonical_name,
                        r.confidence,
                        r.status

                    FROM relationships r

                    JOIN companies c
                      ON c.id = r.source_id

                    WHERE r.source_type =
                          'company'
                      AND r.relationship_type =
                          'HAS_PROJECT'
                      AND r.target_type =
                          'project'
                      AND r.target_id = ?
                    """,
                    (
                        selected_id,
                    ),
                )

                if project_company.empty:

                    st.info(
                        "No company relationship was found."
                    )

                else:

                    project_company[
                        "confidence"
                    ] = (
                        project_company[
                            "confidence"
                        ]
                        .apply(
                            confidence_display
                        )
                    )

                    project_company[
                        "status"
                    ] = (
                        project_company[
                            "status"
                        ]
                        .apply(
                            display_status
                        )
                    )

                    project_company = (
                        project_company.rename(
                            columns={
                                "canonical_name":
                                    "Company",
                                "confidence":
                                    "Confidence",
                                "status":
                                    "Status",
                            }
                        )
                    )

                    st.dataframe(
                        project_company,
                        use_container_width=True,
                        hide_index=True,
                    )

                # ---------------------------------------------
                # Documents
                # ---------------------------------------------

                st.subheader(
                    "Related Documents"
                )

                project_documents = run_query(
                    """
                    SELECT
                        d.document_type,
                        d.document_number,
                        d.title,
                        d.document_date,
                        r.confidence,
                        r.status

                    FROM relationships r

                    JOIN documents d
                      ON d.id = r.source_id

                    WHERE r.source_type =
                          'document'
                      AND r.relationship_type =
                          'RELATES_TO_PROJECT'
                      AND r.target_type =
                          'project'
                      AND r.target_id = ?

                    ORDER BY
                        d.document_type,
                        d.document_number
                    """,
                    (
                        selected_id,
                    ),
                )

                if project_documents.empty:

                    st.info(
                        "No documents are linked to this project."
                    )

                else:

                    project_documents[
                        "document_type"
                    ] = (
                        project_documents[
                            "document_type"
                        ]
                        .apply(
                            pretty_label
                        )
                    )

                    project_documents[
                        "confidence"
                    ] = (
                        project_documents[
                            "confidence"
                        ]
                        .apply(
                            confidence_display
                        )
                    )

                    project_documents[
                        "status"
                    ] = (
                        project_documents[
                            "status"
                        ]
                        .apply(
                            display_status
                        )
                    )

                    project_documents = (
                        project_documents.rename(
                            columns={
                                "document_type":
                                    "Document Type",
                                "document_number":
                                    "Document Number",
                                "title":
                                    "Title",
                                "document_date":
                                    "Date",
                                "confidence":
                                    "Confidence",
                                "status":
                                    "Status",
                            }
                        )
                    )

                    st.dataframe(
                        project_documents,
                        use_container_width=True,
                        hide_index=True,
                    )

                # ---------------------------------------------
                # People involved
                # ---------------------------------------------

                st.subheader(
                    "People Involved"
                )

                project_people = run_query(
                    """
                    SELECT DISTINCT
                        p.canonical_name,
                        p.email,
                        pr.relationship_type

                    FROM relationships project_rel

                    JOIN documents d
                      ON d.id =
                         project_rel.source_id

                    JOIN relationships pr
                      ON pr.source_type =
                         'document'
                     AND pr.source_id =
                         d.id
                     AND pr.target_type =
                         'person'

                    JOIN people p
                      ON p.id =
                         pr.target_id

                    WHERE project_rel.source_type =
                          'document'
                      AND project_rel.relationship_type =
                          'RELATES_TO_PROJECT'
                      AND project_rel.target_type =
                          'project'
                      AND project_rel.target_id = ?
                      AND pr.relationship_type IN (
                          'SENDER',
                          'RECIPIENT',
                          'CC'
                      )

                    ORDER BY
                        p.canonical_name
                    """,
                    (
                        selected_id,
                    ),
                )

                if project_people.empty:

                    st.info(
                        "No people were identified through "
                        "project communications."
                    )

                else:

                    project_people[
                        "relationship_type"
                    ] = (
                        project_people[
                            "relationship_type"
                        ]
                        .apply(
                            lambda value:
                                pretty_label(
                                    value,
                                    RELATIONSHIP_LABELS,
                                )
                        )
                    )

                    project_people = (
                        project_people.rename(
                            columns={
                                "canonical_name":
                                    "Person",
                                "email":
                                    "Email",
                                "relationship_type":
                                    "Communication Role",
                            }
                        )
                    )

                    st.dataframe(
                        project_people,
                        use_container_width=True,
                        hide_index=True,
                    )

                render_evidence_panel(
                    "project",
                    selected_id,
                )

            # =================================================
            # PERSON SUMMARY
            # =================================================

            elif selected_type == "person":

                person = run_query(
                    """
                    SELECT
                        canonical_name,
                        email,
                        resolution_status,
                        confidence

                    FROM people

                    WHERE id = ?
                    """,
                    (
                        selected_id,
                    ),
                )

                if not person.empty:

                    row = person.iloc[0]

                    st.header(
                        row[
                            "canonical_name"
                        ]
                    )

                    st.write(
                        "**Email:**",
                        (
                            row["email"]
                            if pd.notna(
                                row["email"]
                            )
                            else "-"
                        ),
                    )

                    st.write(
                        "**Resolution Status:**",
                        display_status(
                            row[
                                "resolution_status"
                            ]
                        ),
                    )

                st.subheader(
                    "Communications"
                )

                person_communications = run_query(
                    """
                    SELECT
                        d.title,
                        d.document_date,
                        r.relationship_type,
                        r.confidence

                    FROM relationships r

                    JOIN documents d
                      ON d.id = r.source_id

                    WHERE r.source_type =
                          'document'
                      AND r.target_type =
                          'person'
                      AND r.target_id = ?

                    ORDER BY
                        d.document_date DESC
                    """,
                    (
                        selected_id,
                    ),
                )

                if person_communications.empty:

                    st.info(
                        "No communications were found."
                    )

                else:

                    person_communications[
                        "relationship_type"
                    ] = (
                        person_communications[
                            "relationship_type"
                        ]
                        .apply(
                            lambda value:
                                pretty_label(
                                    value,
                                    RELATIONSHIP_LABELS,
                                )
                        )
                    )

                    person_communications[
                        "confidence"
                    ] = (
                        person_communications[
                            "confidence"
                        ]
                        .apply(
                            confidence_display
                        )
                    )

                    person_communications = (
                        person_communications.rename(
                            columns={
                                "title":
                                    "Subject",
                                "document_date":
                                    "Date",
                                "relationship_type":
                                    "Role",
                                "confidence":
                                    "Confidence",
                            }
                        )
                    )

                    st.dataframe(
                        person_communications,
                        use_container_width=True,
                        hide_index=True,
                    )

                render_evidence_panel(
                    "person",
                    selected_id,
                )

            # =================================================
            # DOCUMENT SUMMARY
            # =================================================

            elif selected_type == "document":

                document = run_query(
                    """
                    SELECT
                        document_type,
                        document_number,
                        title,
                        document_date,
                        resolution_status,
                        extraction_confidence

                    FROM documents

                    WHERE id = ?
                    """,
                    (
                        selected_id,
                    ),
                )

                if not document.empty:

                    row = document.iloc[0]

                    heading = (
                        row["document_number"]
                        if pd.notna(
                            row["document_number"]
                        )
                        else row["title"]
                    )

                    st.header(
                        heading
                    )

                    col1, col2, col3 = (
                        st.columns(3)
                    )

                    with col1:

                        st.metric(
                            "Document Type",
                            pretty_label(
                                row[
                                    "document_type"
                                ]
                            ),
                        )

                    with col2:

                        st.metric(
                            "Resolution Status",
                            display_status(
                                row[
                                    "resolution_status"
                                ]
                            ),
                        )

                    with col3:

                        st.metric(
                            "Confidence",
                            confidence_display(
                                row[
                                    "extraction_confidence"
                                ]
                            ),
                        )

                    st.write(
                        "**Title:**",
                        (
                            row["title"]
                            if pd.notna(
                                row["title"]
                            )
                            else "-"
                        ),
                    )

                    st.write(
                        "**Date:**",
                        (
                            row["document_date"]
                            if pd.notna(
                                row["document_date"]
                            )
                            else "-"
                        ),
                    )

                # ---------------------------------------------
                # Relationships
                # ---------------------------------------------

                st.subheader(
                    "Relationships"
                )

                document_relationships = (
                    get_relationship_display_table(
                        "document",
                        selected_id,
                    )
                )

                if document_relationships.empty:

                    st.info(
                        "No relationships are recorded "
                        "for this document."
                    )

                else:

                    st.dataframe(
                        document_relationships,
                        use_container_width=True,
                        hide_index=True,
                    )

                # ---------------------------------------------
                # Referenced Documents
                # ---------------------------------------------

                st.subheader(
                    "Document References"
                )

                references = run_query(
                    """
                    SELECT
                        d.document_type,
                        d.document_number,
                        d.title,
                        r.confidence,
                        r.status

                    FROM relationships r

                    JOIN documents d
                      ON d.id = r.target_id

                    WHERE r.source_type =
                          'document'
                      AND r.source_id = ?
                      AND r.relationship_type =
                          'REFERENCES'
                      AND r.target_type =
                          'document'

                    ORDER BY
                        d.document_number
                    """,
                    (
                        selected_id,
                    ),
                )

                if references.empty:

                    st.info(
                        "No resolved outgoing document references "
                        "were found."
                    )

                else:

                    references[
                        "document_type"
                    ] = (
                        references[
                            "document_type"
                        ]
                        .apply(
                            pretty_label
                        )
                    )

                    references[
                        "confidence"
                    ] = (
                        references[
                            "confidence"
                        ]
                        .apply(
                            confidence_display
                        )
                    )

                    references[
                        "status"
                    ] = (
                        references[
                            "status"
                        ]
                        .apply(
                            display_status
                        )
                    )

                    references = references.rename(
                        columns={
                            "document_type":
                                "Document Type",
                            "document_number":
                                "Document Number",
                            "title":
                                "Title",
                            "confidence":
                                "Confidence",
                            "status":
                                "Status",
                        }
                    )

                    st.dataframe(
                        references,
                        use_container_width=True,
                        hide_index=True,
                    )

                render_evidence_panel(
                    "document",
                    selected_id,
                )

# ============================================================
# RELATIONSHIPS
# ============================================================

elif page == "Relationships":

    st.title(
        "Relationship Explorer"
    )

    relationship_types = run_query(
        """
        SELECT DISTINCT
            relationship_type
        FROM relationships
        ORDER BY relationship_type
        """
    )

    available_types = (
        relationship_types[
            "relationship_type"
        ].tolist()
        if not relationship_types.empty
        else []
    )

    selected_types = st.multiselect(
        "Relationship Types",
        available_types,
        default=available_types,
        format_func=lambda value:
            pretty_label(
                value,
                RELATIONSHIP_LABELS,
            ),
    )

    if selected_types:

        placeholders = ",".join(
            ["?"] * len(
                selected_types
            )
        )

        relationships = run_query(
            f"""
            SELECT
                id,
                source_type,
                source_id,
                relationship_type,
                target_type,
                target_id,
                confidence,
                status

            FROM relationships

            WHERE relationship_type IN (
                {placeholders}
            )

            ORDER BY
                relationship_type,
                source_type,
                source_id

            LIMIT 2000
            """,
            tuple(
                selected_types
            ),
        )

        relationships = format_relationships(relationships)

        st.dataframe(
            relationships,
            use_container_width=True,
            hide_index=True,
        )

        st.caption(
            f"Showing up to 2,000 relationships. "
            f"Current result count: "
            f"{len(relationships)}"
        )

    else:

        st.info(
            "Select at least one relationship type."
        )