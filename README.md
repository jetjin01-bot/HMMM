Elgoog
The project ingests heterogeneous business files, resolves canonical entities and logical documents, preserves provenance and conflicts, and exposes the resulting knowledge model through an interactive Streamlit interface.

1.	Project Overview

Companies rarely store operational knowledge in one clean system where the same customer, project, person, transaction or document may appear across:

- PDFs
- emails
- spreadsheets
- folders
- filenames
- invoices
- quotations
- purchase orders
- delivery notes
- correspondence
- drawings
- specifications

The challenge is therefore not simply to extract text but is to determine:
- what real entities exist
- when two observations refer to the same entity
- how documents relate to projects and companies
- which evidence supports each relationship
- what to do when evidence disagrees
- when a system should refuse to resolve something automatically

This project builds a structured, queryable and explainable business knowledge model over fragmented source files.

2. Problem Framing

The system is designed around several principles:
> Folder structure is evidence, not truth.
> Content can override filenames and folder placement when stronger evidence exists.
> Canonical entities use stable internal IDs.
> Observed aliases are preserved rather than overwritten.
> Ambiguous matches are surfaced for review rather than aggressively merged.
> Relationships retain confidence and provenance.
> Conflicting evidence is preserved instead of silently selecting a winner.

The objective is not to create a searchable pile of extracted text. The objective is to build a model precise enough that downstream software can reason over companies, projects, people, documents and relationships.

3. What I Built

The pipeline currently supports:
	Source ingestion
- recursive file discovery
- file metadata capture
- SHA-256 hashing
- folder context capture
- source file persistence

	Document resolution
- filename based document identity detection
- content-based document identity verification
- logical document creation
- source file to logical document linking
- filename/content conflict detection

	Entity extraction
- company mentions
- project mentions
- person mentions from email headers
- aliases from labelled PDF fields

	Canonical entity resolution
- canonical companies
- canonical projects
- canonical people
- stable internal IDs
- alias preservation
- confidence tracking
- candidate match review

	Relationship construction
- Company → Project
- Document → Project
- Document → Company
- Document → Person
- Document → Document references

	Provenance and data quality
- extraction issues
- unresolved references
- filename/content conflicts
- evidence records
- source file traceability
- human review queue

	User interface
- Overview
- Entity Explorer
- Graph Explorer
- Resolution Review
- Data Quality
- Relationship Explorer
- Ask the Knowledge Model

4. Architecture

The system uses a deterministic-first architecture.
Source Files
    ↓
File Scanner
    ↓
Content Extraction
    ↓
Document Identification
    ↓
Logical Document Resolution
    ↓
Entity Mention Extraction
    ↓
Canonical Entity Resolution
    ↓
Relationship Construction
    ↓
Evidence / Conflict Persistence
    ↓
SQLite Knowledge Model
    ↓
Streamlit Exploration Interface
The SQLite database is treated as a derived, rebuildable knowledge model over the source corpus. The original files remain as the evidence layer.
________________________________________
5. Technology Stack
Core
•	Python
•	SQLite
•	Pandas
Document processing
•	PyMuPDF
Entity resolution
•	deterministic normalization
•	alias matching
•	RapidFuzz-assisted candidate scoring
Graph exploration
•	NetworkX
•	PyVis
Interface
•	Streamlit
The current implementation intentionally does not depend on an LLM for core resolution logic.
This keeps entity resolution reproducible and makes each decision easier to explain.
________________________________________
6. Data Model
The core business entities are:
Company
Represents a canonical organisation.
Examples:
•	Falcon Aerospace Components Ltd
•	Acme Corporation
Project
Represents a canonical job or project.
Example:
•	JOB-2026-0026
Person
Represents a canonical individual, generally identified through email evidence.
Document
Represents a logical business document rather than a physical file.
Supported document categories include:
•	Invoice
•	Quotation
•	Purchase Order
•	Delivery Note
•	Drawing
•	Specification
•	Correspondence
•	Contract
•	Other
Source File
A source file is the physical artefact found in the original corpus.
A logical document may be supported by one or more source files.
This distinction is important because duplicated, revised or renamed files do not necessarily represent distinct business documents.
________________________________________
7. Source File vs Logical Document
The system deliberately separates physical files from logical documents.
For example:
INV-8189.pdf
INV-8189 FINAL.pdf
INV-8189 revised(1).pdf
may all represent the same logical invoice.
The source files remain independently traceable, while the knowledge model can reason over one logical document.
This avoids treating every filename as a separate business object.
________________________________________
8. Document Resolution Strategy
Document identity is determined using multiple signals.
Filename evidence
Structured filename prefixes such as:
INV-
QUO-
PO-
DN-
DWG-
provide an initial document identity signal.
Content evidence
Labelled fields extracted from the document body are treated as stronger evidence when available.
Examples:
Invoice No:
Quotation No:
PO Ref:
Delivery Note:
Conflict handling
If the filename suggests:
QUO-5238
but the extracted document content identifies:
QUO-5239
the system does not silently select one and discard the other.
It resolves the logical document using the stronger content evidence and records a:
filename_content_mismatch
conflict for review and provenance.
________________________________________
9. Entity Resolution Strategy
Entity resolution is intentionally conservative.
A wrong merge is more dangerous than an unresolved alias because an incorrect merge can silently corrupt downstream relationships.
The resolution process therefore uses:
1.	normalized exact matches
2.	known alias matches
3.	candidate scoring
4.	folder evidence where available
5.	score margin against the runner-up candidate
6.	human review when evidence is insufficient
Example:
Observed Alias:
Falcon Aerospace

