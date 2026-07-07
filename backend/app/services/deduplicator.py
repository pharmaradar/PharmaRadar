"""SHA256 exact deduplication."""
import hashlib
import re


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def sha256_hash(content: str) -> str:
    return hashlib.sha256(_normalise(content).encode()).hexdigest()
