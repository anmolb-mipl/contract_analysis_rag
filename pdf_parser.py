import re
import fitz  # PyMuPDF
from config import CHUNK_MAX_CHARS


def extract_pages(data: bytes) -> list[dict]:
    """Return [{'page': 1, 'text': '...'}, ...] (1-based page numbers)."""
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        return [
            {"page": i + 1, "text": page.get_text("text").strip()}
            for i, page in enumerate(doc)
        ]
    finally:
        doc.close()


def looks_scanned(pages: list[dict]) -> bool:
    """True if most pages have almost no extractable text (needs OCR)."""
    with_text = sum(1 for p in pages if len(p["text"]) > 30)
    return with_text < max(1, len(pages) // 2)


def _split_long(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    sentences = re.split(r"(?<=[.;:])\s+", text)
    # Hard-split any single sentence that is still too long.
    sentences = [
        s[i : i + max_chars] for s in sentences for i in range(0, len(s), max_chars)
    ]
    parts, cur = [], ""
    for s in sentences:
        if cur and len(cur) + len(s) + 1 > max_chars:
            parts.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        parts.append(cur)
    return parts


def _paragraphs(page_text: str) -> list[str]:
    # Split on blank lines, or before a line that starts a numbered clause ("3." / "3.1)" ).
    raw = re.split(r"\n\s*\n|\n(?=\s*\d+(?:\.\d+)*[.)]\s)", page_text)
    paras = [re.sub(r"\s+", " ", p).strip() for p in raw]
    return [p for p in paras if p]


def chunk_pages(pages: list[dict], max_chars: int = CHUNK_MAX_CHARS) -> list[dict]:
    """
    Chunks never cross page boundaries, so the page number is always exact.
    Each chunk records which paragraph numbers (within its page) it covers.
    """
    chunks: list[dict] = []
    for p in pages:
        n = 0
        buf, first, last = "", None, None

        def flush():
            nonlocal buf, first, last, n
            if buf:
                n += 1
                chunks.append(_make(p["page"], n, first, last, buf))
            buf, first, last = "", None, None

        for idx, para in enumerate(_paragraphs(p["text"]), start=1):
            for piece in _split_long(para, max_chars):
                if buf and len(buf) + len(piece) + 1 > max_chars:
                    flush()
                if first is None:
                    first = idx
                last = idx
                buf = f"{buf} {piece}".strip()
        flush()
    return chunks


def _make(page: int, n: int, first: int, last: int, text: str) -> dict:
    return {
        "id": f"p{page}-c{n}",
        "page": page,
        "para_start": first,
        "para_end": last,
        "text": text,
    }
