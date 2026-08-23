"""Result caching for verification to avoid redundant tool executions"""

import hashlib
import json
import time
from pathlib import Path
from typing import Dict, Optional


class VerificationCache:
    """Cache verification results keyed by file content hash"""

    def __init__(self, cache_dir: Optional[Path] = None):
        if cache_dir is None:
            cache_dir = Path.home() / ".cache" / "python_verifier"
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_age_days = 7  # Cache expires after 7 days

    def _compute_hash(self, code: str, preset: str) -> str:
        """Compute SHA256 hash of code + preset"""
        content = f"{preset}:{code}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _cache_path(self, code_hash: str) -> Path:
        """Get cache file path for given hash"""
        return self.cache_dir / f"{code_hash}.json"

    def get(self, code: str, preset: str) -> Optional[Dict]:
        """Retrieve cached results if valid, None otherwise"""
        code_hash = self._compute_hash(code, preset)
        cache_file = self._cache_path(code_hash)

        if not cache_file.exists():
            return None

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)

            # Check expiration
            cached_time = cached.get("cached_at", 0)
            age_seconds = time.time() - cached_time
            if age_seconds > (self.max_age_days * 86400):
                cache_file.unlink()  # Expired, delete
                return None

            return cached.get("results")
        except (json.JSONDecodeError, OSError):
            return None

    def set(self, code: str, preset: str, results: Dict) -> None:
        """Cache verification results"""
        code_hash = self._compute_hash(code, preset)
        cache_file = self._cache_path(code_hash)

        cached_data = {"cached_at": time.time(), "results": results}

        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(cached_data, f)
        except OSError:
            pass  # Silently fail if caching fails

    def clear(self) -> int:
        """Clear all cached results, return number of files deleted"""
        count = 0
        try:
            for cache_file in self.cache_dir.glob("*.json"):
                cache_file.unlink()
                count += 1
        except OSError:
            pass
        return count

    def clear_expired(self) -> int:
        """Clear expired cache entries, return number deleted"""
        count = 0
        current_time = time.time()
        max_age_seconds = self.max_age_days * 86400

        try:
            for cache_file in self.cache_dir.glob("*.json"):
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        cached = json.load(f)
                    cached_time = cached.get("cached_at", 0)
                    if current_time - cached_time > max_age_seconds:
                        cache_file.unlink()
                        count += 1
                except (json.JSONDecodeError, OSError):
                    cache_file.unlink()
                    count += 1
        except OSError:
            pass

        return count
