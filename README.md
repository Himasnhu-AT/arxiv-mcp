# arxiv-mcp

An efficient, well-behaved [MCP](https://modelcontextprotocol.io) server for
arXiv. Give your agents a clean interface to **search papers, fetch metadata,
read full text, and download PDFs/source** — with rate-limiting, on-disk
caching, and session pinning built in so the same paper is never fetched twice.

## Why

- **Respects arXiv.** One request every 3 s over a single connection (the
  Terms-of-Use floor), with 503 `Retry-After` back-off and a descriptive
  User-Agent. A global limiter gates *every* outbound call.
- **Fast for repeat queries.** Three-tier cache (metadata / extracted text / raw
  files) keyed by normalized id + version. Concurrent identical fetches collapse
  to one request (single-flight).
- **Bounded disk.** LRU eviction by **paper count** *and* **disk size** —
  whichever ceiling trips first. Papers pinned to an open session are exempt.
- **Context-safe reads.** `read_paper` is paginated so a 40-page paper never
  blows the model's context. Extraction prefers arXiv HTML/LaTeX source over PDF.
- **Any id form.** New (`2401.12345v2`), old (`hep-th/9901001`), `arXiv:` prefix,
  `arxiv.org/abs/...` URLs, and `10.48550/arXiv...` DOIs all normalize.

## Install

```bash
cd arxiv-mcp
uv venv --python 3.11
uv pip install -e .          # add [fast] for PyMuPDF (AGPL) extraction
```

## Run

```bash
uv run arxiv-mcp             # stdio MCP server
```

## Wire into an agent

`.mcp.json` (Claude Code / any MCP client):

```json
{
  "mcpServers": {
    "arxiv": {
      "command": "uv",
      "args": ["run", "--directory", "/ABSOLUTE/PATH/TO/arxiv-mcp", "arxiv-mcp"]
    }
  }
}
```

## Tools

| Tool | Purpose |
|---|---|
| `search_papers` | Query by free text or structured filters (category/author/title/abstract/date), sort, paginate. |
| `get_paper` | Full metadata for one id (cached). |
| `read_paper` | Paginated full text; HTML/source-first, PDF fallback. |
| `download_paper` | Save raw `pdf` or `source` to the cache. |
| `start_session` / `end_session` / `session_status` | Pin papers to a session so repeat work never refetches. |
| `cache_stats` / `list_cached` / `clear_cache` | Inspect and manage the cache. |
| `list_categories` | Bundled arXiv taxonomy for `cat:` filters. |

## Configuration (env vars)

| Var | Default | Meaning |
|---|---|---|
| `ARXIV_MCP_HOME` | `~/.arxiv-mcp` | Cache root. |
| `ARXIV_MCP_MAX_PAPERS` | `200` | Max cached papers before LRU eviction. |
| `ARXIV_MCP_MAX_DISK_MB` | `2048` | Max cache disk before LRU eviction. |
| `ARXIV_MCP_META_TTL_S` | `86400` | TTL for "latest" (unversioned) metadata. |

## Sessions & caching, in short

Content is addressed by normalized id + version, so two sessions requesting the
same paper share one copy on disk. Open a session, do your research across as
many tool calls / intervals as you like — nothing refetches — then close it to
release its papers for eviction. Between sessions, the cache stays bounded by
count and size automatically.

MIT licensed.
