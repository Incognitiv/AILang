"""Result caching for verification to avoid redundant tool executions.

Storage validation is not proof of context validity. Callers must still avoid
reusing results when tools, configuration or dependencies have changed.
"""

import hashlib
import time
from pathlib import Path

from .cache_storage import load_results, publish_results, remove_entry


class VerificationCache:
    """Cache verification results keyed by file content hash."""

    def __init__(self, cache_dir: Path | None = None):
        if cache_dir is None:
            cache_dir = Path.home() / ".cache" / "python_verifier"
        self.cache_dir = cache_dir
        self.max_age_days = 7
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Optional storage must not prevent the actual checks from running.
            pass

    def _compute_hash(self, code: str, preset: str) -> str:
        """Compute SHA256 hash of code + preset."""
        content = f"{preset}:{code}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _cache_path(self, code_hash: str) -> Path:
        """Get cache file path for given hash."""
        return self.cache_dir / f"{code_hash}.json"

    def get(self, code: str, preset: str) -> dict | None:
        """Read a valid entry without unlinking a path a writer might replace."""
        return load_results(
            self._cache_path(self._compute_hash(code, preset)),
            time.time(),
            self.max_age_days * 86400,
        )

    def set(self, code: str, preset: str, results: dict) -> None:
        """Cache a complete result, without making persistence a requirement."""
        publish_results(
            self._cache_path(self._compute_hash(code, preset)), results, time.time()
        )

    def clear(self) -> int:
        """Clear available entries even when individual files cannot be removed."""
        count = 0
        try:
            for cache_file in self.cache_dir.glob("*.json"):
                count += int(remove_entry(cache_file))
        except OSError:
            pass
        return count

    def clear_expired(self) -> int:
        """Best-effort cleanup of expired or invalid disposable entries."""
        count = 0
        now = time.time()
        max_age_seconds = self.max_age_days * 86400
        try:
            for cache_file in self.cache_dir.glob("*.json"):
                if load_results(cache_file, now, max_age_seconds) is None:
                    count += int(remove_entry(cache_file))
        except OSError:
            pass
        return count
