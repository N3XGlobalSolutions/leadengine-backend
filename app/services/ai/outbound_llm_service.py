"""AWS Bedrock LLM service dedicated to Outbound AI Agent reasoning using GLM-5."""

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import get_settings
from app.core.logging_config import get_logger

settings = get_settings()
logger = get_logger(__name__)


class OutboundLLMService:
    """Independent LLM service using zai.glm-5 on AWS Bedrock."""

    def __init__(self):
        self.region = settings.AWS_REGION
        self.llm_model_id = getattr(settings, "BEDROCK_LLM_MODEL_ID", "zai.glm-5")
        self.embedding_model_id = getattr(settings, "BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")
        self.embedding_dimensions = getattr(settings, "BEDROCK_EMBEDDING_DIMENSIONS", 1024)
        self.bearer_token = os.environ.get(
            "AWS_BEARER_TOKEN_BEDROCK",
            getattr(settings, "AWS_BEARER_TOKEN_BEDROCK", None)
        )
        self._client: Optional[boto3.client] = None

    def generate_query_embedding(self, text: str) -> List[float]:
        """Generate Titan V2 embedding vector for queries (1024 dimensions)."""
        return self.embed(text)[0]

    def embed(self, text: str) -> Tuple[List[float], int]:
        """Return (embedding vector, input token count) for a query."""
        try:
            request_body = json.dumps({
                "inputText": text,
                "dimensions": self.embedding_dimensions,
                "normalize": True
            })

            response = self.client.invoke_model(
                modelId=self.embedding_model_id,
                body=request_body,
                contentType="application/json",
                accept="application/json"
            )

            response_body = json.loads(response["body"].read())
            tokens = int(response_body.get("inputTextTokenCount") or 0)
            return response_body["embedding"], tokens
        except Exception as e:
            logger.error(f"[OutboundLLM] Embedding generation failed: {e}")
            raise e

    @property
    def client(self) -> boto3.client:
        """Lazy initialization of Bedrock runtime client."""
        if self._client is None:
            session = boto3.Session()
            config = Config(
                region_name=self.region,
                retries={
                    "max_attempts": 5,
                    "mode": "adaptive"
                },
                connect_timeout=30,
                read_timeout=60
            )

            if self.bearer_token:
                logger.info("[OutboundLLM] Using bearer token auth for Bedrock")
                self._client = session.client(
                    "bedrock-runtime",
                    region_name=self.region,
                    config=config,
                    aws_session_token=self.bearer_token
                )
            else:
                logger.info("[OutboundLLM] Using IAM role credentials for Bedrock")
                self._client = session.client(
                    "bedrock-runtime",
                    region_name=self.region,
                    config=config
                )
        return self._client

    def calculate_cost(self, prompt_tokens: int, completion_tokens: int,
                       cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
        """USD cost of one invocation at AWS Bedrock list prices (see pricing.py)."""
        from app.services.outbound.pricing import llm_cost_usd
        return llm_cost_usd(self.llm_model_id, prompt_tokens, completion_tokens,
                            cache_read_tokens, cache_write_tokens)

    def converse(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        max_retries: int = 3
    ) -> Tuple[Dict[str, Any], float, Dict[str, int], float]:
        """Call Bedrock converse API with retry logic and telemetry tracking.

        Args:
            messages: Conversation message list formatted for Bedrock
            system_prompt: Base system prompt text instructions
            tools: Registered tool schema configurations
            temperature: Prompt reasoning temperature
            max_tokens: Completion response limit
            max_retries: Number of exponential backoff retry cycles

        Returns:
            Tuple of:
            - output_message: Bedrock response message dict
            - latency: Time taken in seconds
            - token_usage: Dict containing prompt, completion, and total tokens
            - cost: Estimated USD cost of the invocation
        """
        # Build Converse parameters
        params: Dict[str, Any] = {
            "modelId": self.llm_model_id,
            "messages": messages,
            "inferenceConfig": {
                "maxTokens": max_tokens,
                "temperature": temperature,
            }
        }

        if system_prompt:
            params["system"] = [{"text": system_prompt}]

        if tools:
            params["toolConfig"] = {
                "tools": tools
            }

        start_time = time.time()
        attempt = 1
        backoff_sec = 2

        while attempt <= max_retries:
            try:
                logger.info(f"[OutboundLLM] Invoking {self.llm_model_id} (Attempt {attempt}/{max_retries})")
                response = self.client.converse(**params)
                latency = time.time() - start_time

                # Extract content
                output_message = response["output"]["message"]
                usage = response.get("usage", {})
                prompt_tokens = int(usage.get("inputTokens", 0) or 0)
                completion_tokens = int(usage.get("outputTokens", 0) or 0)
                cache_read = int(usage.get("cacheReadInputTokens", 0) or 0)
                cache_write = int(usage.get("cacheWriteInputTokens", 0) or 0)
                total_tokens = int(usage.get("totalTokens", 0) or 0) or (
                    prompt_tokens + completion_tokens + cache_read + cache_write
                )

                token_usage = {
                    "prompt": prompt_tokens,
                    "completion": completion_tokens,
                    "cache_read": cache_read,
                    "cache_write": cache_write,
                    "total": total_tokens
                }

                cost = self.calculate_cost(prompt_tokens, completion_tokens, cache_read, cache_write)
                
                logger.debug(
                    f"[OutboundLLM] Successfully generated response in {latency:.2f}s. "
                    f"Tokens: {total_tokens} (Cost: ${cost:.5f})"
                )
                return output_message, latency, token_usage, cost

            except ClientError as e:
                error_code = e.response["Error"]["Code"]
                logger.warning(
                    f"[OutboundLLM] ClientError on attempt {attempt}: {error_code} - {e}. "
                    f"Retrying in {backoff_sec}s..."
                )
                if attempt == max_retries:
                    raise e
                time.sleep(backoff_sec)
                attempt += 1
                backoff_sec *= 2

            except Exception as e:
                logger.error(f"[OutboundLLM] Unexpected error during converse: {e}")
                if attempt == max_retries:
                    raise e
                time.sleep(backoff_sec)
                attempt += 1
                backoff_sec *= 2

        raise RuntimeError("[OutboundLLM] Converse failed after maximum retries")


# Singleton instance
outbound_llm_service = OutboundLLMService()
