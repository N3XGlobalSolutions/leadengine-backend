"""Isolated RAG Service for Outbound AI Agent, querying the existing Qdrant collection."""

from typing import Any, Dict, List, Optional
from qdrant_client.http import models as qmodels

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.core.database import get_qdrant_client
from app.services.ai.outbound_llm_service import outbound_llm_service

settings = get_settings()
logger = get_logger(__name__)


class OutboundRAGService:
    """Isolated tenant-safe RAG querying for Outbound Sales Agent."""

    def __init__(self):
        self.qdrant_client = get_qdrant_client()
        self.collection_name = settings.EFFECTIVE_QDRANT_COLLECTION

    def search(
        self,
        query: str,
        tenant_id: str,
        limit: int = 5,
        min_score: float = 0.25,
        usage: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Search company documents in Qdrant, strictly filtered by tenant_id.

        Args:
            query: User's natural language question
            tenant_id: Organization ID for isolation filtering
            limit: Maximum document chunks to retrieve
            min_score: Relevance threshold. Titan V2 cosine scores for a short chat
                question against document chunks are typically 0.25-0.5, so 0.25
                keeps real matches (0.35 was dropping them).
            usage: Optional dict; embedding tokens are added to usage["embedding"].

        Returns:
            List of matching dictionaries:
            - text: document chunk text
            - source: document title or filename
            - score: vector match confidence score
        """
        try:
            logger.info(f"[OutboundRAG] Searching knowledge for tenant {tenant_id}: '{query}'")
            
            # Generate query vector using isolated LLM service
            query_vector, embed_tokens = outbound_llm_service.embed(query)
            if usage is not None:
                usage["embedding"] = int(usage.get("embedding", 0)) + embed_tokens

            # Query Qdrant with tenant filter
            search_result = self.qdrant_client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                query_filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key="tenant_id",
                            match=qmodels.MatchValue(value=tenant_id)
                        )
                    ]
                ),
                limit=limit,
            )

            results = []
            for hit in search_result.points:
                score = getattr(hit, "score", 0.0)
                if score < min_score:
                    continue

                results.append({
                    "text": hit.payload.get("text", ""),
                    "source": hit.payload.get("source", ""),
                    "score": score,
                })

            top = max((getattr(h, "score", 0.0) for h in search_result.points), default=0.0)
            logger.info(f"[OutboundRAG] Found {len(results)} relevant chunks (Threshold: {min_score}, top score: {top:.3f})")
            return results

        except Exception as e:
            logger.error(f"[OutboundRAG] Qdrant search failed: {e}")
            return []


# Singleton service instance
outbound_rag_service = OutboundRAGService()
