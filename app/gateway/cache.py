"""Cache mémoire des réponses LLM (§5.4 — protège la marge).

Les appels identiques (même tier + même prompt, même workspace) renvoient la
réponse déjà obtenue, sans rappeler Claude → coût 0. TTL simple + éviction.
"""
import hashlib
import time
from threading import Lock


class ResponseCache:
    def __init__(self, ttl_seconds: int = 3600, max_entries: int = 1000) -> None:
        self._store: dict[str, tuple[float, dict]] = {}
        self._lock = Lock()
        self.ttl = ttl_seconds
        self.max_entries = max_entries

    @staticmethod
    def make_key(workspace_id, tier: str, system: str, user: str, max_tokens: int) -> str:
        raw = f"{workspace_id}|{tier}|{max_tokens}|{system}|{user}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str):
        with self._lock:
            item = self._store.get(key)
            if not item:
                return None
            expires_at, data = item
            if expires_at < time.time():
                self._store.pop(key, None)
                return None
            return data

    def set(self, key: str, data: dict) -> None:
        with self._lock:
            if len(self._store) >= self.max_entries and key not in self._store:
                self._store.pop(next(iter(self._store)), None)  # éviction simple (FIFO)
            self._store[key] = (time.time() + self.ttl, data)

    def delete(self, key: str) -> None:
        """Retire une entrée — pour écarter une réponse mise en cache puis jugée invalide."""
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


# instance partagée
response_cache = ResponseCache()
