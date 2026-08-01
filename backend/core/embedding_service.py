"""
FRIS Embedding Service
Generates vector embeddings for fused entities so the graph becomes
semantically searchable, not just queryable by exact field match.

This is Phase 1 of the next-phase roadmap. Everything downstream
(SLM-driven querying, the NLP Q/A layer) depends on this existing first.

Design choices that matter:
  - Model runs fully offline once downloaded. No external API calls at
    embedding time. This is non-negotiable for FRIS's air-gapped/sovereign
    deployment model — a client running a fully isolated instance cannot
    have embeddings silently depend on an internet connection.
  - CPU-friendly, small model (all-MiniLM-L6-v2, 384 dimensions). Good
    enough quality for entity-level semantic search, fast enough to run
    on commodity hardware without requiring a GPU, which matches FRIS's
    "tiered by compute" philosophy from the existing engine.
  - Embeddings are generated from a constructed text representation of
    the entity (its meaningful fields concatenated), not from raw JSON,
    so the vector actually reflects what the entity *is* in plain language.
"""

import hashlib
from typing import Any, Dict, List, Optional

from sentence_transformers import SentenceTransformer


# Fields to exclude when building the text representation of an entity —
# internal bookkeeping fields add noise to the embedding, not meaning.
EXCLUDED_FIELDS = {
    "fris_id", "entity_type", "_created_at", "_updated_at",
    "_sources", "_field_provenance", "_embedding", "_embedding_hash"
}

MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIMENSIONS = 384


class EmbeddingService:
    """
    Loads the embedding model once at startup and reuses it for every
    entity and document embedded afterward. Loading is the expensive part
    (downloading/initializing weights), so this should be a long-lived
    singleton, not instantiated per-request.
    """

    def __init__(self, model_name: str = MODEL_NAME):
        self.model_name = model_name
        self._model: Optional[SentenceTransformer] = None

    def load(self):
        """
        Loads the model into memory. Called once at FastAPI startup.
        First run downloads the model weights; for air-gapped deployments,
        the model must be pre-downloaded and cached locally before the
        instance goes fully offline (documented in the deployment runbook).
        """
        if self._model is None:
            self._model = SentenceTransformer(self.model_name)

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @staticmethod
    def entity_to_text(entity_type: str, properties: Dict[str, Any]) -> str:
        """
        Builds a plain-language text representation of an entity from its
        properties. This text is what actually gets embedded — the goal is
        a sentence-like summary, not a dump of raw key-value pairs, since
        embedding models are trained on natural language, not JSON.

        Example output:
          "Organization: Acme Corporation. domain: acme.com. sector: logistics.
           country: UAE. revenue: 4200000."
        """
        parts = [f"{entity_type}:"]
        for key, value in properties.items():
            if key in EXCLUDED_FIELDS or key.startswith("_"):
                continue
            if value is None or value == "":
                continue
            parts.append(f"{key}: {value}.")
        return " ".join(parts)

    @staticmethod
    def content_hash(text: str) -> str:
        """
        Hash of the text used to generate an embedding. Stored alongside
        the embedding so re-fusion of an entity can skip re-embedding when
        nothing meaningful actually changed — embedding generation is the
        most expensive step in the fusion pipeline, so this avoids wasted
        compute on every sync when most entities are unchanged.
        """
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def embed_text(self, text: str) -> List[float]:
        """
        Generates a single embedding vector for a piece of text.
        Used for entity properties and for embedding a user's natural
        language query in Phase 2/3, so the same model produces vectors
        in the same space for both sides of a similarity search.
        """
        if not self.is_loaded:
            self.load()
        vector = self._model.encode(text, convert_to_numpy=True, normalize_embeddings=True)
        return vector.tolist()

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Batched embedding generation — significantly faster than calling
        embed_text() in a loop when fusing many records from one connector
        sync, since the model can process the batch in parallel internally.
        """
        if not self.is_loaded:
            self.load()
        if not texts:
            return []
        vectors = self._model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
        return vectors.tolist()

    def embed_entity(self, entity_type: str, properties: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convenience wrapper: builds the text representation, generates the
        embedding, and returns both plus the content hash, ready to attach
        to the entity's Neo4j properties.
        """
        text = self.entity_to_text(entity_type, properties)
        # entity_to_text always returns at least "EntityType:" even when
        # there are zero real fields, so checking truthiness alone isn't
        # enough — that prefix-only string would otherwise get embedded
        # as if it were meaningful content. Require the text to contain
        # something beyond the bare "EntityType:" label.
        if not text.strip() or text.strip() == f"{entity_type}:":
            return {"embedding": None, "embedding_hash": None, "embedding_text": ""}
        embedding = self.embed_text(text)
        return {
            "embedding": embedding,
            "embedding_hash": self.content_hash(text),
            "embedding_text": text
        }
