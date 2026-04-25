from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path
from typing import Any, Callable, TypeVar

from src.config import CACHE_DIR

T = TypeVar("T")


def _key(namespace: str, args: dict[str, Any]) -> Path:
    blob = json.dumps(args, sort_keys=True, default=str).encode()
    digest = hashlib.sha256(blob).hexdigest()[:16]
    return CACHE_DIR / namespace / f"{digest}.pkl"


def disk_cache(namespace: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Disk cache keyed by call args. Use on tool wrappers."""

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        def wrapper(*args: Any, **kwargs: Any) -> T:
            path = _key(namespace, {"args": args, "kwargs": kwargs})
            if path.exists():
                with path.open("rb") as f:
                    return pickle.load(f)
            result = fn(*args, **kwargs)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("wb") as f:
                pickle.dump(result, f)
            return result

        return wrapper

    return decorator
