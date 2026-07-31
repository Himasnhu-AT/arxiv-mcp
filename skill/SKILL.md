---
name: arxiv-research
description: Search, fetch, read, and download arXiv papers efficiently via the arxiv-mcp server. Use whenever the user wants to find academic papers, look up an arXiv id, read a paper's full text or a section, gather a literature set on a topic, or download a PDF/source. Triggers include "find papers on...", "what's the latest research on...", "read arXiv:...", "summarize this paper", "get the PDF for...", or any arXiv id / arxiv.org URL.
---

# arXiv research

You have an `arxiv` MCP server for interacting with arXiv. Use its tools instead
of web-scraping arxiv.org. The server handles rate-limiting, caching, and text
extraction for you — your job is to query well and read economically.

## Core workflow

1. **Open a session** for any multi-step task: `start_session(session_id="<task-slug>")`.
   Pass that same `session_id` to every subsequent call. This pins fetched papers
   so nothing refetches across your calls, and protects them from cache eviction
   mid-task. **Call `end_session` when done.**
2. **Search** with `search_papers` to discover candidates.
3. **Read** with `read_paper` (paginated) when you need a paper's contents.
4. **Download** with `download_paper` only if the user wants the raw file.

## search_papers — construct good queries

Prefer the **structured shortcuts** (they're AND-combined and escaped for you):

- `category="cs.LG"` → `cat:cs.LG`  (use `list_categories` to find valid codes)
- `author="Yann LeCun"` → quoted author match
- `title="attention is all you need"` → title phrase
- `abstract="retrieval augmented"` → abstract phrase

For anything more complex, pass a raw `query` in arXiv field syntax:

- Fields: `ti:` `au:` `abs:` `cat:` `co:` `jr:` `all:`
- Booleans: `AND` `OR` `ANDNOT`  (it is **`ANDNOT`**, never `NOT`)
- Phrase: `ti:"quantum criticality"`
- Example: `query='au:hinton AND cat:cs.LG ANDNOT ti:"reinforcement learning"'`

Sorting & paging:

- `sort_by`: `relevance` (default), `submittedDate` (newest research),
  `lastUpdatedDate`. `sort_order`: `descending`/`ascending`.
- **"Latest N papers on X"** → `sort_by="submittedDate", sort_order="descending"`.
- `max_results` ≤ 2000 per call; page deeper with `start` (total window 30000).
- Read `total_results` to decide whether to page further.

Keep `max_results` small (10–25) unless the user asked for a broad sweep — it's
faster and kinder to arXiv.

## get_paper — one id, full metadata

`get_paper(paper_id="2401.12345")`. Accepts any id form: bare, `arXiv:` prefix,
`arxiv.org/abs/...` URL, old-style `hep-th/9901001`, or a `10.48550/arXiv...`
DOI. Served from cache when possible.

## read_paper — read economically

`read_paper` returns **one page of text at a time** (default ~15000 chars) with
`total_pages` and `has_more`. Do **not** blindly pull every page:

- For a summary, page 1 (title/abstract/intro) is often enough — check `has_more`
  and only continue if the user needs deeper detail.
- To target a section, page through until you find it, or read sequentially.
- Extraction is HTML/source-first (clean) with PDF fallback; the `method` field
  tells you which was used. Results are cached permanently per exact version.
- **Versions:** a bare id reads the latest version; pin one with `2401.12345v1`.

## Sessions & cache management

- `start_session` / `end_session` / `session_status` — pin papers for a task.
- `cache_stats` — paper count, disk usage, and limits at a glance.
- `list_cached` — what's cached, newest first, with pin state.
- `clear_cache` — free space (keeps open-session papers unless `drop_pinned`).

The cache is bounded by paper count and disk size (LRU eviction), so you never
need to worry about it growing unbounded — but pinning via sessions guarantees
your active papers stay put.

## Good habits

- One session per research task; always close it.
- Search narrow first, widen only if `total_results` is low.
- Read page 1 before deciding to read more.
- Cite papers by their canonical id and title from the metadata you fetched.
- Never hammer: the server already spaces requests ~3s apart — don't launch huge
  fan-outs of `read_paper` across dozens of papers at once.
