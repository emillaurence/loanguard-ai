"""PDF text extraction utilities for the Layer 2 regulatory pipeline."""

from pathlib import Path

import pdfplumber


def extract_pdf_pages(pdf_path: Path) -> list:
    """Extract text per page. Returns list of (page_num, text) tuples."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ''
            pages.append((i, text))
    return pages


def batch_to_text(batch: list) -> str:
    """Join (page_num, text) pairs into a single string with page markers."""
    parts = []
    for num, text in batch:
        parts.append(f'--- PAGE {num} ---')
        parts.append(text)
    return '\n\n'.join(parts)


def extract_full_text(pdf_path: Path) -> str:
    """Extract all text from a PDF as a single concatenated string."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ''
            pages.append(text)
    return '\n\n'.join(pages)
