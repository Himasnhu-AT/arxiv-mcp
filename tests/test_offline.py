"""Network-free unit tests: id normalization, cache eviction, session pinning.

Run with pytest (``uv run python -m pytest tests/test_offline.py``) or directly
(``uv run python tests/test_offline.py``).
"""

import os
import tempfile

os.environ.setdefault("ARXIV_MCP_HOME", tempfile.mkdtemp(prefix="arxiv-test-"))

from arxiv_mcp.ids import normalize, InvalidArxivId  # noqa: E402
from arxiv_mcp.cache import Cache, CacheConfig  # noqa: E402


def test_id_normalization():
    cases = {
        "2401.12345": ("2401.12345", None),
        "arXiv:2401.12345v2": ("2401.12345", 2),
        "ARXIV:2401.12345": ("2401.12345", None),
        "https://arxiv.org/abs/1706.03762v5": ("1706.03762", 5),
        "https://arxiv.org/pdf/1706.03762.pdf": ("1706.03762", None),
        "hep-th/9901001": ("hep-th/9901001", None),
        "math.GT/0107042v1": ("math.GT/0107042", 1),
        "10.48550/arXiv.2401.12345": ("2401.12345", None),
    }
    for raw, (base, ver) in cases.items():
        aid = normalize(raw)
        assert aid.base == base, f"{raw}: base {aid.base!r} != {base!r}"
        assert aid.version == ver, f"{raw}: version {aid.version!r} != {ver!r}"


def test_id_rejects_garbage():
    for bad in ("", "   ", "not-an-id", "hello world"):
        try:
            normalize(bad)
        except InvalidArxivId:
            continue
        raise AssertionError(f"should have rejected {bad!r}")


def test_id_urls_and_keys():
    aid = normalize("2401.12345v2")
    assert aid.canonical == "2401.12345v2"
    assert aid.pdf_url == "https://arxiv.org/pdf/2401.12345v2"
    assert aid.abs_url == "https://arxiv.org/abs/2401.12345v2"
    # Versioned and bare ids get distinct cache keys (never clobber).
    assert normalize("2401.12345").key != normalize("2401.12345v2").key
    # Old-scheme slash is made filesystem-safe.
    assert "/" not in normalize("hep-th/9901001").key


def _fresh_cache(**kw) -> Cache:
    root = tempfile.mkdtemp(prefix="arxiv-test-")
    return Cache(CacheConfig(root=root, **kw))


def test_cache_eviction_by_count():
    c = _fresh_cache(max_papers=3, max_disk_mb=10_000)
    for i in range(6):
        c.set_text(normalize(f"2401.0000{i}"), "x" * 1000)
    assert c.stats()["papers"] <= 3


def test_cache_eviction_by_size():
    # Tiny disk cap forces size-based eviction well before the count cap.
    c = _fresh_cache(max_papers=1000, max_disk_mb=1)
    for i in range(50):
        c.set_text(normalize(f"2402.0{i:04d}"), "x" * 100_000)  # ~100 KB each
    disk_mb = c.stats()["disk_mb"]
    assert disk_mb <= 1.5, f"disk not bounded: {disk_mb} MB"


def test_session_pin_survives_eviction():
    c = _fresh_cache(max_papers=3, max_disk_mb=10_000)
    keep = normalize("2402.99999")
    c.start_session("s1")
    c.pin("s1", keep)
    c.set_text(keep, "y" * 1000)
    for i in range(10):
        c.set_text(normalize(f"2402.1000{i}"), "z" * 1000)
    assert c.get_text(keep) is not None, "pinned paper was evicted"
    # After the session ends, the pin is released.
    assert c.end_session("s1") == 1


def test_metadata_roundtrip_and_versioning():
    c = _fresh_cache()
    aid = normalize("2401.55555v1")
    c.set_metadata(aid, {"title": "Hello", "id": "2401.55555v1"})
    got = c.get_metadata(aid)
    assert got and got["title"] == "Hello"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} offline tests passed")
