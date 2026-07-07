"""Test SHA256 deduplication logic (no external services needed)."""
from app.services.deduplicator import sha256_hash


def test_identical_content_same_hash():
    h1 = sha256_hash("Hello World")
    h2 = sha256_hash("Hello   World")  # extra whitespace
    assert h1 == h2


def test_different_content_different_hash():
    h1 = sha256_hash("Roche announces trial results")
    h2 = sha256_hash("Pfizer announces trial results")
    assert h1 != h2


def test_case_insensitive():
    h1 = sha256_hash("ROCHE")
    h2 = sha256_hash("roche")
    assert h1 == h2
