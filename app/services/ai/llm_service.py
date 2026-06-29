"""AWS Bedrock LLM and Embedding Service.

Uses AWS Bedrock with bearer token authentication for:
- LLM: Meta Llama4 Maverick 17B Instruct model
- Embeddings: Amazon Titan Text Embeddings V2
"""

import json
import os
from typing import Optional

import boto3
from botocore.config import Config

from app.core.config import get_settings
from app.core.logging_config import get_logger

settings = get_settings()
logger = get_logger(__name__)


class BedrockService:
    """Service for interacting with AWS Bedrock models."""

    def __init__(self):
        """Initialize Bedrock client with IAM role or bearer token authentication."""
        self.region = settings.AWS_REGION
        self.llm_model_id = settings.BEDROCK_LLM_MODEL_ID
        self.embedding_model_id = settings.BEDROCK_EMBEDDING_MODEL_ID
        self.embedding_dimensions = settings.BEDROCK_EMBEDDING_DIMENSIONS

        # Get bearer token from environment (optional - for local dev)
        self.bearer_token = os.environ.get(
            "AWS_BEARER_TOKEN_BEDROCK",
            getattr(settings, "AWS_BEARER_TOKEN_BEDROCK", None),
        )

        # Configure boto3 client
        self._client: Optional[boto3.client] = None

    @property
    def client(self):
        """Lazy initialization of Bedrock runtime client.
        
        Uses IAM role credentials when running in Lambda (recommended).
        Falls back to bearer token for local development.
        """
        if self._client is None:
            # Create a session
            session = boto3.Session()

            config = Config(
                region_name=self.region,
                retries={
                    "max_attempts": 10,  # Increased from 3 to handle throttling
                    "mode": "adaptive"  # Uses exponential backoff automatically
                },
                connect_timeout=60,  # Longer timeout for high load
                read_timeout=60
            )

            # Create bedrock-runtime client
            # In Lambda: Uses IAM role credentials automatically
            # Locally: Uses bearer token if provided
            if self.bearer_token:
                # Local dev with bearer token
                logger.info("Using bearer token authentication for Bedrock")
                self._client = session.client(
                    "bedrock-runtime",
                    region_name=self.region,
                    config=config,
                    aws_session_token=self.bearer_token,
                )
            else:
                # Lambda with IAM role (credentials from environment)
                logger.info("Using IAM role credentials for Bedrock")
                self._client = session.client(
                    "bedrock-runtime",
                    region_name=self.region,
                    config=config,
                    # Credentials automatically picked up from Lambda IAM role
                )
        return self._client

    def converse(self, prompt: str, temperature: float = 0.7, max_tokens: int = 4096):
        return self.client.converse(
            modelId=self.llm_model_id,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "text": prompt
                        }
                    ]
                }
            ],
            inferenceConfig={
                "maxTokens": max_tokens,
                "temperature": temperature,
            }
        )
    def generate_embedding(self, text: str) -> list[float]:
        """Generate embedding for document text using Titan Text Embeddings V2.

        Args:
            text: Text to embed (for document storage)

        Returns:
            List of floats representing the embedding vector (1024 dimensions)
        """
        try:
            # Titan Text Embeddings V2 request format
            request_body = json.dumps(
                {
                    "inputText": text,
                    "dimensions": self.embedding_dimensions,
                    "normalize": True,
                }
            )

            response = self.client.invoke_model(
                modelId=self.embedding_model_id,
                body=request_body,
                contentType="application/json",
                accept="application/json",
            )

            response_body = json.loads(response["body"].read())
            embedding = response_body["embedding"]

            logger.debug(
                f"Generated embedding with {len(embedding)} dimensions",
                extra={"extra_data": {"model": self.embedding_model_id}},
            )

            return embedding

        except Exception as e:
            logger.error(
                f"Error generating embedding: {e}",
                extra={"extra_data": {"model": self.embedding_model_id}},
            )
            raise

    def generate_query_embedding(self, text: str) -> list[float]:
        """Generate embedding for query text using Titan Text Embeddings V2.

        For Titan V2, query and document embeddings use the same method.

        Args:
            text: Query text to embed

        Returns:
            List of floats representing the embedding vector (1024 dimensions)
        """
        # Titan V2 uses same embedding for queries and documents
        return self.generate_embedding(text)

    def generate_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embeddings for multiple texts in batch.

        Note: AWS Bedrock doesn't support true batch inference for embeddings,
        but this method provides a consistent interface and can be optimized
        with concurrent requests in the future.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        embeddings = []
        for text in texts:
            try:
                embedding = self.generate_embedding(text)
                embeddings.append(embedding)
            except Exception as e:
                logger.error(f"Failed to generate embedding in batch: {e}")
                # Use zero vector as fallback
                embeddings.append([0.0] * self.embedding_dimensions)

        logger.info(
            f"Generated {len(embeddings)} embeddings in batch",
            extra={"extra_data": {"count": len(texts)}},
        )
        return embeddings

    def generate_response(self, prompt: str) -> str:
        try:
            response = self.client.converse(
                modelId=self.llm_model_id,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "text": prompt
                            }
                        ]
                    }
                ],
                inferenceConfig={
                    "maxTokens": 4096,
                    "temperature": 0.7,
                }
            )

            generated_text = response["output"]["message"]["content"][0]["text"]

            logger.info(
                "Generated LLM response",
                extra={
                    "extra_data": {
                        "model": self.llm_model_id,
                        "prompt_length": len(prompt),
                        "response_length": len(generated_text),
                    }
                },
            )

            return generated_text

        except Exception as e:
            logger.error(
                f"Error generating response: {e}",
                extra={"extra_data": {"model": self.llm_model_id}},
            )
            raise

