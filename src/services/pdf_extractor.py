"""Server-side PDF text extraction using pymupdf."""

import base64
import logging

import pymupdf

logger = logging.getLogger(__name__)


def extract_pdf_text(content_base64: str) -> str:
    """Extract text content from a base64-encoded PDF.

    Returns extracted text with page markers, or empty string if extraction fails.
    Never raises — graceful fallback to BinaryContent passthrough.
    """
    try:
        pdf_bytes = base64.b64decode(content_base64)
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        pages = []
        for page_num, page in enumerate(doc):
            text = page.get_text("text")
            if text.strip():
                pages.append(f"--- Page {page_num + 1} ---\n{text.strip()}")
        doc.close()
        return "\n\n".join(pages)
    except Exception as e:
        logger.warning(f"PDF text extraction failed: {e}")
        return ""
