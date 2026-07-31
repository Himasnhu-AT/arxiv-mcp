"""Full-text extraction, source-first.

Order of preference (cleaner and cheaper before heavier):

1. arXiv's LaTeXML HTML (``arxiv.org/html/<id>``) — newer TeX-source papers.
2. ar5iv (``ar5iv.labs.arxiv.org``) — broader historical coverage.
3. PDF via ``pypdf`` (BSD). If the optional ``pymupdf`` extra is installed it is
   used instead — much faster and higher quality, but AGPL-licensed.

Each returns ``(text, method)`` or ``None`` if that source is unavailable.
"""

from __future__ import annotations

import io
from html.parser import HTMLParser

from .cache import Cache
from .client import ArxivClient
from .ids import ArxivId

_SKIP_TAGS = {"script", "style", "noscript", "head", "svg", "math"}
_BLOCK_TAGS = {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6",
               "li", "br", "tr", "table", "figure", "figcaption", "pre"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._chunks.append(data)

    def text(self) -> str:
        raw = "".join(self._chunks)
        lines = [ln.strip() for ln in raw.splitlines()]
        out: list[str] = []
        blank = False
        for ln in lines:
            if ln:
                out.append(ln)
                blank = False
            elif not blank:
                out.append("")
                blank = True
        return "\n".join(out).strip()


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    return p.text()


def pdf_to_text(data: bytes) -> str:
    """Extract text from PDF bytes. Prefers pymupdf if available, else pypdf."""
    try:
        import fitz  # PyMuPDF, optional [fast] extra.

        doc = fitz.open(stream=data, filetype="pdf")
        try:
            return "\n\n".join(page.get_text("text") for page in doc).strip()
        finally:
            doc.close()
    except ImportError:
        pass

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 — a bad page shouldn't kill the whole doc.
            continue
    return "\n\n".join(parts).strip()


# Below a threshold the HTML is almost certainly a "no HTML available" stub.
_MIN_HTML_TEXT = 500


async def extract_full_text(aid: ArxivId, client: ArxivClient, cache: Cache) -> tuple[str, str]:
    """Return ``(text, method)``, using and populating the cache. ``method`` is one
    of ``html``, ``ar5iv``, ``pdf``, or ``cache``."""
    cached = cache.get_text(aid)
    if cached is not None:
        return cached, "cache"

    # 1. arXiv LaTeXML HTML.
    status, body = await client.fetch_text(aid.html_url, key=f"html:{aid.canonical}")
    if status == 200:
        text = html_to_text(body)
        if len(text) >= _MIN_HTML_TEXT:
            cache.set_text(aid, text)
            return text, "html"

    # 2. ar5iv fallback.
    status, body = await client.fetch_text(aid.ar5iv_url, key=f"ar5iv:{aid.canonical}")
    if status == 200:
        text = html_to_text(body)
        if len(text) >= _MIN_HTML_TEXT:
            cache.set_text(aid, text)
            return text, "ar5iv"

    # 3. PDF.
    pdf_path = cache.get_pdf(aid)
    if pdf_path is not None:
        data = pdf_path.read_bytes()
    else:
        data = await client.fetch_bytes(aid.pdf_url, key=f"pdf:{aid.canonical}")
        cache.set_pdf(aid, data)
    text = pdf_to_text(data)
    cache.set_text(aid, text)
    return text, "pdf"
