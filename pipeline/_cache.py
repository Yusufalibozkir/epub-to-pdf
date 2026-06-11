"""
Content-addressed caching layer for the EPUB-to-PDF pipeline.

Stores and retrieves intermediate results keyed by SHA-256 hashes, so that
re-running with the same or compatible inputs skips expensive computation.

Cache structure (under artifact_dir / .pipeline_cache /):
  doc_clean/<hash>    -> per-document cleaned HTML fragment
  ai_plan/<hash>      -> AI structure-planning results
  css/<hash>          -> generated CSS text
  toc_numbers/<hash>  -> resolved TOC page-number dict (JSON)
  page_renders/<hash> -> list of rendered QA page paths (JSON)
  qa_verdict/<hash>   -> QAVerdict JSON snapshot
"""
from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path
from typing import Any, Optional


class PipelineCache:
    """Content-addressed, directory-backed cache for pipeline intermediate results.

    Each cached item is stored at: <root>/<namespace>/<first-2-chars>/<full-hash>
    The two-char subdirectory prevents any single folder from having too many files.
    """

    def __init__(self, cache_root: Path):
        self._root = cache_root / ".pipeline_cache"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def store(self, namespace: str, key: str, data: Any) -> None:
        """Store a pickle-serializable object under namespace/key."""
        blob = pickle.dumps(data)
        path = self._key_path(namespace, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)

    def load(self, namespace: str, key: str) -> Optional[Any]:
        """Load a previously stored object, or None if missing."""
        path = self._key_path(namespace, key)
        if not path.exists():
            return None
        try:
            return pickle.loads(path.read_bytes())
        except Exception:
            return None

    def has(self, namespace: str, key: str) -> bool:
        return self._key_path(namespace, key).exists()

    def store_text(self, namespace: str, key: str, text: str) -> None:
        path = self._key_path(namespace, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def load_text(self, namespace: str, key: str) -> Optional[str]:
        path = self._key_path(namespace, key)
        if not path.exists():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return None

    def store_json(self, namespace: str, key: str, obj: Any) -> None:
        self.store_text(namespace, key, json.dumps(obj, ensure_ascii=False, sort_keys=True))

    def load_json(self, namespace: str, key: str) -> Optional[Any]:
        text = self.load_text(namespace, key)
        if text is None:
            return None
        try:
            return json.loads(text)
        except Exception:
            return None

    def invalidate(self, namespace: str, key: Optional[str] = None) -> None:
        """Delete a single cache entry or an entire namespace."""
        if key is None:
            ns_dir = self._root / namespace
            if ns_dir.exists():
                import shutil
                shutil.rmtree(ns_dir)
        else:
            path = self._key_path(namespace, key)
            if path.exists():
                path.unlink()

    def invalidate_all(self) -> None:
        """Wipe the entire cache."""
        if self._root.exists():
            import shutil
            shutil.rmtree(self._root)

    # ------------------------------------------------------------------
    # Hash helpers
    # ------------------------------------------------------------------

    @staticmethod
    def hash_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def hash_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def hash_file(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def hash_object(obj: Any) -> str:
        """Hash a JSON-serializable object."""
        return hashlib.sha256(
            json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def hash_combined(*parts: str) -> str:
        """Combine multiple hash-like strings into one."""
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _key_path(self, namespace: str, key: str) -> Path:
        # First 2 chars as subdirectory to avoid directory bloat
        return self._root / namespace / key[:2] / key
