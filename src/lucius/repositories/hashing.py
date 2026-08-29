from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_json_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return sha256_bytes(encoded)


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def looks_binary(path: Path, sample_size: int = 8192) -> bool:
    sample = path.read_bytes()[:sample_size]
    if b"\0" in sample:
        return True
    if not sample:
        return False
    try:
        sample.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


def manifest_identity(files: list[dict[str, Any]]) -> str:
    stable = [
        {
            "path": item["path"],
            "size_bytes": item["size_bytes"],
            "content_hash": item.get("content_hash"),
            "git_status": item.get("git_status"),
            "is_sensitive": item.get("is_sensitive"),
            "is_binary": item.get("is_binary"),
        }
        for item in sorted(files, key=lambda row: row["path"])
    ]
    return stable_json_hash(stable)

