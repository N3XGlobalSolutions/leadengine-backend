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
        """Generate a response using LLM model.
        
        Supports both Converse API format (messages) and legacy format (prompt)
        based on model ID.

        Args:
            prompt: The prompt to send to the model

        Returns:
            Generated text response
        """
        try:
            # Determine request format based on model ID
            # Cross-region inference profiles (us.*, eu.*) may use legacy format
            # Llama models often use legacy format with prompt field
            use_legacy_format = (
                self.llm_model_id.startswith("us.") or 
                self.llm_model_id.startswith("eu.") or
                "llama" in self.llm_model_id.lower()
            )
            
            if "titan" in self.llm_model_id:
                 # Amazon Titan format
                request_body = json.dumps(
                    {
                        "inputText": prompt,
                        "textGenerationConfig": {
                            "maxTokenCount": 4096,
                            "stopSequences": [],
                            "temperature": 0.7,
                            "topP": 0.9,
                        },
                    }
                )
            elif "nova" in self.llm_model_id:
                # Amazon Nova format (Converse API style)
                request_body = json.dumps(
                    {
                        "messages": [
                            {
                                "role": "user",
                                "content": [{"text": prompt}],
                            }
                        ],
                        "inferenceConfig": {
                            "max_new_tokens": 1000,  # Reduced - keep responses concise
                            "temperature": 0.3,  # Lowered from 0.7 - much less hallucination
                            "top_p": 0.85,  # Lowered from 0.9 - more focused responses
                            "stopSequences": [  # NEW: Stop if leaking prompts
                                "<",  # Stop before XML tags
                                "CRITICAL INSTRUCTION",
                                "core_instructions",
                                "I am programmed",
                                "My instructions are"
                            ]
                        }
                    }
                )
            elif use_legacy_format:
                # Legacy format for Llama and cross-region profiles
                request_body = json.dumps(
                    {
                        "prompt": prompt,
                        "max_gen_len": 4096,
                        "temperature": 0.7,
                        "top_p": 0.9,
                    }
                )
            else:
                # Converse API format for newer models
                request_body = json.dumps(
                    {
                        "messages": [
                            {
                                "role": "user",
                                "content": prompt,
                            }
                        ],
                        "max_tokens": 4096,
                        "temperature": 0.7,
                        "top_p": 0.9,
                    }
                )

            response = self.client.invoke_model(
                modelId=self.llm_model_id,
                body=request_body,
                contentType="application/json",
                accept="application/json",
            )

            response_body = json.loads(response["body"].read())

            # Extract generated text from response
            # Handle multiple response formats based on model type
            generated_text = None
            
            # Try Titan format
            if "results" in response_body:
                generated_text = response_body["results"][0]["outputText"]

            # Try legacy Llama format first (generation field)
            elif "generation" in response_body:
                generated_text = response_body["generation"]
            
            # Try Converse API format (Llama4, Claude, etc.)
            elif "output" in response_body:
                output = response_body["output"]
                if "message" in output and "content" in output["message"]:
                    # Extract text from content array or string
                    content = output["message"]["content"]
                    if isinstance(content, list) and len(content) > 0:
                        generated_text = content[0].get("text", str(content[0]))
                    elif isinstance(content, str):
                        generated_text = content
            
            # Try OpenAI format
            elif "choices" in response_body:
                generated_text = response_body["choices"][0]["message"]["content"]
            
            # Try simple content field
            elif "content" in response_body:
                generated_text = response_body["content"]
            
            # Fallback: convert entire response to string
            if not generated_text:
                logger.warning(f"Unexpected response format: {response_body}")
                generated_text = str(response_body)

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
        """
        Extract lead information from text using structured LLM output.

        Uses AWS Bedrock's tool calling for guaranteed JSON structure.
        Falls back to basic parsing if tool calling fails.
        """
        try:
            # Define tool schema for structured extraction
            tool_schema = {
                "toolSpec": {
                    "name": "extract_lead_fields",
                    "description": "Extract lead information fields from user text",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                field: {
                                    "type": "string",
                                    "description": f"The {field} of the lead",
                                }
                                for field in fields
                            },
                            "required": [],  # All fields optional
                        }
                    },
                }
            }

            # Create request with tool use
            request_body = json.dumps(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "text": f"Extract any of these fields if mentioned: {', '.join(fields)}. Text: {text}"
                                }
                            ],
                        }
                    ],
                    "toolConfig": {"tools": [tool_schema]},
                    "inferenceConfig": {
                        "maxTokens": 1000,
                        "temperature": 0
                    }
                }
            )

            response = self.client.invoke_model(
                modelId=self.llm_model_id,
                body=request_body,
                contentType="application/json",
                accept="application/json",
            )

            response_body = json.loads(response["body"].read())

            # Extract structured output from tool use
            if "content" in response_body:
                for block in response_body["content"]:
                    if block.get("type") == "toolUse":
                        return block.get("input", {})

            # Fallback: try basic JSON parsing
            logger.warning("Tool calling not available, using fallback parsing")
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
