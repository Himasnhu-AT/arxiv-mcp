"""HTTP client for the arXiv Atom query API and content endpoints.

Everything outbound flows through the shared :class:`RateLimiter` so the whole
server honors arXiv's one-request-per-3s / single-connection policy, and through
:class:`SingleFlight` so concurrent identical fetches collapse to one request.
"""

from __future__ import annotations

import asyncio

import feedparser
import httpx

from .ids import ArxivId, normalize
from .rate_limiter import RateLimiter, SingleFlight

_QUERY_URL = "https://export.arxiv.org/api/query"
_USER_AGENT = "arxiv-mcp/0.1 (+https://github.com/Himasnhu-AT/arxiv-mcp; research agent tool)"

MAX_RESULTS_PER_CALL = 2000  # arXiv per-call ceiling.
_MAX_RETRIES = 5


class ArxivClient:
    def __init__(self, limiter: RateLimiter | None = None) -> None:
        self.limiter = limiter or RateLimiter(min_interval=3.0)
        self._sf = SingleFlight()
        self._http = httpx.AsyncClient(
            headers={"User-Agent": _USER_AGENT},
            timeout=httpx.Timeout(60.0),
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(
        self, url: str, params: dict | None = None, raise_for_client_error: bool = True
    ) -> httpx.Response:
        """Rate-limited GET with 503/Retry-After back-off and retries.

        4xx responses raise by default. Callers that treat a 404 as "this source
        isn't available, try the next one" (e.g. HTML/ar5iv extraction) pass
        ``raise_for_client_error=False`` to get the response back instead.
        """
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            await self.limiter.acquire()
            try:
                resp = await self._http.get(url, params=params)
            except httpx.HTTPError as exc:
                last_exc = exc
                await asyncio.sleep(3.0 * (attempt + 1))
                continue
            if resp.status_code == 503:
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 3.0 * (attempt + 1)
                self.limiter.note_retry_after(delay)
                continue
            if 400 <= resp.status_code < 500 and not raise_for_client_error:
                return resp
            if resp.status_code >= 400:
                resp.raise_for_status()
            return resp
        if last_exc:
            raise last_exc
        raise RuntimeError(f"arXiv request failed after {_MAX_RETRIES} attempts: {url}")

    # --- search -------------------------------------------------------------
    async def search(
        self,
        search_query: str | None = None,
        id_list: list[str] | None = None,
        start: int = 0,
        max_results: int = 10,
        sort_by: str = "relevance",
        sort_order: str = "descending",
    ) -> dict:
        """Run one query-API call and return normalized JSON.

        Returns ``{"total_results", "start", "count", "entries": [...]}``.
        """
        if max_results > MAX_RESULTS_PER_CALL:
            max_results = MAX_RESULTS_PER_CALL
        params: dict[str, str | int] = {
            "start": start,
            "max_results": max_results,
            "sortBy": sort_by,
            "sortOrder": sort_order,
        }
        if search_query:
            params["search_query"] = search_query
        if id_list:
            params["id_list"] = ",".join(id_list)
        if not search_query and not id_list:
            raise ValueError("provide search_query or id_list")

        resp = await self._get(_QUERY_URL, params=params)
        feed = feedparser.parse(resp.text)
        total = int(feed.feed.get("opensearch_totalresults", len(feed.entries)) or 0)
        entries = [_entry_to_dict(e) for e in feed.entries]
        return {
            "total_results": total,
            "start": start,
            "count": len(entries),
            "entries": entries,
        }

    async def get_metadata(self, aid: ArxivId) -> dict | None:
        result = await self._sf.do(
            f"meta:{aid.canonical}",
            lambda: self.search(id_list=[aid.canonical], max_results=1),
        )
        entries = result.get("entries", [])
        return entries[0] if entries else None

    # --- content ------------------------------------------------------------
    async def fetch_bytes(self, url: str, key: str) -> bytes:
        async def _do() -> bytes:
            resp = await self._get(url)
            return resp.content

        return await self._sf.do(f"bytes:{key}", _do)

    async def fetch_text(self, url: str, key: str) -> tuple[int, str]:
        """Fetch a text resource, returning ``(status, body)``. A 404 (source not
        available) is returned rather than raised so callers can fall back."""
        async def _do() -> tuple[int, str]:
            resp = await self._get(url, raise_for_client_error=False)
            return resp.status_code, resp.text

        return await self._sf.do(f"text:{key}", _do)


def _entry_to_dict(e) -> dict:
    """Convert a feedparser Atom entry into compact, agent-friendly JSON."""
    aid = normalize(e.get("id", ""))
    authors = [a.get("name", "") for a in e.get("authors", [])] if e.get("authors") else []
    categories = [t.get("term") for t in e.get("tags", [])] if e.get("tags") else []

    pdf_url = None
    doi_url = None
    for link in e.get("links", []):
        if link.get("title") == "pdf" or link.get("type") == "application/pdf":
            pdf_url = link.get("href")
        if link.get("title") == "doi":
            doi_url = link.get("href")

    return {
        "id": aid.canonical,
        "base_id": aid.base,
        "version": aid.version,
        "title": " ".join(e.get("title", "").split()),
        "authors": authors,
        "summary": " ".join(e.get("summary", "").split()),
        "published": e.get("published"),
        "updated": e.get("updated"),
        "primary_category": (e.get("arxiv_primary_category") or {}).get("term"),
        "categories": categories,
        "comment": e.get("arxiv_comment"),
        "journal_ref": e.get("arxiv_journal_ref"),
        "doi": e.get("arxiv_doi") or doi_url,
        "abs_url": aid.abs_url,
        "pdf_url": pdf_url or aid.pdf_url,
        "html_url": aid.html_url,
        "source_url": aid.source_url,
    }
