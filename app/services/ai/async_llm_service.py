"""Async LLM Service wrapper using run_in_executor for blocking boto3 calls.

Since aioboto3 has dependency conflicts, we use asyncio.run_in_executor
to run blocking boto3 calls in a thread pool, achieving non-blocking behavior.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import List, Optional

from app.services.ai.llm_service import llm_service
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# Thread pool for blocking I/O operations
_executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="llm_worker")


async def generate_embedding_async(text: str) -> List[float]:
    """Generate embedding asynchronously using thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, llm_service.generate_embedding, text)


async def generate_embeddings_batch_async(texts: List[str]) -> List[List[float]]:
    """Generate multiple embeddings concurrently."""
    tasks = [generate_embedding_async(text) for text in texts]
    return await asyncio.gather(*tasks)


async def generate_response_async(prompt: str) -> str:
    """Generate LLM response asynchronously using thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, llm_service.generate_response, prompt)


async def extract_lead_info_async(text: str, fields: List[str]) -> dict:
    """Extract lead info asynchronously using thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _executor, partial(llm_service.extract_lead_info, text, fields)
    )


async def generate_rag_response_async(
    query: str,
    context: str,
    conversation_history: Optional[List[dict]] = None,
    system_prompt: Optional[str] = None,
) -> str:
    """Generate RAG response asynchronously."""
    loop = asyncio.get_event_loop()

    def _generate():
        return llm_service.generate_rag_response(
            query=query,
            context=context,
            conversation_history=conversation_history,
            system_prompt=system_prompt,
        )

    return await loop.run_in_executor(_executor, _generate)