Candidate:
Falcon Aerospace Components Ltd

Match Score:
89.8

Runner-up Score:
45.5

Score Margin:
44.3

Folder Support:
No
Despite a strong name score and large margin, the system can still classify the match as:
Manual Review Required
when independent evidence is insufficient.
This is intentional.
________________________________________
10. Human-in-the-Loop Resolution
The Resolution Review screen exposes ambiguous matches rather than hiding them.
For each candidate, the interface displays:
•	observed alias
•	candidate company
•	match score
•	runner-up score
•	score margin
•	folder support
•	decision reason
•	occurrence count
•	supporting source files
•	observed mentions
•	known candidate aliases
This allows a reviewer to inspect why a match was proposed and what evidence supports it.
The system therefore treats uncertainty as an explicit state rather than forcing every record into a canonical entity.
________________________________________
11. Relationship Model
The current relationship vocabulary includes:
HAS_PROJECT
RELATES_TO_PROJECT
RELATES_TO_COMPANY
BILL_TO
CUSTOMER
SUPPLIER
SHIP_TO
ISSUED_BY
SENDER
RECIPIENT
CC
REFERENCES
Examples:
Company
	HAS_PROJECT
	Project
Document
	RELATES_TO_PROJECT
	Project
Invoice
	BILL_TO
	Company
Email
	SENDER
	Person
	RECIPIENT		
	Person
Delivery Note
	REFERENCES
	Purchase Order
Relationships are stored separately from entities and retain confidence and status information.
________________________________________
12. Provenance
The system is designed so that resolved information can be traced back to its supporting evidence.
The interface exposes:
•	evidence records
•	source files
•	entity mentions
•	relationships
•	conflicts
This allows a user to answer:
Why does this entity exist?
Which file supports this alias?
Why was this company linked to this project?
Which document established this relationship?
Provenance is treated as part of the model rather than as an afterthought.
________________________________________
13. Conflict Handling
Conflicting evidence is preserved.
Current conflict categories include:
Extraction issues
Files that cannot be read successfully or contain no extractable text.
Unresolved document references
A document contains an explicit reference such as a PO or quotation number, but the referenced logical document cannot be found.
Filename / content conflicts
The filename identity disagrees with the document identity found inside the document content.
The system records these issues in a persistent conflict register.
This makes data-quality problems queryable and reproducible.
________________________________________
14. Data Quality
The current corpus produces recorded issues across several categories.
Example:
Extraction Issues
Unresolved References
Filename / Content Conflicts
These are persisted rather than only printed during execution.
The data-quality layer is also designed to be idempotent, so repeated pipeline runs do not create duplicate conflict records.
________________________________________
15. Graph Explorer
The Graph Explorer provides several focused views.
Business Structure
Displays:
Company
→ Projects
→ Representative Documents
Documents
Shows relationships directly involving logical documents.
Communications
Displays:
Company / Project
→ Correspondence
→ People
Direct Relationships
Provides a one-hop inspection view around the selected entity.
The graph intentionally limits node counts to avoid producing an unreadable global hairball.
________________________________________
16. Ask the Knowledge Model
The application includes a deterministic query interface over the resolved knowledge model.
It can search for:
•	companies
•	projects
•	people
•	documents
Example queries:
JOB-2026-0026
PO-3167
Falcon Aerospace
Marcus Chandra
The result page can expose:
•	entity summary
•	related projects
•	related documents
•	people involved
•	references
•	relationships
•	conflicts
•	provenance
This feature queries the structured SQLite model directly.
It does not use an LLM and does not re-read all source files at query time.
________________________________________
17. Example Investigation
A useful demonstration path is:
Falcon Aerospace
    ↓
