import re
from pathlib import Path

from SRC.Ingestion.extraction.content_extractor import extract_content


PRIMARY_DOCUMENT_PATTERNS = {
    "invoice": [
        re.compile(
            r"Invoice\s*(?:Number|No\.?|#)\s*[:\-]?\s*(INV[- ]?\d+)",
            re.IGNORECASE,
        ),
    ],

    "quotation": [
        re.compile(
            r"(?:Quotation|Quote)\s*(?:Number|No\.?|#)\s*[:\-]?\s*(QUO[- ]?\d+)",
            re.IGNORECASE,
        ),
    ],

    "purchase_order": [
        re.compile(
            r"(?:Purchase\s*Order|PO)\s*(?:Number|No\.?|#)\s*[:\-]?\s*(PO[- ]?\d+)",
            re.IGNORECASE,
        ),
    ],

    "delivery_note": [
        re.compile(
            r"(?:Delivery\s*Note|DN)\s*(?:Number|No\.?|#)\s*[:\-]?\s*(DN[- ]?\d+)",
            re.IGNORECASE,
        ),
    ],

    "drawing": [
        re.compile(
            r"(?:Drawing|DWG)\s*(?:Number|No\.?|#)\s*[:\-]?\s*(DWG[- ]?\d+)",
            re.IGNORECASE,
        ),
    ],
}


FALLBACK_PATTERNS = {
    "invoice": re.compile(
        r"\bINV[- ]?(\d+)\b",
        re.IGNORECASE,
    ),

    "quotation": re.compile(
        r"\bQUO[- ]?(\d+)\b",
        re.IGNORECASE,
    ),

    "purchase_order": re.compile(
        r"\bPO[- ]?(\d+)\b",
        re.IGNORECASE,
    ),

    "delivery_note": re.compile(
        r"\bDN[- ]?(\d+)\b",
        re.IGNORECASE,
    ),

    "drawing": re.compile(
        r"\bDWG[- ]?(\d+)\b",
        re.IGNORECASE,
    ),
}


PREFIXES = {
    "invoice": "INV",
    "quotation": "QUO",
    "purchase_order": "PO",
    "delivery_note": "DN",
    "drawing": "DWG",
}


def normalize_document_number(value: str) -> str:
    """
    Normalize document numbers into forms such as:
    INV-8190
    QUO-5259
    PO-3167
    DN-6029
    DWG-7686
    """

    value = value.upper().replace(" ", "-")

    match = re.search(
        r"(INV|QUO|PO|DN|DWG)-?(\d+)",
        value,
    )

    if not match:
        return value

    return f"{match.group(1)}-{match.group(2)}"


def extract_primary_document_identity(
    text: str,
    expected_type: str | None = None,
):
    """
    Extract the document's own primary identity.

    Important rule:
    If filename evidence already suggests a document type,
    only that document type is considered for content verification.

    References to other document types must not be mistaken
    for the identity of the current document.
    """

    if not text:
        return None

    # ------------------------------------------------------------
    # Case 1:
    # Filename already gives us an expected document type.
    #
    # Only search for that same type's explicit labelled field.
    # ------------------------------------------------------------
    if expected_type in PRIMARY_DOCUMENT_PATTERNS:

        for pattern in PRIMARY_DOCUMENT_PATTERNS[expected_type]:

            match = pattern.search(text)

            if match:
                return {
                    "document_type": expected_type,
                    "document_number": normalize_document_number(
                        match.group(1)
                    ),
                    "confidence": 0.99,
                    "method": "labelled_primary_field",
                }

        # --------------------------------------------------------
        # No labelled primary field found.
        #
        # We may still look for an unlabelled number of the SAME
        # document type, but this is weak evidence only.
        # --------------------------------------------------------
        fallback_pattern = FALLBACK_PATTERNS.get(expected_type)

        if fallback_pattern:
            match = fallback_pattern.search(text)

            if match:
                return {
                    "document_type": expected_type,
                    "document_number": (
                        f"{PREFIXES[expected_type]}-{match.group(1)}"
                    ),
                    "confidence": 0.80,
                    "method": "same_type_fallback",
                }

        return None

    # ------------------------------------------------------------
    # Case 2:
    # No expected type exists.
    #
    # This may be useful later for files such as generic PDFs,
    # but we remain conservative.
    # ------------------------------------------------------------
    for document_type, patterns in PRIMARY_DOCUMENT_PATTERNS.items():

        for pattern in patterns:

            match = pattern.search(text)

            if match:
                return {
                    "document_type": document_type,
                    "document_number": normalize_document_number(
                        match.group(1)
                    ),
                    "confidence": 0.95,
                    "method": "labelled_primary_field_without_prior",
                }

    return None


def parse_document(
    file_path: Path,
    expected_type: str | None = None,
):
    """
    Extract content and infer the logical document identity.
    """

    content = extract_content(file_path)

    text = content.get("text", "")

    identity = extract_primary_document_identity(
        text,
        expected_type=expected_type,
    )

    return {
        "file_path": str(file_path),
        "filename": file_path.name,
        "content": content,
        "identity": identity,
    }


if __name__ == "__main__":
    print("Document parser ready.")