# Singleton instance - backward compatible with existing code
class LLMService(BedrockService):
    """Alias for BedrockService to maintain backward compatibility."""

    def extract_lead_info(self, text: str, fields: list[str]) -> dict:
        try:
            prompt = f"""
    Extract any of these fields if mentioned.

    Fields:
    {', '.join(fields)}

    User text:
    {text}

    Return ONLY valid JSON.

    Example:

    {{
      "name": "Selvam",
      "phone": "9876543210"
    }}

    Do not return markdown.
    Do not return explanations.
    Do not wrap JSON in code blocks.
    """

            response = self.converse(
                prompt=prompt,
                temperature=0,
                max_tokens=1000
            )

            content = response["output"]["message"]["content"][0]["text"]

            try:
                return json.loads(content)
            except Exception:
                logger.warning(
                    f"Failed to parse extraction JSON: {content}"
                )
                return self._fallback_extract(text, fields)

        except Exception as e:
            logger.error(f"Structured extraction failed: {e}")
            return self._fallback_extract(text, fields)
    def _fallback_extract(self, text: str, fields: list[str]) -> dict:
        """Fallback extraction using basic prompt and JSON parsing."""
        prompt = f"""
        You are an information extraction system.

        Extract ONLY explicitly mentioned fields from the user text.

        Fields to extract:
        {", ".join(fields)}

        Rules:
        - Return ONLY valid JSON
        - No explanation
        - If user says only one word like "selvam", treat it as name
        - If field missing, omit it
        - Never hallucinate values

        User Text:
        {text}

        JSON:
        """
        try:
            response = self.generate_response(prompt)
            # Basic cleanup if model chats
            start = response.find("{")
            end = response.rfind("}") + 1
            if start != -1 and end != -1:
                json_str = response[start:end]
                return json.loads(json_str)
            return {}
        except Exception as e:
            logger.error(f"Failed to extract entities: {e}")
            return {}

    def generate_rag_response(
        self,
        query: str,
        context: str,
        conversation_history: Optional[list[dict]] = None,
        system_prompt: Optional[str] = None,
    ) -> str:
        """Generate a RAG response based on query, context, and history.

        Args:
            query: The user's question
            context: Retrieved context documents
            conversation_history: List of previous message dicts (role, content)
            system_prompt: Optional custom system prompt

        Returns:
            Generated response string
        """
        if system_prompt is None:
            system_prompt = (
                "You're a helpful assistant chatting with someone. "
                "Use the context I give you to answer their question naturally. "
                "If the answer isn't in the context, just let them know honestly."
            )

        messages = []
        if conversation_history:
            messages.extend(conversation_history)

        # Construct the final prompt with context
        final_prompt = f"""
System: {system_prompt}

Context:
{context}

User: {query}
"""
        return self.generate_response(final_prompt)
    def analyze_lead_priority(
        self,
        transcript: list[dict],
        extracted_data: dict,
        lqc: list[dict],
        company_context: Optional[str] = None
    ) -> dict:
        """
        Analyze lead priority using full conversation context and LQC.

        Args:
            transcript: List of message dicts ({role, content})
            extracted_data: JSON data extracted from the conversation
            lqc: List of Lead Qualification Criteria (questions)
            company_context: Optional description of the business

        Returns:
            Dict containing priority, reasoning, objections, next_step
        """
        try:
            # Format inputs
            lqc_text = "\n".join([f"- {q.get('question_text', '')}" for q in lqc])
            
            transcript_text = ""
            for msg in transcript:
                role = "User" if msg.get('role') == 'user' else "AI"
                content = msg.get('content', '')
                transcript_text += f"{role}: {content}\n"

            prompt = f"""
            Act as a Senior Sales Analyst. Analyze the following conversation against the company's qualification criteria.
            
            Context:
            {company_context or 'General Business'}
            
            Lead Qualification Criteria (LQC):
            {lqc_text}
            
            Extracted Data:
            {json.dumps(extracted_data, indent=2)}
            
            Transcript:
            {transcript_text}
            
            Task:
            1. Evaluate the lead's intent, budget, authority, and timeline (BANT) based on the LQC.
            2. Assign a Priority (High, Medium, Low).
               - High: Strong intent, meets key LQC, urgent timeline.
               - Medium: Interested but has blockers or vague timeline.
               - Low: Just browsing, no intent, or explicitly disqualified.
            3. Identify key objections or concerns raised.
            4. Recommend a specific next step.
            
            Return strictly a JSON object with this schema:
            {{
                "lead_priority": "High" | "Medium" | "Low",
                "reasoning_summary": "2-3 sentences explaining the rating...",
                "key_objections": ["objection 1", "objection 2"],
                "recommended_next_step": "Actionable next step..."
            }}
            """
            
            response = self.generate_response(prompt)
            
            # Simple cleanup to extract JSON
            start = response.find("{")
            end = response.rfind("}") + 1
            if start != -1 and end != -1:
                json_str = response[start:end]
                return json.loads(json_str)
            
            logger.warning(f"Failed to find JSON in analysis response: {response[:100]}...")
            return {
                "lead_priority": "Medium",
                "reasoning_summary": "Analysis failed to parse. Review transcript manually.",
                "key_objections": [],
                "recommended_next_step": "Manual review required."
            }
            
        except Exception as e:
            logger.error(f"Error analyzing lead priority: {e}")
            return {
                "lead_priority": "Medium", 
                "reasoning_summary": "Error during AI analysis.",
                "key_objections": [],
                "recommended_next_step": "Contact manually."
            }

# Create singleton instance
llm_service = LLMService()
