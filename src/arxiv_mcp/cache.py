"""On-disk, size- and count-bounded cache with session pinning.

Layout (default ``~/.arxiv-mcp``)::

    meta/<key>.json     normalized metadata (carries fetched_at for TTL)
    text/<key>.txt      extracted full text (permanent per exact version)
    pdf/<key>.pdf       raw PDF
    src/<key>.tar.gz    raw LaTeX source
    index.json          per-paper access time + byte size (LRU bookkeeping)
    sessions.json       session_id -> [pinned paper keys]

Eviction runs on every write. Two ceilings are enforced independently — whichever
trips first evicts the least-recently-accessed papers:

* ``max_papers``   — number of distinct cached papers (default 200)
* ``max_disk_mb``  — total bytes across pdf/src/text/meta (default 2048 MB)

A paper pinned to a *live* session is never evicted while that session is open,
so an in-flight research task can't lose a paper mid-work. When reclaiming space
for an unpinned paper, its raw pdf/src are dropped before its (cheap) extracted
text and metadata.

Content is addressed by :attr:`arxiv_mcp.ids.ArxivId.key`, so two sessions
requesting the same paper share one copy on disk — never a double fetch.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .ids import ArxivId


def _default_root() -> Path:
    return Path(os.environ.get("ARXIV_MCP_HOME", str(Path.home() / ".arxiv-mcp")))


@dataclass
class CacheConfig:
    root: Path = field(default_factory=_default_root)
    max_papers: int = field(default_factory=lambda: int(os.environ.get("ARXIV_MCP_MAX_PAPERS", "200")))
    max_disk_mb: int = field(default_factory=lambda: int(os.environ.get("ARXIV_MCP_MAX_DISK_MB", "2048")))
    metadata_ttl_s: int = field(default_factory=lambda: int(os.environ.get("ARXIV_MCP_META_TTL_S", str(24 * 3600))))


class Cache:
    def __init__(self, config: CacheConfig | None = None) -> None:
        self.cfg = config or CacheConfig()
        self.root = self.cfg.root
        for sub in ("meta", "text", "pdf", "src"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / "index.json"
        self._sessions_path = self.root / "sessions.json"
        self._index: dict[str, dict[str, Any]] = self._load(self._index_path)
        self._sessions: dict[str, list[str]] = self._load(self._sessions_path)

    # --- persistence helpers ------------------------------------------------
    @staticmethod
    def _load(p: Path) -> dict:
        try:
            return json.loads(p.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_index(self) -> None:
        self._index_path.write_text(json.dumps(self._index))

    def _save_sessions(self) -> None:
        self._sessions_path.write_text(json.dumps(self._sessions))

    # --- paths --------------------------------------------------------------
    def _meta_p(self, key: str) -> Path:
        return self.root / "meta" / f"{key}.json"

    def _text_p(self, key: str) -> Path:
        return self.root / "text" / f"{key}.txt"

    def _pdf_p(self, key: str) -> Path:
        return self.root / "pdf" / f"{key}.pdf"

    def _src_p(self, key: str) -> Path:
        return self.root / "src" / f"{key}.tar.gz"

    def _paths(self, key: str) -> list[Path]:
        return [self._meta_p(key), self._text_p(key), self._pdf_p(key), self._src_p(key)]

    # --- bookkeeping --------------------------------------------------------
    def _touch(self, key: str) -> None:
        size = sum(p.stat().st_size for p in self._paths(key) if p.exists())
        self._index[key] = {"accessed": time.time(), "size": size}
        self._save_index()

    def _total_bytes(self) -> int:
        return sum(e.get("size", 0) for e in self._index.values())

    def _pinned_keys(self) -> set[str]:
        pinned: set[str] = set()
        for keys in self._sessions.values():
            pinned.update(keys)
        return pinned

    # --- metadata -----------------------------------------------------------
    def get_metadata(self, aid: ArxivId) -> dict | None:
        p = self._meta_p(aid.key)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError:
            return None
        # TTL only applies to "latest" (unversioned) lookups, which can change.
        if aid.version is None:
            age = time.time() - data.get("_fetched_at", 0)
            if age > self.cfg.metadata_ttl_s:
                return None
        self._touch(aid.key)
        return data

    def set_metadata(self, aid: ArxivId, data: dict) -> None:
        payload = dict(data)
        payload["_fetched_at"] = time.time()
        self._meta_p(aid.key).write_text(json.dumps(payload))
        self._touch(aid.key)
        self._evict_if_needed()

    # --- extracted text -----------------------------------------------------
    def get_text(self, aid: ArxivId) -> str | None:
        p = self._text_p(aid.key)
        if not p.exists():
            return None
        self._touch(aid.key)
        return p.read_text()

    def set_text(self, aid: ArxivId, text: str) -> None:
        self._text_p(aid.key).write_text(text)
        self._touch(aid.key)
        self._evict_if_needed()

    # --- raw pdf / source ---------------------------------------------------
    def get_pdf(self, aid: ArxivId) -> Path | None:
        p = self._pdf_p(aid.key)
        if p.exists():
            self._touch(aid.key)
            return p
        return None

    def set_pdf(self, aid: ArxivId, data: bytes) -> Path:
        p = self._pdf_p(aid.key)
        p.write_bytes(data)
        self._touch(aid.key)
        self._evict_if_needed()
        return p

    def get_source(self, aid: ArxivId) -> Path | None:
        p = self._src_p(aid.key)
        if p.exists():
            self._touch(aid.key)
            return p
        return None

    def set_source(self, aid: ArxivId, data: bytes) -> Path:
        p = self._src_p(aid.key)
        p.write_bytes(data)
        self._touch(aid.key)
        self._evict_if_needed()
        return p

    # --- sessions -----------------------------------------------------------
    def start_session(self, session_id: str) -> None:
        self._sessions.setdefault(session_id, [])
        self._save_sessions()

    def end_session(self, session_id: str) -> int:
        """Close a session, unpinning its papers. Returns count unpinned."""
        keys = self._sessions.pop(session_id, [])
        self._save_sessions()
        return len(keys)

    def pin(self, session_id: str, aid: ArxivId) -> None:
        keys = self._sessions.setdefault(session_id, [])
        if aid.key not in keys:
            keys.append(aid.key)
            self._save_sessions()

    def session_papers(self, session_id: str) -> list[str]:
        return list(self._sessions.get(session_id, []))

    def list_sessions(self) -> dict[str, int]:
        return {sid: len(keys) for sid, keys in self._sessions.items()}

    # --- eviction -----------------------------------------------------------
    def _evict_if_needed(self) -> None:
        pinned = self._pinned_keys()
        # Order unpinned papers oldest-accessed first.
        candidates = sorted(
            (k for k in self._index if k not in pinned),
            key=lambda k: self._index[k].get("accessed", 0),
        )
        i = 0
        while self._over_limits() and i < len(candidates):
            key = candidates[i]
            i += 1
            # Reclaim heavy artifacts (pdf/src) first; if still over, drop text/meta.
            self._drop_files(key, [self._pdf_p(key), self._src_p(key)])
            if self._over_limits():
                self._drop_files(key, [self._text_p(key), self._meta_p(key)])
                self._index.pop(key, None)
            else:
                # Keep the (now smaller) entry; refresh its recorded size.
                if any(p.exists() for p in self._paths(key)):
                    self._index[key]["size"] = sum(
                        p.stat().st_size for p in self._paths(key) if p.exists()
                    )
                else:
                    self._index.pop(key, None)
        self._save_index()

    def _over_limits(self) -> bool:
        if len(self._index) > self.cfg.max_papers:
            return True
        if self._total_bytes() > self.cfg.max_disk_mb * 1024 * 1024:
            return True
        return False

    @staticmethod
    def _drop_files(key: str, paths: list[Path]) -> None:
        for p in paths:
            try:
                p.unlink()
            except FileNotFoundError:
                pass

    # --- introspection ------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        pinned = self._pinned_keys()
        return {
            "papers": len(self._index),
            "max_papers": self.cfg.max_papers,
            "disk_mb": round(self._total_bytes() / (1024 * 1024), 2),
            "max_disk_mb": self.cfg.max_disk_mb,
            "pinned_papers": len(pinned),
            "sessions": self.list_sessions(),
            "root": str(self.root),
        }

    def cached_papers(self) -> list[dict[str, Any]]:
        pinned = self._pinned_keys()
        out = []
        for key, meta in sorted(self._index.items(), key=lambda kv: kv[1].get("accessed", 0), reverse=True):
            out.append({
                "key": key,
                "size_mb": round(meta.get("size", 0) / (1024 * 1024), 3),
                "accessed": meta.get("accessed", 0),
                "pinned": key in pinned,
                "has_text": self._text_p(key).exists(),
                "has_pdf": self._pdf_p(key).exists(),
            })
        return out

    def clear(self, drop_pinned: bool = False) -> int:
        pinned = self._pinned_keys()
        removed = 0
        for key in list(self._index):
            if key in pinned and not drop_pinned:
                continue
            self._drop_files(key, self._paths(key))
            self._index.pop(key, None)
            removed += 1
        self._save_index()
        return removed