JOB-2026-0026
    ↓
Quotation / Purchase Order / Delivery Note / Invoice
    ↓
Document References
    ↓
Email Correspondence
    ↓
People
    ↓
Resolution Evidence
    ↓
Conflicts
This demonstrates that the system is resolving a connected business model rather than independently extracting files.
________________________________________
18. Pipeline Order
The rebuild process currently follows this sequence:
1. Initialise / reset the derived database
2. Scan source files
3. Audit extraction issues
4. Build initial logical documents
5. Verify document identity
6. Resolve PDF logical documents
7. Extract entity mentions
8. Resolve companies and projects
9. Extract and resolve people
10. Extract PDF company aliases
11. Resolve company candidates
12. Build relationships
13. Resolve document references
14. Persist unresolved references and conflicts
The database can therefore be regenerated from the source corpus.
________________________________________
19. Key Design Decisions
Deterministic first
The core resolution pipeline does not require an LLM.
This improves:
•	reproducibility
•	explainability
•	debugging
•	testability
Do not trust folder structure blindly
Folders contribute evidence but do not determine truth.
Separate observations from canonical entities
Observed names remain available as mentions and aliases.
Preserve ambiguity
Low-confidence or insufficiently supported candidates remain unresolved.
Preserve conflicts
Conflicting evidence is stored rather than overwritten.
Separate source files from logical documents
Physical storage and business identity are different concepts.
Rich model first
The knowledge model retains relationships, provenance and confidence even when downstream systems may eventually need simpler flattened views.
________________________________________
20. Limitations
The current implementation is intentionally scoped.
OCR
Image-only and scanned PDFs without a text layer are currently surfaced as extraction issues.
A production system would add an OCR or document-understanding stage.
Entity resolution
Candidate matching is currently strongest for company aliases.
More general entity-resolution policies would be needed for people, products, sites and other entity classes.
Relationship validation
A detected reference is treated as observed evidence.
The system does not yet fully determine whether the referenced documents form a valid transaction chain.
For example, contradictory dates or business semantics may require an additional validation layer.
Manual review actions
The current interface explains review candidates but does not yet provide a full accept / reject / merge / split workflow.
Schema discovery
The current business entity and relationship vocabulary is predefined.
A production implementation would likely require governed schema extension.
Incremental updates
The current demo favors reproducible full rebuilds.
A production system would require efficient incremental ingestion and re-resolution.
________________________________________
21. Future Improvements
Potential next steps include:
OCR and multimodal extraction
Add OCR / document vision support for scanned PDFs, handwriting and images.
Review write-back
Allow reviewers to:
•	approve a candidate
•	reject a candidate
•	manually select another entity
•	merge entities
•	split entities
Stronger relationship validation
Use dates, amounts and reference chains to distinguish:
observed reference
from:
validated transaction relationship
Candidate blocking
Reduce the search space before approximate matching for larger datasets.
Incremental graph recomputation
Recompute only affected entities and relationships when new files arrive.
Natural-language query layer
Add an LLM as an optional interpretation layer over the already-resolved structured model.
The LLM would not be responsible for entity resolution or uncontrolled database writes.
________________________________________
22. Final Note
The main goal of this project is not maximum extraction coverage.
It is to demonstrate how fragmented enterprise data can be transformed into a structured, evidence-backed and reviewable knowledge model without hiding uncertainty.
The key design principle is simple:
When the evidence is strong, resolve deterministically.
When the evidence conflicts, preserve the conflict.

