from __future__ import annotations

from io import BytesIO
from pathlib import Path


PLAIN_TEXT_SUFFIXES = {".csv", ".json", ".md", ".rtf", ".text", ".txt"}


def extract_document_text(filename: str, content: bytes) -> tuple[str, str, str]:
    """Return extracted text, a machine-readable status, and a safe error message."""
    suffix = Path(filename).suffix.lower()
    try:
        if suffix in PLAIN_TEXT_SUFFIXES:
            return content.decode("utf-8", errors="replace").strip(), "extracted", ""
        if suffix == ".pdf":
            from pypdf import PdfReader

            pages = (page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)
            text = "\n\n".join(part.strip() for part in pages if part.strip())
            return text, "extracted" if text else "empty", ""
        if suffix == ".docx":
            from docx import Document

            text = "\n".join(paragraph.text.strip() for paragraph in Document(BytesIO(content)).paragraphs if paragraph.text.strip())
            return text, "extracted" if text else "empty", ""
        return "", "unsupported", f"Automatic extraction is not available for {suffix or 'this file type'}"
    except Exception as exc:  # A damaged document must not prevent storing the original bytes.
        return "", "failed", f"{type(exc).__name__}: {exc}"
