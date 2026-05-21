"""Async RAG Service using Motor and async LLM calls."""

import uuid
from typing import Any, Dict, List, Optional, Tuple
from langchain_text_splitters import RecursiveCharacterTextSplitter

from qdrant_client.http import models as qmodels

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.core.async_database import get_async_qdrant_client
from app.services.ai.async_llm_service import (
    generate_embedding_async,
    generate_embeddings_batch_async,
    generate_rag_response_async,
)
from app.services.ai.guardrails_service import guardrails_service

settings = get_settings()
logger = get_logger(__name__)


class AsyncRAGService:
    """Async RAG service for document ingestion and query answering."""

    def __init__(self):
        self.qdrant_client = get_async_qdrant_client()
        # self.mongo_db = get_async_mongo_db() <-- REMOVED
        self.collection_name = settings.EFFECTIVE_QDRANT_COLLECTION
        self.embedding_dimensions = settings.BEDROCK_EMBEDDING_DIMENSIONS

    async def ensure_collection(self):
        """Ensures the Qdrant collection exists with correct dimensions."""
        collections = await self.qdrant_client.get_collections()
        exists = any(c.name == self.collection_name for c in collections.collections)
        if not exists:
            await self.qdrant_client.create_collection(
                collection_name=self.collection_name,
                vectors_config=qmodels.VectorParams(
                    size=self.embedding_dimensions,
                    distance=qmodels.Distance.COSINE,
                ),
            )

    def chunk_text(
        self, text: str, chunk_size: int = 1000, overlap: int = 200
    ) -> List[str]:
        """Chunks text using LangChain's RecursiveCharacterTextSplitter."""
        try:

            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=overlap,
                length_function=len,
                separators=["\n\n", "\n", ". ", " ", ""],
            )
            return splitter.split_text(text)
        except ImportError:
            # Fallback to basic chunking
            chunks = []
            start = 0
            while start < len(text):
                end = start + chunk_size
                chunks.append(text[start:end])
                start += chunk_size - overlap
            return chunks

    async def ingest_document(
        self, tenant_id: str, file_path: str, source: str
    ) -> Dict[str, Any]:
        """Ingest a document asynchronously."""
        # 1. Parse document
        if file_path.endswith(".pdf"):
            from app.services.ai.rag_service import rag_service

            text = rag_service.parse_pdf(file_path)
        elif file_path.endswith(".docx"):
            from app.services.ai.rag_service import rag_service

            text = rag_service.parse_docx(file_path)
        else:
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()

        # 2. Chunk
        chunks = self.chunk_text(text)

        # 3. Delete existing vectors (idempotency)
        await self.delete_document(tenant_id, source)

        # 4. Generate embeddings in batch (async + concurrent)
        logger.info(f"Generating {len(chunks)} embeddings for {source}")
        embeddings = await generate_embeddings_batch_async(chunks)

        # 5. Create points and upsert
        points = []
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            point_id = str(uuid.uuid4())
            points.append(
                qmodels.PointStruct(
                    id=point_id,
                    vector=embedding,
                    payload={
                        "tenant_id": tenant_id,
                        "source": source,
                        "text": chunk,
                        "chunk_index": i,
                    },
                )
            )

        await self.qdrant_client.upsert(
            collection_name=self.collection_name, points=points
        )

        # 6. Store metadata using DocumentService (supports DynamoDB)
        # Use run_in_executor because DocumentService is synchronous
        import asyncio
        from app.services import document_service

        loop = asyncio.get_running_loop()
        
        # We need to determine if we update or create. The worker calls this after SQS.
        # Ideally, metadata is created when upload happens.
        
        # Check if doc exists (run sync method in thread)
        existing_doc = await loop.run_in_executor(
            None, 
            lambda: document_service.get_document_by_filename(tenant_id, source)
        )

        if existing_doc:
            await loop.run_in_executor(
                None,
                lambda: document_service.update_document_status(
                    document_id=existing_doc["document_id"],
                    status="indexed",
                    chunk_count=len(chunks),
                    point_ids=[p.id for p in points]
                )
            )
        else:
            # Create new if not found (fallback)
            # BUG FIX: Improved document type detection to handle txt and other formats
            doc_type = "txt"  # Default
            if source.endswith(".pdf"):
                doc_type = "pdf"
            elif source.endswith(".docx"):
                doc_type = "docx"
            
            await loop.run_in_executor(
                None,
                lambda: document_service.create_document(
                    tenant_id=tenant_id,
                    filename=source,
                    document_type=doc_type,
                    source="reindex", 
                    extra_info={"chunk_count": len(chunks)}
                )
            )
            
            # Re-fetch to get ID and update status
            new_doc = await loop.run_in_executor(
                None, 
                lambda: document_service.get_document_by_filename(tenant_id, source)
            )
            
            if new_doc:
                logger.info(f"New document created with ID: {new_doc.get('document_id')}. Updating status...")
                await loop.run_in_executor(
                    None,
                    lambda: document_service.update_document_status(
                        document_id=new_doc["document_id"],
                        status="indexed",
                        chunk_count=len(chunks), 
                        point_ids=[p.id for p in points]
                    )
                )
                logger.info(f"Status updated for {new_doc.get('document_id')}")
            else:
                logger.error("Failed to retrieve newly created document metadata.")

        return {"source": source, "chunks": len(chunks), "status": "indexed"}

    async def delete_document(self, tenant_id: str, source: str):
        """Deletes all vectors for a specific document source."""
        try:
            await self.qdrant_client.delete(
                collection_name=self.collection_name,
                points_selector=qmodels.FilterSelector(
                    filter=qmodels.Filter(
                        must=[
                            qmodels.FieldCondition(
                                key="tenant_id",
                                match=qmodels.MatchValue(value=tenant_id),
                            ),
                            qmodels.FieldCondition(
                                key="source",
                                match=qmodels.MatchValue(value=source),
                            ),
                        ]
                    )
                ),
            )
        except Exception as e:
            logger.warning(f"Failed to delete document vectors: {e}")

    async def search(
        self, tenant_id: str, query: str, top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """Search for relevant documents."""
        query_embedding = await generate_embedding_async(query)

        results = await self.qdrant_client.query_points(
            collection_name=self.collection_name,
            query=query_embedding,
            query_filter=qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="tenant_id",
                        match=qmodels.MatchValue(value=tenant_id),
                    )
                ]
            ),
            limit=top_k,
        )

        return [
            {
                "text": hit.payload.get("text", ""),
                "source": hit.payload.get("source", ""),
                "score": hit.score,
            }
            for hit in results.points
        ]

    async def answer_query(
        self,
        tenant_id: str,
        query: str,
        conversation_history: Optional[List[dict]] = None,
        tenant_config: Optional[dict] = None,
        missing_fields: Optional[List[str]] = None,
    ) -> Tuple[str, List[str]]:
        """Answer a query using RAG with guardrails."""

        # 1. Input validation with guardrails
        # BUG FIX: validate_input doesn't exist, should use check_input
        allow_pii = bool(missing_fields)  # Allow PII during lead capture
        custom_guardrails_list = tenant_config.get("guardrails", []) if tenant_config else []
        
        result = guardrails_service.check_input(query, custom_guardrails_list, allow_pii)
        if not result.is_safe:
            rejection_msg = guardrails_service.get_refusal_message(result.category)
            return rejection_msg, []

        # 2. Search for relevant context
        search_results = await self.search(tenant_id, query, top_k=5)

        context = "\n\n".join([r["text"] for r in search_results])
        sources = list(set([r["source"] for r in search_results]))

        # 3. Build system prompt with guardrails
        # BUG FIX: build_system_prompt doesn't accept tenant_config or missing_fields
        # It expects: tone, language, custom_guardrails, company_context
        tone = tenant_config.get("tone", "professional") if tenant_config else "professional"
        language = tenant_config.get("language", "en") if tenant_config else "en"
        company_context = tenant_config.get("company_context", "") if tenant_config else ""
        
        system_prompt = guardrails_service.build_system_prompt(
            tone=tone,
            language=language,
            custom_guardrails=custom_guardrails_list,
            company_context=company_context
        )

        # 4. Generate response (async)
        answer = await generate_rag_response_async(
            query=query,
            context=context,
            conversation_history=conversation_history or [],
            system_prompt=system_prompt,
        )

        # 5. Output validation
        # BUG FIX: validate_output doesn't exist, should use check_output
        # Also, check_output returns GuardrailResult, not (is_safe, fallback)
        output_result = guardrails_service.check_output(answer, query)
        if not output_result.is_safe:
            # Use a safe fallback message
            answer = "I apologize, but I'm not able to help with that request. Is there something else about our products or services I can assist you with?"

        return answer, sources


# Singleton instance
async_rag_service = AsyncRAGService()
