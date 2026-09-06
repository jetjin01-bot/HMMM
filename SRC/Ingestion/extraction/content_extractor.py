from pathlib import Path
from email import policy
from email.parser import BytesParser

import fitz  # PyMuPDF


def _extract_pdf_text(file_path: Path) -> dict:
    """
    Extract text from a text-based PDF.

    This does not perform OCR yet.
    """

    pages = []
    full_text_parts = []

    with fitz.open(file_path) as document:
        for page_number, page in enumerate(document, start=1):
            text = page.get_text("text").strip()

            pages.append(
                {
                    "page_number": page_number,
                    "text": text,
                }
            )

            if text:
                full_text_parts.append(text)

    return {
        "extraction_method": "pymupdf_text",
        "page_count": len(pages),
        "text": "\n\n".join(full_text_parts),
        "pages": pages,
    }


def _extract_eml_content(file_path: Path) -> dict:
    """
    Extract structured metadata and body text from an EML file.
    """

    with file_path.open("rb") as file:
        message = BytesParser(policy=policy.default).parse(file)

    sender = message.get("From")
    recipient = message.get("To")
    cc = message.get("Cc")
    subject = message.get("Subject")
    date = message.get("Date")

    body_parts = []

    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            content_disposition = part.get_content_disposition()

            # Ignore attachments
            if content_disposition == "attachment":
                continue

            if content_type in ("text/plain", "text/html"):
                try:
                    body_parts.append(part.get_content())
                except Exception as exc:
                    raise ValueError("Cannot decode email body") from exc

    else:
        if message.get_content_type() in ("text/plain", "text/html"):
            try:
                body_parts.append(message.get_content())
            except Exception as exc:
                raise ValueError("Cannot decode email body") from exc

    body = "\n".join(body_parts).strip()

    return {
        "extraction_method": "python_email_parser",
        "sender": sender,
        "recipient": recipient,
        "cc": cc,
        "subject": subject,
        "date": date,
        "text": body,
    }


class ExtractionFailure(ValueError):
    pass

def _checked_extract(file_path, extractor):
    file_path = Path(file_path)
    try:
        if file_path.stat().st_size == 0:
            raise ValueError("empty file")
        result = extractor(file_path)
        if not result.get("text", "").strip():
            # Header-only email is valid; a PDF without text needs OCR/review.
            if file_path.suffix.lower() != '.eml' or not any(result.get(k) for k in ('sender', 'recipient', 'subject')):
                raise ValueError("no extractable text")
        return result
    except Exception as exc:
        raise ExtractionFailure(f"{file_path.suffix.lower()} extraction failed: {type(exc).__name__}: {exc}") from exc

def extract_pdf_text(file_path):
    return _checked_extract(file_path, _extract_pdf_text)

def extract_eml_content(file_path):
    return _checked_extract(file_path, _extract_eml_content)


def extract_content(file_path: Path) -> dict:
    """
    Route a file to the correct content extractor.
    """

    extension = file_path.suffix.lower()

    if extension == ".pdf":
        return extract_pdf_text(file_path)

    if extension == ".eml":
        return extract_eml_content(file_path)

    return {
        "extraction_method": "unsupported",
        "text": "",
    }


if __name__ == "__main__":

    eml_path = Path(
        r"C:\Users\Administrator\Downloads\takehome\Customers\Falcon Aerospace Components Ltd\JOB-2026-0026 Control Panel Replacement\Correspondence\20231213_c5fdece7.eml"
    )

    result = extract_content(eml_path)

    print("=" * 70)
    print("EML TEST")
    print("=" * 70)

    print("From:", result["sender"])
    print("To:", result["recipient"])
    print("CC:", result["cc"])
    print("Date:", result["date"])
    print("Subject:", result["subject"])

    print("\nBODY:")
    print(result["text"][:3000])