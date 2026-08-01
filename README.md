# arxiv-mcp

An efficient, well-behaved [Model Context Protocol](https://modelcontextprotocol.io)
server for [arXiv](https://arxiv.org). It gives AI agents a clean interface to
**search papers, fetch metadata, read full text, and download PDFs/source** —
with rate-limiting, on-disk caching, and session pinning built in so the same
paper is never fetched twice.

Built on the official MCP Python SDK (2.x). Runs over stdio.

---

## Contents

- [Why](#why)
- [Install](#install)
- [Run](#run)
- [Wire into an agent](#wire-into-an-agent)
- [Quickstart: a research flow](#quickstart-a-research-flow)
- [Tools reference](#tools-reference)
- [Sessions & caching](#sessions--caching)
- [Configuration](#configuration)
- [Architecture](#architecture)
- [Development & testing](#development--testing)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Why

- **Respects arXiv.** One request every 3 s over a single connection (the
  [Terms-of-Use](https://info.arxiv.org/help/api/tou.html) floor), with 503
  `Retry-After` back-off and a descriptive `User-Agent`. A single global limiter
  gates *every* outbound call, so no combination of tools can exceed the rate.
- **Fast for repeat queries.** Three-tier cache (metadata / extracted text / raw
  files) keyed by normalized id + version. Concurrent identical fetches collapse
  to one request (single-flight dedup).
- **Bounded disk.** LRU eviction by **paper count** *and* **disk size** —
  whichever ceiling trips first. Papers pinned to an open session are exempt.
- **Context-safe reads.** `read_paper` is paginated so a 40-page paper never
  overflows the model's context. Extraction prefers clean arXiv HTML / LaTeX
  source and falls back to the PDF for older or PDF-only papers.
- **Any id form.** New (`2401.12345v2`), old (`hep-th/9901001`), `arXiv:` prefix,
  `arxiv.org/abs/...` URLs, and `10.48550/arXiv...` DOIs all normalize.

## Install

Requires Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Himasnhu-AT/arxiv-mcp.git
cd arxiv-mcp
uv venv --python 3.11
uv pip install -e .           # add ".[fast]" for PyMuPDF (AGPL) extraction
```

The default install uses `pypdf` (BSD) for PDF text extraction. The optional
`fast` extra pulls in PyMuPDF — much faster and higher quality, but AGPL-3.0, so
it is opt-in:

```bash
uv pip install -e ".[fast]"
```

## Run

```bash
uv run arxiv-mcp              # starts the stdio MCP server
```

Normally you don't run this yourself — your MCP client launches it (below).

## Wire into an agent

Add to your MCP client config (`.mcp.json`, Claude Desktop config, etc.). See
[`.mcp.json.example`](.mcp.json.example):

```json
{
  "mcpServers": {
    "arxiv": {
      "command": "uv",
      "args": ["run", "--directory", "/ABSOLUTE/PATH/TO/arxiv-mcp", "arxiv-mcp"],
      "env": {
        "ARXIV_MCP_MAX_PAPERS": "200",
        "ARXIV_MCP_MAX_DISK_MB": "2048"
      }
    }
  }
}
```

**Claude Code**, at user (global) scope:

```bash
claude mcp add arxiv --scope user -- \
  uv run --directory /ABSOLUTE/PATH/TO/arxiv-mcp arxiv-mcp
claude mcp list          # should show: arxiv ... ✔ Connected
```

Tools then appear to the agent as `mcp__arxiv__search_papers`, `mcp__arxiv__read_paper`, etc.

The [`skill/SKILL.md`](skill/SKILL.md) file teaches an agent *how* to use these
tools well (query construction, reading economically, session hygiene). Drop it
wherever your agent loads skills.

## Quickstart: a research flow

The intended pattern for a multi-step task:

1. `start_session(session_id="rlhf-review")` — open a session.
2. `search_papers(title="...", category="cs.LG", session_id="rlhf-review")` — discover.
3. `get_paper(paper_id="2203.02155", session_id="rlhf-review")` — inspect metadata.
4. `read_paper(paper_id="2203.02155", page=1, session_id="rlhf-review")` — read, page by page.
5. `end_session(session_id="rlhf-review")` — release pinned papers.

Passing the same `session_id` throughout pins every fetched paper so nothing
refetches across calls or intervals, and protects them from eviction mid-task.

## Tools reference

### `search_papers`
Search arXiv; returns compact metadata plus `total_results` for paging.

| Param | Type | Default | Notes |
|---|---|---|---|
| `query` | str | – | Raw arXiv field syntax, e.g. `au:hinton AND cat:cs.LG`. |
| `category` | str | – | Shortcut → `cat:<value>` (e.g. `cs.LG`). |
| `author` | str | – | Shortcut → `au:<value>` (quoted if it contains spaces). |
| `title` | str | – | Shortcut → `ti:<value>`. |
| `abstract` | str | – | Shortcut → `abs:<value>`. |
| `id_list` | list[str] | – | Fetch specific ids instead of searching. |
| `start` | int | 0 | Pagination offset. |
| `max_results` | int | 10 | Capped at 2000/call; total window 30000. |
| `sort_by` | str | `relevance` | or `submittedDate`, `lastUpdatedDate`. |
| `sort_order` | str | `descending` | or `ascending`. |
| `session_id` | str | – | Pin returned papers to this session. |

Shortcuts are AND-combined with `query`. Booleans in raw queries are `AND`,
`OR`, `ANDNOT` (**not** `NOT`). For "latest N on X", use
`sort_by="submittedDate"`.

### `get_paper`
`get_paper(paper_id, session_id=None)` — full metadata for one id (any form).
Served from cache when possible. Returns normalized metadata plus derived
`pdf_url` / `abs_url` / `html_url` / `source_url`.

### `read_paper`
`read_paper(paper_id, page=1, page_size=15000, session_id=None)` — paginated full
text. Extraction is HTML/source-first with a PDF fallback; the result is cached
permanently per exact version. The response includes `method`
(`html`/`ar5iv`/`pdf`/`cache`), `page`, `total_pages`, `total_chars`, and
`has_more`. Pin a version with `2401.12345v1`; a bare id reads the latest.

### `download_paper`
`download_paper(paper_id, fmt="pdf", session_id=None)` — download the raw `pdf`
or `source` (LaTeX tarball) into the cache; returns the local path.

### Sessions
- `start_session(session_id)` — open a session (pins its papers).
- `end_session(session_id)` — close it, unpinning its papers.
- `session_status(session_id)` — list the papers pinned to it.

### Cache
- `cache_stats()` — paper count, disk usage, limits, open sessions.
- `list_cached()` — cached papers (most-recent first) with size and pin state.
- `clear_cache(drop_pinned=False)` — evict papers (keeps open-session papers unless `drop_pinned`).

### Categories
- `list_categories(group=None)` — the bundled arXiv taxonomy. Pass a group prefix
  (`cs`) to narrow, or a full id (`cs.AI`) for its description.

## Sessions & caching

Content is addressed by normalized id + version, so two sessions requesting the
same paper share one copy on disk — never a double fetch. Open a session, do your
research across as many tool calls / intervals as you like (nothing refetches),
then close it to release its papers for eviction.

Between and during sessions the cache stays bounded automatically by **two**
independent ceilings — paper **count** and disk **size** — with least-recently-
accessed papers evicted first (`ARXIV_MCP_MAX_PAPERS`, `ARXIV_MCP_MAX_DISK_MB`).
Papers pinned to an *open* session are never evicted, so an in-flight task can't
lose a paper mid-work. When reclaiming space, a paper's heavy raw PDF/source is
dropped before its cheap extracted text and metadata.

## Configuration

All via environment variables:

| Var | Default | Meaning |
|---|---|---|
| `ARXIV_MCP_HOME` | `~/.arxiv-mcp` | Cache root directory. |
| `ARXIV_MCP_MAX_PAPERS` | `200` | Max cached papers before LRU eviction. |
| `ARXIV_MCP_MAX_DISK_MB` | `2048` | Max cache disk (MB) before LRU eviction. |
| `ARXIV_MCP_META_TTL_S` | `86400` | TTL (s) for "latest" (unversioned) metadata. |

## Architecture

```
src/arxiv_mcp/
  server.py        MCPServer + the 11 tool definitions (stdio entry point)
  client.py        Atom query API + content fetch; all traffic rate-limited
  rate_limiter.py  global ≥3s limiter + 503 back-off + single-flight dedup
  cache.py         3-tier disk cache, sessions, LRU eviction (count AND size)
  extract.py       HTML/ar5iv-source-first text extraction, pypdf fallback
  ids.py           id normalization (new/old schemes, URL/DOI) + URL builders
  categories.py    bundled arXiv subject taxonomy
```

**Request path.** Every tool that touches the network goes through
`ArxivClient`, whose `_get` acquires the shared `RateLimiter` before each call,
honors `Retry-After` on 503, and retries transient failures. Identical
in-flight fetches are deduplicated by `SingleFlight`. Results land in `Cache`,
keyed by `ArxivId.key` (normalized id + version), and every write triggers a
bounded LRU eviction pass that skips session-pinned papers.

**Extraction order.** `read_paper` → `arxiv.org/html` (LaTeXML) →
`ar5iv.labs.arxiv.org` → PDF. HTML sources are accepted only when they carry a
real render; arXiv serves a 200-status *stub* (or 404) for papers without native
HTML, so short/stub responses are rejected and fall through to the PDF, which is
always available.

## Development & testing

```bash
uv pip install -e .

# Offline unit tests (id normalization, cache eviction & pinning) — no network:
uv run python -m pytest tests/ -q
# or without pytest:
uv run python tests/test_offline.py

# Live end-to-end smoke test against arXiv (needs network, ~30s, polite 3s spacing):
uv run python tests/test_live.py
```

`test_offline.py` is deterministic and network-free. `test_live.py` performs a
handful of real requests (search, metadata, extraction, cache-hit) and asserts a
few well-known papers resolve correctly.

## Troubleshooting

- **`claude mcp list` shows "Failed to connect".** Ensure `uv` is on PATH and the
  `--directory` path is correct and absolute. Run `uv run arxiv-mcp` in the repo
  to see startup errors directly (it will wait for stdio input; Ctrl-C to exit).
- **A paper won't read / returns a short stub.** Older or PDF-only papers have no
  arXiv HTML; the server falls back to the PDF automatically. If the PDF itself is
  a scan with no text layer, extraction may be sparse — install the `fast` extra
  for better results.
- **Cache growing?** It can't exceed `ARXIV_MCP_MAX_PAPERS` / `ARXIV_MCP_MAX_DISK_MB`
  except for papers pinned to *open* sessions. Call `end_session` when done, or
  `clear_cache`.
- **Rate-limited by arXiv.** The server already spaces requests ~3 s apart; avoid
  launching large fan-outs of `read_paper` across many papers at once.

## License

MIT — see [LICENSE](LICENSE). The optional `fast` extra (PyMuPDF) is AGPL-3.0;
the default install avoids it.
