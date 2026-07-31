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


# arXiv serves a 200-status *stub* (feedback-form chrome, or an ar5iv
# "See pages 1-last of …" PDF passthrough) when a paper has no real HTML render.
# Those stubs are short and/or carry telltale markers, so we reject them and fall
# through to the PDF, which is always available. The threshold is deliberately
# generous: the cost of rejecting a genuine-but-short HTML render is merely using
# the PDF instead, which is fine.
_MIN_HTML_TEXT = 8000
_STUB_MARKERS = (
    "see pages 1-last of",       # ar5iv failed to convert; it just embeds the PDF
    "html is not available for the source",
    "no html for",
)


def _is_real_fulltext(text: str) -> bool:
    if len(text) < _MIN_HTML_TEXT:
        return False
    low = text[:2000].lower()
    return not any(m in low for m in _STUB_MARKERS)


async def _try_html(url: str, key: str, client: ArxivClient) -> str | None:
    status, body = await client.fetch_text(url, key=key)
    if status != 200:
        return None
    text = html_to_text(body)
    return text if _is_real_fulltext(text) else None


async def extract_full_text(aid: ArxivId, client: ArxivClient, cache: Cache) -> tuple[str, str]:
    """Return ``(text, method)``, using and populating the cache. ``method`` is one
    of ``html``, ``ar5iv``, ``pdf``, or ``cache``.

    Prefers arXiv's LaTeXML HTML, then ar5iv, then falls back to the PDF. HTML
    sources are only accepted when they carry a real render (not a stub); the PDF
    is the reliable backstop for older / PDF-only papers.
    """
    cached = cache.get_text(aid)
    if cached is not None:
        return cached, "cache"

    # 1. arXiv LaTeXML HTML (cleanest when it exists).
    text = await _try_html(aid.html_url, f"html:{aid.canonical}", client)
    if text is not None:
        cache.set_text(aid, text)
        return text, "html"

    # 2. ar5iv (broader historical coverage).
    text = await _try_html(aid.ar5iv_url, f"ar5iv:{aid.canonical}", client)
    if text is not None:
        cache.set_text(aid, text)
        return text, "ar5iv"

    # 3. PDF — always available.
    pdf_path = cache.get_pdf(aid)
    if pdf_path is not None:
        data = pdf_path.read_bytes()
    else:
        data = await client.fetch_bytes(aid.pdf_url, key=f"pdf:{aid.canonical}")
        cache.set_pdf(aid, data)
    text = pdf_to_text(data)
    cache.set_text(aid, text)
    return text, "pdf"
