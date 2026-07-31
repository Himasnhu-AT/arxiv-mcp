"""arXiv identifier normalization and URL construction.

Two identifier schemes coexist:

* Old (pre-April 2007):  ``archive[.subject]/YYMMNNN``  e.g. ``hep-th/9901001``,
  ``math.GT/0107042``.
* New (April 2007+):     ``YYMM.NNNN[N]``               e.g. ``0706.0001``,
  ``2401.12345`` (5-digit sequence since Jan 2015).

Both may carry a ``vN`` version suffix and an ``arXiv:`` prefix. A bare id (no
``vN``) resolves to the *latest* version, which changes over time — so the cache
keys on the resolved version string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# New scheme: 0704.0001 .. 2412.99999, optional version.
_NEW_RE = re.compile(r"^(\d{4}\.\d{4,5})(v(\d+))?$")
# Old scheme: hep-th/9901001, math.GT/0107042, optional version.
_OLD_RE = re.compile(r"^([a-z-]+(?:\.[A-Za-z-]{2,})?/\d{7})(v(\d+))?$")

_ABS_HOST = "https://arxiv.org"
_EXPORT = "http://export.arxiv.org/api/query"


class InvalidArxivId(ValueError):
    """Raised when a string cannot be parsed as an arXiv identifier."""


@dataclass(frozen=True)
class ArxivId:
    """A parsed arXiv identifier.

    ``base`` is the id without any version suffix (``2401.12345`` or
    ``hep-th/9901001``). ``version`` is the integer version if one was given,
    else ``None`` (meaning "latest").
    """

    base: str
    version: int | None

    @property
    def canonical(self) -> str:
        """Id including the version suffix if known (``2401.12345v2``)."""
        return f"{self.base}v{self.version}" if self.version is not None else self.base

    @property
    def key(self) -> str:
        """Filesystem-safe cache key.

        Versioned when known so different versions never collide; a bare id
        (latest) is keyed as ``<base>vlatest`` to avoid clobbering a pinned
        version on disk.
        """
        v = str(self.version) if self.version is not None else "latest"
        return f"{self.base.replace('/', '_')}v{v}"

    # --- derived URLs -------------------------------------------------------
    @property
    def abs_url(self) -> str:
        return f"{_ABS_HOST}/abs/{self.canonical}"

    @property
    def pdf_url(self) -> str:
        return f"{_ABS_HOST}/pdf/{self.canonical}"

    @property
    def html_url(self) -> str:
        # LaTeXML HTML; available for most newer TeX-source papers only.
        return f"{_ABS_HOST}/html/{self.canonical}"

    @property
    def ar5iv_url(self) -> str:
        # Broader historical coverage than arxiv.org/html; good fallback.
        return f"https://ar5iv.labs.arxiv.org/html/{self.canonical}"

    @property
    def source_url(self) -> str:
        # Original submission source (usually a gzipped tar of TeX + figures).
        return f"{_ABS_HOST}/e-print/{self.canonical}"


def normalize(raw: str) -> ArxivId:
    """Parse any accepted form of an arXiv id into an :class:`ArxivId`.

    Accepts bare ids, ``arXiv:`` prefixes (any case), full ``arxiv.org`` URLs,
    and the ``10.48550/arXiv.<id>`` DOI form.
    """
    if not raw or not raw.strip():
        raise InvalidArxivId("empty identifier")
    s = raw.strip()

    # Strip common wrappers.
    for host in ("https://arxiv.org/abs/", "http://arxiv.org/abs/",
                 "https://arxiv.org/pdf/", "http://arxiv.org/pdf/",
                 "https://arxiv.org/html/", "http://arxiv.org/html/"):
        if s.lower().startswith(host):
            s = s[len(host):]
            break
    if s.lower().endswith(".pdf"):
        s = s[:-4]
    s = re.sub(r"^(arxiv:)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^10\.48550/arxiv\.", "", s, flags=re.IGNORECASE)
    s = s.strip()

    m = _NEW_RE.match(s)
    if m:
        return ArxivId(base=m.group(1), version=int(m.group(3)) if m.group(3) else None)
    m = _OLD_RE.match(s)
    if m:
        # Preserve original archive casing for the path (arXiv accepts as-is).
        return ArxivId(base=m.group(1), version=int(m.group(3)) if m.group(3) else None)

    raise InvalidArxivId(f"not a recognizable arXiv id: {raw!r}")


def id_from_abs_url(url: str) -> ArxivId:
    """Extract the id from an ``<entry><id>`` value such as
    ``http://arxiv.org/abs/2401.12345v1``."""
    return normalize(url)
