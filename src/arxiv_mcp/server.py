"""arxiv-mcp MCP server.

Exposes search, fetch, read (paginated), download, session, and cache tools over
stdio. All outbound arXiv traffic is rate-limited and cached; concurrent
identical fetches are deduplicated.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from .cache import Cache
from .client import ArxivClient
from .extract import extract_full_text
from .ids import InvalidArxivId, normalize
from .categories import TAXONOMY, lookup_category

mcp = MCPServer("arxiv-mcp")

_cache = Cache()
_client = ArxivClient()

# Default characters per read_paper page — a few thousand tokens, context-safe.
_PAGE_CHARS = 15000


def _maybe_pin(session_id: str | None, canonical: str) -> None:
    if session_id:
        try:
            _cache.pin(session_id, normalize(canonical))
        except InvalidArxivId:
            pass


@mcp.tool()
async def search_papers(
    query: str | None = None,
    category: str | None = None,
    author: str | None = None,
    title: str | None = None,
    abstract: str | None = None,
    id_list: list[str] | None = None,
    start: int = 0,
    max_results: int = 10,
    sort_by: str = "relevance",
    sort_order: str = "descending",
    session_id: str | None = None,
) -> dict[str, Any]:
    """Search arXiv and return compact paper metadata.

    Provide a raw ``query`` (arXiv field syntax, e.g. ``au:hinton AND cat:cs.LG``)
    and/or the structured shortcuts ``category`` (cat:), ``author`` (au:),
    ``title`` (ti:), ``abstract`` (abs:) which are AND-combined. Or pass
    ``id_list`` to fetch specific papers by id.

    ``sort_by`` is one of ``relevance``, ``lastUpdatedDate``, ``submittedDate``;
    ``sort_order`` is ``ascending`` or ``descending``. ``max_results`` is capped
    at 2000 per call — page with ``start`` for more (up to 30000 total).

    If ``session_id`` is given, returned papers are pinned to that session's
    cache so they won't be evicted while the session is open.
    """
    parts: list[str] = []
    if query:
        parts.append(f"({query})" if any(op in query for op in (" AND ", " OR ", " ANDNOT ")) else query)
    if category:
        parts.append(f"cat:{category}")
    if author:
        parts.append(f'au:"{author}"' if " " in author else f"au:{author}")
    if title:
        parts.append(f'ti:"{title}"' if " " in title else f"ti:{title}")
    if abstract:
        parts.append(f'abs:"{abstract}"' if " " in abstract else f"abs:{abstract}")
    search_query = " AND ".join(parts) if parts else None

    result = await _client.search(
        search_query=search_query,
        id_list=id_list,
        start=start,
        max_results=max_results,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    for entry in result["entries"]:
        _cache.set_metadata(normalize(entry["id"]), entry)
        _maybe_pin(session_id, entry["id"])
    return result


@mcp.tool()
async def get_paper(paper_id: str, session_id: str | None = None) -> dict[str, Any]:
    """Fetch full metadata for one paper by arXiv id.

    Accepts any id form: ``2401.12345``, ``arXiv:2401.12345v2``, an
    ``arxiv.org/abs/...`` URL, ``hep-th/9901001``, or a ``10.48550/arXiv...`` DOI.
    Served from cache when possible. Returns normalized metadata plus derived
    pdf / abs / html / source URLs.
    """
    try:
        aid = normalize(paper_id)
    except InvalidArxivId as exc:
        return {"error": str(exc)}

    meta = _cache.get_metadata(aid)
    if meta is None:
        meta = await _client.get_metadata(aid)
        if meta is None:
            return {"error": f"no paper found for id {paper_id!r}"}
        _cache.set_metadata(aid, meta)
    _maybe_pin(session_id, aid.canonical)
    return meta


@mcp.tool()
async def read_paper(
    paper_id: str,
    page: int = 1,
    page_size: int = _PAGE_CHARS,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Read a paper's full text, paginated so large papers don't overflow context.

    Extraction prefers arXiv HTML/LaTeX source and falls back to PDF; the result
    is cached permanently per exact version. ``page`` is 1-based; ``page_size`` is
    characters per page. The response reports ``total_pages`` and ``has_more`` so
    you can request the next page. To read a specific version, include it in the
    id (``2401.12345v1``); a bare id resolves to the latest.
    """
    try:
        aid = normalize(paper_id)
    except InvalidArxivId as exc:
        return {"error": str(exc)}

    text, method = await extract_full_text(aid, _client, _cache)
    _maybe_pin(session_id, aid.canonical)

    total = len(text)
    page_size = max(1000, page_size)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    chunk = text[start:start + page_size]
    return {
        "id": aid.canonical,
        "method": method,
        "page": page,
        "total_pages": total_pages,
        "page_size": page_size,
        "total_chars": total,
        "has_more": page < total_pages,
        "text": chunk,
    }


