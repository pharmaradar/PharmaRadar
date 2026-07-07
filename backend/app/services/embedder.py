"""Embedding service — Voyage AI primary, FastEmbed fallback (padded to 512 dims)."""
import structlog
from app.config import get_settings

logger = structlog.get_logger(__name__)

EMBEDDING_DIM = 512
_fastembed_model = None  # lazy-loaded, cached in process


def _pad(vec: list[float]) -> list[float]:
    if len(vec) >= EMBEDDING_DIM:
        return vec[:EMBEDDING_DIM]
    return vec + [0.0] * (EMBEDDING_DIM - len(vec))


def embed_texts(texts: list[str]) -> list[list[float] | None]:
    """Embed a batch of texts. Returns list of embeddings (None on failure)."""
    if not texts:
        return []

    clean = [t[:4000] for t in texts]
    settings = get_settings()

    # Primary: Voyage AI
    if settings.voyage_api_key:
        try:
            import voyageai
            client = voyageai.Client(api_key=settings.voyage_api_key)
            result = client.embed(clean, model="voyage-3-lite")
            return [_pad(e) for e in result.embeddings]
        except Exception as e:
            logger.warning("embedder.voyage_failed", error=str(e))

    # Fallback: FastEmbed (384 dims padded to 512)
    try:
        global _fastembed_model
        if _fastembed_model is None:
            from fastembed import TextEmbedding
            _fastembed_model = TextEmbedding("BAAI/bge-small-en-v1.5")
            logger.info("embedder.fastembed_loaded")
        embeddings = list(_fastembed_model.embed(clean))
        return [_pad(e.tolist()) for e in embeddings]
    except Exception as e:
        logger.warning("embedder.fastembed_failed", error=str(e))

    return [None] * len(texts)


def embed_one(text: str) -> list[float] | None:
    results = embed_texts([text])
    return results[0] if results else None
