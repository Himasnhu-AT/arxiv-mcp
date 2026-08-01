"""Live end-to-end tests against arXiv (requires network).

Performs a handful of real, politely-spaced requests and asserts that well-known
papers resolve and extract correctly, and that a second read hits the cache.

Run directly: ``uv run python tests/test_live.py``
(Skipped by pytest unless ``ARXIV_MCP_LIVE=1`` is set, so CI without network
doesn't fail.)
"""

import asyncio
import os
import tempfile

os.environ["ARXIV_MCP_HOME"] = tempfile.mkdtemp(prefix="arxiv-live-")

from arxiv_mcp.client import ArxivClient  # noqa: E402
from arxiv_mcp.cache import Cache  # noqa: E402
from arxiv_mcp.extract import extract_full_text  # noqa: E402
from arxiv_mcp.ids import normalize  # noqa: E402


async def _run() -> None:
    client = ArxivClient()
    cache = Cache()
    try:
        # 1. Search finds the Transformer paper as a top hit.
        res = await client.search(search_query='ti:"Attention Is All You Need"', max_results=5)
        assert res["total_results"] >= 1
        ids = [e["base_id"] for e in res["entries"]]
        assert "1706.03762" in ids, ids
        print(f"PASS search: {ids[:3]}")

        # 2. Extraction resolves several papers with the right method + content.
        #    Mix of native-HTML, ar5iv, and PDF-only (older) papers.
        checks = [
            ("1706.03762", "attention"),      # Transformer (html)
            ("2203.02155", "instruction"),    # InstructGPT (ar5iv)
            ("1412.6980", "adam"),            # Adam (pdf fallback)
            ("hep-th/9711200", "maldacena"),  # old physics (pdf fallback)
        ]
        for pid, needle in checks:
            aid = normalize(pid)
            text, method = await extract_full_text(aid, client, cache)
            assert len(text) > 8000, f"{pid}: only {len(text)} chars"
            assert needle in text.lower(), f"{pid}: missing {needle!r}"
            # Second read must come from cache.
            _, method2 = await extract_full_text(aid, client, cache)
            assert method2 == "cache", f"{pid}: 2nd read was {method2}, not cache"
            print(f"PASS extract {pid}: method={method} chars={len(text)} (cache OK)")
    finally:
        await client.aclose()


try:
    import pytest

    @pytest.mark.skipif(os.environ.get("ARXIV_MCP_LIVE") != "1",
                        reason="live network test; set ARXIV_MCP_LIVE=1 to run")
    def test_live():
        asyncio.run(_run())
except ImportError:  # pytest not installed — standalone run still works.
    pass


if __name__ == "__main__":
    asyncio.run(_run())
    print("\nlive tests passed")
