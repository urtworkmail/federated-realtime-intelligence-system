"""
FRIS Fusion Engine Core
Takes normalized records from any number of connectors and:
  1. Resolves duplicate entities (same company from 2 sources = 1 node)
  2. Resolves conflicting field values (source trust + recency)
  3. Writes the canonical fused graph to Neo4j
  4. Tracks provenance — which source said what
"""

import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from neo4j import AsyncGraphDatabase, AsyncDriver

from connectors.base import NormalizedRecord
from core.embedding_service import EmbeddingService, EMBEDDING_DIMENSIONS


class EntityResolver:
    """
    Decides if two records from different (or the same) sources refer
    to the same real-world entity. Uses field similarity + identifier matching.
    """

    SIMILARITY_THRESHOLD = 0.85

    # Matches scoring at or above SIMILARITY_THRESHOLD but below this are
    # still auto-merged (existing behavior, unchanged) but flagged for human
    # review — this is what makes entity-resolution confidence visible
    # instead of silent. A decisive identifier match (domain/email/SKU) is
    # never flagged since it isn't a similarity judgment call.
    REVIEW_THRESHOLD = 0.93

    # Common legal-entity suffixes that should not count as a difference
    # when comparing company names. "Acme Corp" and "Acme Corporation" are
    # the same entity — naive string similarity alone misses this.
    LEGAL_SUFFIXES = [
        "incorporated", "corporation", "company", "limited",
        "llc", "inc", "corp", "ltd", "co", "plc", "gmbh", "pvt", "pty"
    ]

    @staticmethod
    def normalize_string(s: str) -> str:
        if not s:
            return ""
        s = s.lower().strip()
        s = re.sub(r'[^\w\s]', '', s)
        s = re.sub(r'\s+', ' ', s)
        return s

    @classmethod
    def strip_legal_suffix(cls, s: str) -> str:
        """Remove trailing legal entity suffixes so 'Acme Corp' and
        'Acme Corporation' normalize to the same core string 'acme'."""
        normalized = cls.normalize_string(s)
        words = normalized.split()
        while words and words[-1] in cls.LEGAL_SUFFIXES:
            words.pop()
        return " ".join(words)

    @classmethod
    def similarity(cls, a: str, b: str) -> float:
        a_norm = cls.normalize_string(str(a))
        b_norm = cls.normalize_string(str(b))
        if not a_norm or not b_norm:
            return 0.0

        base_score = SequenceMatcher(None, a_norm, b_norm).ratio()

        # Re-score after stripping legal suffixes — if the core business
        # name matches exactly once suffixes are removed, treat as a strong match.
        a_core = cls.strip_legal_suffix(a)
        b_core = cls.strip_legal_suffix(b)
        if a_core and a_core == b_core:
            return max(base_score, 0.95)

        core_score = SequenceMatcher(None, a_core, b_core).ratio() if a_core and b_core else 0.0
        return max(base_score, core_score)

    @classmethod
    def get_match_key_fields(cls, entity_type: str) -> List[str]:
        """Which fields to compare for entity matching, per entity type."""
        type_keys = {
            "Organization": ["name", "company_name", "domain", "website", "registration_number"],
            "Person": ["email", "name", "full_name", "phone", "linkedin_url"],
            "Transaction": ["transaction_id", "reference_number"],
            "Product": ["sku", "product_id", "name"],
        }
        return type_keys.get(entity_type, ["name", "id"])

    # Fields where an exact match is decisive on its own — no need to agree
    # with other fields. Two records sharing the same domain/email/SKU are
    # the same entity even if every other field disagrees.
    DECISIVE_IDENTIFIER_FIELDS = ["domain", "website", "email", "sku", "registration_number"]

    @classmethod
    def find_match(
        cls,
        record: NormalizedRecord,
        existing_entities: List[Dict[str, Any]]
    ) -> Tuple[Optional[Dict[str, Any]], float, bool]:
        """
        Compare a new record against existing fused entities of the same type.
        Returns (match_or_None, confidence_score, is_decisive).

        Decisive identifiers (domain, email, SKU, registration number) are
        checked first and short-circuit the match — they don't get averaged
        away by other, less reliable fields like a freeform name. A decisive
        match always carries confidence 1.0 and is_decisive=True, since it
        isn't a similarity judgment call and should never enter the review
        queue (a caller can use is_decisive to skip review-flagging).
        """
        match_fields = cls.get_match_key_fields(record.entity_type)
        candidates = [e for e in existing_entities if e.get("entity_type") == record.entity_type]

        best_match = None
        best_score = 0.0

        for candidate in candidates:
            candidate_props = candidate.get("properties", {})

            # Pass 1: decisive identifier check — exact match wins immediately
            decisive_match = False
            for field in match_fields:
                if field not in cls.DECISIVE_IDENTIFIER_FIELDS:
                    continue
                record_val = record.properties.get(field)
                candidate_val = candidate_props.get(field)
                if record_val and candidate_val:
                    if str(record_val).lower().strip() == str(candidate_val).lower().strip():
                        decisive_match = True
                        break

            if decisive_match:
                return candidate, 1.0, True

            # Pass 2: fuzzy similarity across remaining comparable fields
            scores = []
            for field in match_fields:
                if field in cls.DECISIVE_IDENTIFIER_FIELDS:
                    continue
                record_val = record.properties.get(field)
                candidate_val = candidate_props.get(field)
                if record_val and candidate_val:
                    scores.append(cls.similarity(record_val, candidate_val))

            if scores:
                avg_score = sum(scores) / len(scores)
                if avg_score > best_score:
                    best_score = avg_score
                    best_match = candidate

        if best_score >= cls.SIMILARITY_THRESHOLD:
            return best_match, best_score, False
        return None, best_score, False