@mcp.tool()
async def download_paper(paper_id: str, fmt: str = "pdf", session_id: str | None = None) -> dict[str, Any]:
    """Download a paper's raw ``pdf`` or ``source`` (LaTeX tarball) into the cache.

    Returns the local path. Use ``read_paper`` if you want extracted text rather
    than the raw file.
    """
    try:
        aid = normalize(paper_id)
    except InvalidArxivId as exc:
        return {"error": str(exc)}

    if fmt == "pdf":
        existing = _cache.get_pdf(aid)
        if existing is None:
            data = await _client.fetch_bytes(aid.pdf_url, key=f"pdf:{aid.canonical}")
            existing = _cache.set_pdf(aid, data)
        path = existing
    elif fmt == "source":
        existing = _cache.get_source(aid)
        if existing is None:
            data = await _client.fetch_bytes(aid.source_url, key=f"src:{aid.canonical}")
            existing = _cache.set_source(aid, data)
        path = existing
    else:
        return {"error": f"unknown fmt {fmt!r}; use 'pdf' or 'source'"}

    _maybe_pin(session_id, aid.canonical)
    return {"id": aid.canonical, "fmt": fmt, "path": str(path), "size_bytes": path.stat().st_size}


# --- sessions ---------------------------------------------------------------
@mcp.tool()
async def start_session(session_id: str) -> dict[str, Any]:
    """Open a caching session. Papers fetched with this ``session_id`` are pinned
    (never evicted) until :func:`end_session`, so repeated work over intervals
    never refetches the same paper."""
    _cache.start_session(session_id)
    return {"session_id": session_id, "status": "open"}


@mcp.tool()
async def end_session(session_id: str) -> dict[str, Any]:
    """Close a session, unpinning its papers so they become eligible for eviction."""
    n = _cache.end_session(session_id)
    return {"session_id": session_id, "status": "closed", "unpinned_papers": n}


@mcp.tool()
async def session_status(session_id: str) -> dict[str, Any]:
    """Report the papers pinned to a session."""
    return {"session_id": session_id, "papers": _cache.session_papers(session_id)}


# --- cache ------------------------------------------------------------------
@mcp.tool()
async def cache_stats() -> dict[str, Any]:
    """Report cache usage: paper count, disk usage, limits, sessions."""
    return _cache.stats()


@mcp.tool()
async def list_cached() -> dict[str, Any]:
    """List cached papers (most-recently-accessed first) with size and pin state."""
    return {"papers": _cache.cached_papers()}


@mcp.tool()
async def clear_cache(drop_pinned: bool = False) -> dict[str, Any]:
    """Evict cached papers. By default leaves papers pinned to open sessions;
    set ``drop_pinned`` to remove everything."""
    removed = _cache.clear(drop_pinned=drop_pinned)
    return {"removed_papers": removed, "stats": _cache.stats()}


# --- categories -------------------------------------------------------------
@mcp.tool()
async def list_categories(group: str | None = None) -> dict[str, Any]:
    """List arXiv subject categories for building ``cat:`` filters.

    Pass a ``group`` prefix (e.g. ``cs``, ``math``, ``astro-ph``) to narrow, or a
    full category id (``cs.AI``) to get its description.
    """
    if group and "." in group:
        desc = lookup_category(group)
        return {"category": group, "name": desc} if desc else {"error": f"unknown category {group!r}"}
    if group:
        subset = {k: v for k, v in TAXONOMY.items() if k.split(".")[0] == group or k == group}
        return {"group": group, "categories": subset or "no match"}
    return {"categories": TAXONOMY}


def main() -> None:
    """Console-script entry point: run the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