class ConflictResolver:
    """
    When two sources disagree on a field value, decide which wins.
    Based on: source trust score (configurable) + recency (newer wins if tied).
    """

    DEFAULT_SOURCE_TRUST = 0.5

    @staticmethod
    def resolve(
        field_name: str,
        existing_value: Any,
        existing_source_trust: float,
        existing_timestamp: datetime,
        new_value: Any,
        new_source_trust: float,
        new_timestamp: datetime
    ) -> Tuple[Any, str]:
        """
        Returns (winning_value, reason).
        """
        if existing_value == new_value:
            return existing_value, "identical_values"

        if existing_value is None:
            return new_value, "existing_was_null"

        if new_value is None:
            return existing_value, "new_was_null"

        # Trust score takes priority if meaningfully different
        trust_diff = new_source_trust - existing_source_trust
        if abs(trust_diff) > 0.15:
            if trust_diff > 0:
                return new_value, "higher_source_trust"
            return existing_value, "higher_source_trust"

        # Otherwise recency wins
        if new_timestamp > existing_timestamp:
            return new_value, "more_recent"
        return existing_value, "more_recent"


class FusionEngine:
    """
    Main fusion orchestrator. Owns the Neo4j connection and runs the
    ingest → resolve → fuse → write cycle for every batch of normalized records.
    """

    def __init__(
        self,
        neo4j_uri: str,
        neo4j_user: str,
        neo4j_password: str,
        embedding_service: Optional["EmbeddingService"] = None
    ):
        self.neo4j_uri = neo4j_uri
        self.neo4j_user = neo4j_user
        self.neo4j_password = neo4j_password
        self._driver: Optional[AsyncDriver] = None
        self.source_trust_scores: Dict[str, float] = {}  # connector_id -> trust score
        # Optional — fusion still works without it, embeddings just get skipped.
        # Kept optional so existing tests/usages of FusionEngine don't break.
        self.embedding_service = embedding_service

    async def connect(self):
        self._driver = AsyncGraphDatabase.driver(
            self.neo4j_uri,
            auth=(self.neo4j_user, self.neo4j_password)
        )
        await self._ensure_constraints()

    async def close(self):
        if self._driver:
            await self._driver.close()

    async def _ensure_constraints(self):
        """Set up Neo4j constraints and indices for performance."""
        async with self._driver.session() as session:
            await session.run(
                "CREATE CONSTRAINT entity_fris_id IF NOT EXISTS "
                "FOR (e:Entity) REQUIRE e.fris_id IS UNIQUE"
            )
            await session.run(
                "CREATE INDEX entity_type_idx IF NOT EXISTS "
                "FOR (e:Entity) ON (e.entity_type)"
            )
            await session.run(
                "CREATE INDEX entity_organization_idx IF NOT EXISTS "
                "FOR (e:Entity) ON (e.organization_id)"
            )
            # Native vector index — powers semantic similarity search over
            # fused entities (Phase 1). cosine similarity matches the
            # normalize_embeddings=True setting used in EmbeddingService,
            # so query_similar_entities() compares correctly against this.
            await session.run(
                "CREATE VECTOR INDEX entity_embedding_idx IF NOT EXISTS "
                "FOR (e:Entity) ON (e.embedding) "
                "OPTIONS {indexConfig: {"
                "`vector.dimensions`: $dims, "
                "`vector.similarity_function`: 'cosine'"
                "}}",
                dims=EMBEDDING_DIMENSIONS
            )

    def set_source_trust(self, connector_id: str, trust_score: float):
        """Configure how much to trust a given connector's data (0.0 - 1.0)."""
        self.source_trust_scores[connector_id] = max(0.0, min(1.0, trust_score))

    def get_source_trust(self, connector_id: str) -> float:
        return self.source_trust_scores.get(connector_id, ConflictResolver.DEFAULT_SOURCE_TRUST)

    async def get_existing_entities(
        self, entity_type: str, organization_id: Optional[str] = None, limit: int = 5000
    ) -> List[Dict[str, Any]]:
        """
        Pull existing entities of a type for matching against incoming
        records. organization_id scopes the candidate pool to one tenant —
        without it, records from one organization could fuse against
        another organization's entities, which would both corrupt the
        graph and leak data across the tenant boundary.
        """
        async with self._driver.session() as session:
            result = await session.run(
                "MATCH (e:Entity {entity_type: $entity_type, organization_id: $organization_id}) "
                "RETURN e.fris_id as fris_id, e.entity_type as entity_type, "
                "       properties(e) as properties "
                "LIMIT $limit",
                entity_type=entity_type, organization_id=organization_id, limit=limit
            )
            records = await result.data()
            return [
                {
                    "fris_id": r["fris_id"],
                    "entity_type": r["entity_type"],
                    "properties": r["properties"]
                }
                for r in records
            ]

    # Below this, a source's own extraction confidence (NormalizedRecord.confidence
    # — always 1.0 for structured connectors, but meaningfully lower for NLP-extracted
    # entities from unstructured documents) is enough to flag review on its own,
    # independent of entity-resolution match confidence. Otherwise a document
    # connector's uncertain guess could create a brand-new entity — which never
    # goes through EntityResolver's merge-confidence check at all — with nobody
    # ever seeing that it was a low-confidence guess.
    EXTRACTION_CONFIDENCE_REVIEW_THRESHOLD = 0.7

    async def fuse_record(self, record: NormalizedRecord, organization_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Fuse a single normalized record into the graph, scoped to one
        tenant (organization_id). Either creates a new entity or merges
        into an existing matched entity within that same tenant.
        Returns a fusion report: {action, fris_id, conflicts_resolved, confidence, needs_review}
        """
        existing_entities = await self.get_existing_entities(record.entity_type, organization_id=organization_id)
        match, confidence, is_decisive = EntityResolver.find_match(record, existing_entities)
        low_extraction_confidence = record.confidence < self.EXTRACTION_CONFIDENCE_REVIEW_THRESHOLD
        needs_review = low_extraction_confidence or (
            (not is_decisive) and (EntityResolver.SIMILARITY_THRESHOLD <= confidence < EntityResolver.REVIEW_THRESHOLD)
        )

        source_trust = self.get_source_trust(record.source_connector_id)

        if match is None:
            # New entity — create it. Confidence reported here is the
            # source's own extraction confidence, since there's no existing
            # entity to compare against yet.
            fris_id = await self._create_entity(
                record, source_trust, organization_id=organization_id, needs_review=low_extraction_confidence
            )
            return {
                "action": "created", "fris_id": fris_id, "conflicts_resolved": [],
                "confidence": round(record.confidence, 4), "needs_review": low_extraction_confidence
            }
        else:
            # Existing entity — merge with conflict resolution
            conflicts = await self._merge_entity(match, record, source_trust, confidence=confidence, needs_review=needs_review)
            return {
                "action": "merged", "fris_id": match["fris_id"], "conflicts_resolved": conflicts,
                "confidence": round(min(confidence, record.confidence), 4), "needs_review": needs_review
            }

    async def _create_entity(
        self, record: NormalizedRecord, source_trust: float,
        organization_id: Optional[str] = None, needs_review: bool = False
    ) -> str:
        properties = dict(record.properties)
        properties["fris_id"] = record.fris_id
        properties["entity_type"] = record.entity_type
        properties["organization_id"] = organization_id
        properties["_created_at"] = datetime.utcnow().isoformat()
        properties["_sources"] = [record.source_connector_id]
        properties["_last_merge_confidence"] = record.confidence
        properties["_needs_review"] = needs_review
        properties["_field_provenance"] = {
            k: {"source": record.source_connector_id, "trust": source_trust,
                "timestamp": record.ingested_at.isoformat()}
            for k in record.properties.keys()
        }

        # Embedding generation — skipped gracefully if no embedding_service
        # was wired in (keeps FusionEngine usable without it, e.g. in tests).
        if self.embedding_service is not None:
            embed_result = self.embedding_service.embed_entity(record.entity_type, properties)
            if embed_result["embedding"] is not None:
                properties["embedding"] = embed_result["embedding"]
                properties["_embedding_hash"] = embed_result["embedding_hash"]

        # Neo4j doesn't support nested dicts as properties — serialize provenance
        import json
        properties["_field_provenance"] = json.dumps(properties["_field_provenance"])
        properties["_sources"] = json.dumps(properties["_sources"])

        async with self._driver.session() as session:
            await session.run(
                "CREATE (e:Entity) SET e = $props",
                props=properties
            )
        return record.fris_id

    async def _merge_entity(
        self,
        existing: Dict[str, Any],
        new_record: NormalizedRecord,
        new_trust: float,
        confidence: float = 1.0,
        needs_review: bool = False
    ) -> List[Dict[str, Any]]:
        import json

        existing_props = existing["properties"]
        fris_id = existing["fris_id"]
        conflicts_resolved = []

        provenance_raw = existing_props.get("_field_provenance", "{}")
        try:
            provenance = json.loads(provenance_raw) if isinstance(provenance_raw, str) else provenance_raw
        except (json.JSONDecodeError, TypeError):
            provenance = {}

        sources_raw = existing_props.get("_sources", "[]")
        try:
            sources = json.loads(sources_raw) if isinstance(sources_raw, str) else sources_raw
        except (json.JSONDecodeError, TypeError):
            sources = []

        if new_record.source_connector_id not in sources:
            sources.append(new_record.source_connector_id)

        updated_properties = {}

        for field, new_value in new_record.properties.items():
            existing_value = existing_props.get(field)
            field_prov = provenance.get(field, {})
            existing_trust = field_prov.get("trust", ConflictResolver.DEFAULT_SOURCE_TRUST)
            existing_ts_str = field_prov.get("timestamp")
            existing_ts = datetime.fromisoformat(existing_ts_str) if existing_ts_str else datetime.min

            winning_value, reason = ConflictResolver.resolve(
                field_name=field,
                existing_value=existing_value,
                existing_source_trust=existing_trust,
                existing_timestamp=existing_ts,
                new_value=new_value,
                new_source_trust=new_trust,
                new_timestamp=new_record.ingested_at
            )

            if winning_value != existing_value and existing_value is not None:
                conflicts_resolved.append({
                    "field": field,
                    "old_value": existing_value,
                    "new_value": new_value,
                    "winner": winning_value,
                    "reason": reason
                })

            updated_properties[field] = winning_value
            provenance[field] = {
                "source": new_record.source_connector_id if winning_value == new_value else field_prov.get("source"),
                "trust": new_trust if winning_value == new_value else existing_trust,
                "timestamp": new_record.ingested_at.isoformat() if winning_value == new_value else existing_ts_str
            }

        updated_properties["fris_id"] = fris_id
        updated_properties["entity_type"] = existing["entity_type"]
        updated_properties["_updated_at"] = datetime.utcnow().isoformat()
        updated_properties["_sources"] = json.dumps(sources)
        updated_properties["_field_provenance"] = json.dumps(provenance)
        updated_properties["_last_merge_confidence"] = confidence
        # Once flagged, a merge stays in the review queue until a human
        # explicitly confirms/rejects it (resolve_review) — a later,
        # higher-confidence merge on the same entity must not silently
        # clear a flag nobody has looked at yet.
        updated_properties["_needs_review"] = bool(existing_props.get("_needs_review")) or needs_review

        # Re-embed only if the merged content actually changed. Most syncs
        # re-fuse entities whose fields didn't meaningfully change (a
        # connector re-pulling the same record), so this hash check avoids
        # re-running the embedding model on every sync for every entity —
        # embedding generation is the most expensive step in this pipeline.
        if self.embedding_service is not None:
            new_text = self.embedding_service.entity_to_text(
                existing["entity_type"], updated_properties
            )
            new_hash = self.embedding_service.content_hash(new_text)
            existing_hash = existing_props.get("_embedding_hash")
            if new_hash != existing_hash and new_text.strip():
                updated_properties["embedding"] = self.embedding_service.embed_text(new_text)
                updated_properties["_embedding_hash"] = new_hash

        async with self._driver.session() as session:
            await session.run(
                "MATCH (e:Entity {fris_id: $fris_id}) SET e += $props",
                fris_id=fris_id, props=updated_properties
            )

        return conflicts_resolved

    async def fuse_batch(self, records: List[NormalizedRecord], organization_id: Optional[str] = None) -> Dict[str, Any]:
        """Fuse a batch of records (typically one connector sync's output), all scoped to one tenant."""
        created = 0
        merged = 0
        flagged_for_review = 0
        all_conflicts = []

        for record in records:
            result = await self.fuse_record(record, organization_id=organization_id)
            if result["action"] == "created":
                created += 1
            else:
                merged += 1
            if result.get("needs_review"):
                flagged_for_review += 1
            all_conflicts.extend(result["conflicts_resolved"])

        return {
            "total_processed": len(records),
            "entities_created": created,
            "entities_merged": merged,
            "conflicts_resolved": len(all_conflicts),
            "flagged_for_review": flagged_for_review,
            "conflict_details": all_conflicts[:50]  # cap for response size
        }

    async def query_similar_entities(
        self,
        query_text: str,
        organization_id: Optional[str] = None,
        entity_type: Optional[str] = None,
        top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Semantic search over fused entities, scoped to one tenant. Embeds
        the query text with the same model used for entities, then finds
        the nearest neighbors in the vector index. This is the method
        Phase 2's SLM query layer and Phase 3's Q/A layer will call once a
        question gets translated into a search rather than an exact Cypher
        match.

        Note: the vector index query fetches top_k globally *before* the
        tenant/entity_type WHERE filter runs, so a busy multi-tenant index
        can return fewer than top_k results after filtering — same
        trade-off the existing entity_type filter already made. Acceptable
        at current scale; revisit with a tenant-partitioned index if it
        becomes a real gap.
        """
        if self.embedding_service is None:
            raise RuntimeError(
                "query_similar_entities requires an embedding_service — "
                "none was configured on this FusionEngine instance."
            )

        query_vector = self.embedding_service.embed_text(query_text)

        async with self._driver.session() as session:
            result = await session.run(
                "CALL db.index.vector.queryNodes('entity_embedding_idx', $top_k, $vector) "
                "YIELD node, score "
                "WHERE node.organization_id = $organization_id "
                "AND ($entity_type IS NULL OR node.entity_type = $entity_type) "
                "RETURN node.fris_id as fris_id, node.entity_type as entity_type, "
                "       properties(node) as properties, score "
                "ORDER BY score DESC",
                top_k=top_k, vector=query_vector, entity_type=entity_type, organization_id=organization_id
            )
            records = await result.data()

        return [
            {
                "fris_id": r["fris_id"],
                "entity_type": r["entity_type"],
                "properties": {k: v for k, v in r["properties"].items() if k != "embedding"},
                "similarity_score": round(r["score"], 4)
            }
            for r in records
        ]

    async def backfill_embeddings(self, batch_size: int = 100, organization_id: Optional[str] = None) -> Dict[str, Any]:
        """
        One-time (or rerun-safe) utility: generates embeddings for any
        entity that doesn't have one yet. This matters for FRIS instances
        that fused data before this feature existed — without this, only
        newly fused entities after the upgrade would be searchable.
        Safe to rerun: entities that already have an embedding are skipped.
        organization_id scopes the backfill to one tenant when provided.
        """
        if self.embedding_service is None:
            raise RuntimeError("backfill_embeddings requires an embedding_service.")

        async with self._driver.session() as session:
            result = await session.run(
                "MATCH (e:Entity) WHERE e.embedding IS NULL "
                "AND ($organization_id IS NULL OR e.organization_id = $organization_id) "
                "RETURN e.fris_id as fris_id, e.entity_type as entity_type, "
                "       properties(e) as properties "
                "LIMIT $limit",
                limit=batch_size, organization_id=organization_id
            )
            entities = await result.data()

        updated = 0
        for entity in entities:
            embed_result = self.embedding_service.embed_entity(
                entity["entity_type"], entity["properties"]
            )
            if embed_result["embedding"] is None:
                continue
            async with self._driver.session() as session:
                await session.run(
                    "MATCH (e:Entity {fris_id: $fris_id}) "
                    "SET e.embedding = $embedding, e._embedding_hash = $hash",
                    fris_id=entity["fris_id"],
                    embedding=embed_result["embedding"],
                    hash=embed_result["embedding_hash"]
                )
            updated += 1

        return {
            "entities_found_without_embedding": len(entities),
            "entities_embedded_this_batch": updated,
            "batch_complete": len(entities) < batch_size
        }

    async def create_relationship(
        self,
        from_fris_id: str,
        to_fris_id: str,
        relationship_type: str,
        organization_id: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None
    ):
        """
        Create an edge between two entities — e.g. WORKS_AT, INVESTED_IN.
        When organization_id is given, both endpoints must belong to that
        tenant or the MATCH finds nothing and no edge is created — this is
        what stops a caller from ever linking two different tenants' data.
        """
        props = properties or {}
        async with self._driver.session() as session:
            await session.run(
                f"MATCH (a:Entity {{fris_id: $from_id}}), (b:Entity {{fris_id: $to_id}}) "
                f"WHERE $organization_id IS NULL "
                f"OR (a.organization_id = $organization_id AND b.organization_id = $organization_id) "
                f"MERGE (a)-[r:{relationship_type}]->(b) "
                f"SET r += $props",
                from_id=from_fris_id, to_id=to_fris_id, props=props, organization_id=organization_id
            )

    async def query_graph(self, cypher_query: str, params: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """
        Run a raw Cypher query — used only by the admin-only /api/graph/query
        endpoint. This method itself cannot enforce tenant scoping against
        arbitrary caller-supplied Cypher, which is exactly why that endpoint
        is gated to owner/admin roles and audit-logged on every call rather
        than exposed to every authenticated account.
        """
        async with self._driver.session() as session:
            result = await session.run(cypher_query, params or {})
            return await result.data()

    async def get_stats(self, organization_id: Optional[str] = None) -> Dict[str, Any]:
        """Per-tenant stats for the dashboard."""
        async with self._driver.session() as session:
            entity_count = await (await session.run(
                "MATCH (e:Entity {organization_id: $organization_id}) RETURN count(e) as c",
                organization_id=organization_id
            )).single()
            rel_count = await (await session.run(
                "MATCH (a:Entity {organization_id: $organization_id})-[r]->(b:Entity {organization_id: $organization_id}) "
                "RETURN count(r) as c",
                organization_id=organization_id
            )).single()
            by_type = await (await session.run(
                "MATCH (e:Entity {organization_id: $organization_id}) "
                "RETURN e.entity_type as type, count(e) as count "
                "ORDER BY count DESC",
                organization_id=organization_id
            )).data()
            rel_by_type = await (await session.run(
                "MATCH (a:Entity {organization_id: $organization_id})-[r]->(b:Entity {organization_id: $organization_id}) "
                "RETURN type(r) as type, count(r) as count "
                "ORDER BY count DESC",
                organization_id=organization_id
            )).data()

        return {
            "total_entities": entity_count["c"] if entity_count else 0,
            "total_relationships": rel_count["c"] if rel_count else 0,
            "entities_by_type": by_type,
            "relationships_by_type": rel_by_type
        }

    async def get_review_queue(self, organization_id: str, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        """Entities whose most recent merge was flagged for human review (see EntityResolver.REVIEW_THRESHOLD)."""
        async with self._driver.session() as session:
            total = await (await session.run(
                "MATCH (e:Entity {organization_id: $organization_id, _needs_review: true}) RETURN count(e) as c",
                organization_id=organization_id
            )).single()
            result = await session.run(
                "MATCH (e:Entity {organization_id: $organization_id, _needs_review: true}) "
                "RETURN e.fris_id as fris_id, e.entity_type as entity_type, "
                "       e._last_merge_confidence as confidence, properties(e) as properties "
                "ORDER BY e._last_merge_confidence ASC SKIP $offset LIMIT $limit",
                organization_id=organization_id, limit=limit, offset=offset
            )
            rows = await result.data()
        return {
            "total": total["c"] if total else 0,
            "entities": [
                {
                    "fris_id": r["fris_id"], "entity_type": r["entity_type"],
                    "confidence": r["confidence"],
                    "properties": {k: v for k, v in r["properties"].items() if k != "embedding"}
                }
                for r in rows
            ]
        }

    async def resolve_review(self, fris_id: str, organization_id: str, action: str) -> bool:
        """
        Human confirms or rejects a flagged merge. Both actions clear the
        flag (this is attribution/audit, not an "undo the merge" — splitting
        a fused entity back apart is a heavier operation reserved for a
        later phase); the distinction is recorded so a rejection can inform
        future match-confidence tuning per entity type.
        """
        if action not in ("confirm", "reject"):
            raise ValueError("action must be 'confirm' or 'reject'")
        async with self._driver.session() as session:
            result = await session.run(
                "MATCH (e:Entity {fris_id: $fris_id, organization_id: $organization_id}) "
                "SET e._needs_review = false, e._review_action = $action, "
                "    e._reviewed_at = $reviewed_at "
                "RETURN e.fris_id as fris_id",
                fris_id=fris_id, organization_id=organization_id,
                action=action, reviewed_at=datetime.utcnow().isoformat()
            )
            record = await result.single()
        return record is not None

    async def get_entity_explanation(self, fris_id: str, organization_id: str) -> Optional[Dict[str, Any]]:
        """
        The decision trail behind one fused entity: which source contributed
        each field's winning value, at what trust score and timestamp, plus
        the entity's overall merge confidence and review status. This is
        what turns "the graph says X" into "the graph says X because Y" —
        the concrete answer to compliance/audit questions (EU AI Act, GDPR)
        about how an automated fusion decision was reached, and the same
        data every silent conflict-resolution decision in _merge_entity
        already computes, just not previously surfaced anywhere.
        """
        import json as _json

        async with self._driver.session() as session:
            result = await session.run(
                "MATCH (e:Entity {fris_id: $fris_id, organization_id: $organization_id}) "
                "RETURN properties(e) as properties",
                fris_id=fris_id, organization_id=organization_id
            )
            record = await result.single()
        if not record:
            return None

        props = record["properties"]

        def _parse(raw, default):
            if raw is None:
                return default
            if isinstance(raw, str):
                try:
                    return _json.loads(raw)
                except (ValueError, TypeError):
                    return default
            return raw

        provenance = _parse(props.get("_field_provenance"), {})
        sources = _parse(props.get("_sources"), [])

        fields = [
            {
                "field": field,
                "value": props.get(field),
                "contributing_source": prov.get("source"),
                "source_trust_score": prov.get("trust"),
                "recorded_at": prov.get("timestamp"),
            }
            for field, prov in provenance.items()
            if not field.startswith("_") and field not in ("fris_id", "entity_type", "organization_id", "embedding")
        ]

        return {
            "fris_id": fris_id,
            "entity_type": props.get("entity_type"),
            "contributing_sources": sources,
            "fields": fields,
            "match_confidence": props.get("_last_merge_confidence"),
            "needs_review": bool(props.get("_needs_review")),
            "review_action": props.get("_review_action"),
            "reviewed_at": props.get("_reviewed_at"),
            "created_at": props.get("_created_at"),
            "updated_at": props.get("_updated_at"),
        }

    # ---------- Graph Explorer (structured browse + visualization) ----------

    async def search_nodes(
        self, organization_id: Optional[str] = None, entity_type: Optional[str] = None, text: Optional[str] = None,
        limit: int = 50, offset: int = 0
    ) -> Dict[str, Any]:
        """Paged, filterable node browse — the table view of the Graph Explorer, scoped to one tenant."""
        conditions = []
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if organization_id:
            conditions.append("e.organization_id = $organization_id")
            params["organization_id"] = organization_id
        if entity_type:
            conditions.append("e.entity_type = $entity_type")
            params["entity_type"] = entity_type
        if text:
            conditions.append("any(k in keys(e) WHERE toString(e[k]) CONTAINS $text)")
            params["text"] = text
        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        async with self._driver.session() as session:
            total = await (await session.run(
                f"MATCH (e:Entity) {where_sql} RETURN count(e) as c", params
            )).single()
            result = await session.run(
                f"MATCH (e:Entity) {where_sql} "
                f"RETURN e.fris_id as fris_id, e.entity_type as entity_type, properties(e) as properties "
                f"ORDER BY e.fris_id SKIP $offset LIMIT $limit",
                params
            )
            rows = await result.data()

        nodes = [
            {
                "fris_id": r["fris_id"], "entity_type": r["entity_type"],
                "properties": {k: v for k, v in r["properties"].items() if k != "embedding"}
            }
            for r in rows
        ]
        return {"total": total["c"] if total else 0, "nodes": nodes, "limit": limit, "offset": offset}

    async def get_neighbors(self, fris_id: str, organization_id: Optional[str] = None, depth: int = 1) -> Dict[str, Any]:
        """
        Relationship expansion around a single node — powers 'click to
        expand'. Both the center node and every traversed neighbor are
        constrained to organization_id, so a fris_id from another tenant
        returns an empty subgraph rather than that tenant's data, and
        expansion can never walk into another tenant's entities even via a
        pre-existing relationship.
        """
        depth = max(1, min(depth, 3))
        records = []
        async with self._driver.session() as session:
            result = await session.run(
                f"MATCH (center:Entity {{fris_id: $fris_id, organization_id: $organization_id}}) "
                f"OPTIONAL MATCH path = (center)-[*1..{depth}]-(other:Entity {{organization_id: $organization_id}}) "
                f"RETURN center, [n in nodes(path) | n] as path_nodes, "
                f"       [r in relationships(path) | r] as path_rels",
                fris_id=fris_id, organization_id=organization_id
            )
            # Note: .data() would flatten Node/Relationship graph objects down to
            # plain property dicts, losing .type/.start_node/.end_node — so we
            # walk raw records here instead, keeping graph-typed values intact.
            async for record in result:
                records.append({
                    "center": record["center"],
                    "path_nodes": record["path_nodes"],
                    "path_rels": record["path_rels"],
                })

        return self._paths_to_subgraph(records, center_fris_id=fris_id)

    async def get_subgraph(self, fris_id: str, organization_id: Optional[str] = None, depth: int = 2) -> Dict[str, Any]:
        """Same shape as get_neighbors, sized for the visual graph explorer."""
        return await self.get_neighbors(fris_id, organization_id=organization_id, depth=depth)

    def _paths_to_subgraph(self, records: List[Dict[str, Any]], center_fris_id: str) -> Dict[str, Any]:
        nodes_by_id: Dict[str, Dict[str, Any]] = {}
        edges = []
        seen_edges = set()

        for record in records:
            center = record.get("center")
            if center:
                props = dict(center)
                fris_id = props.get("fris_id")
                if fris_id and fris_id not in nodes_by_id:
                    nodes_by_id[fris_id] = {
                        "fris_id": fris_id, "entity_type": props.get("entity_type"),
                        "properties": {k: v for k, v in props.items() if k != "embedding"}
                    }
            for node in (record.get("path_nodes") or []):
                if node is None:
                    continue
                props = dict(node)
                fris_id = props.get("fris_id")
                if fris_id and fris_id not in nodes_by_id:
                    nodes_by_id[fris_id] = {
                        "fris_id": fris_id, "entity_type": props.get("entity_type"),
                        "properties": {k: v for k, v in props.items() if k != "embedding"}
                    }
            for rel in (record.get("path_rels") or []):
                if rel is None:
                    continue
                edge_key = (rel.start_node.get("fris_id"), rel.type, rel.end_node.get("fris_id"))
                if edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)
                edges.append({
                    "from": rel.start_node.get("fris_id"),
                    "to": rel.end_node.get("fris_id"),
                    "type": rel.type,
                    "properties": dict(rel)
                })

        return {
            "center": center_fris_id,
            "nodes": list(nodes_by_id.values()),
            "edges": edges
        }
