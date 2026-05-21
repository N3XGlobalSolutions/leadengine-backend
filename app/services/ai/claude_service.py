"""Anthropic Claude LLM Service for S3_MAX tier."""

import json
from typing import Optional

from anthropic import Anthropic

from app.core.config import get_settings
from app.core.logging_config import get_logger

settings = get_settings()
logger = get_logger(__name__)


class ClaudeService:
    """Service for interacting with Anthropic Claude models."""

    def __init__(self):
        """Initialize Anthropic client."""
        self.api_key = settings.CLAUDE_API_KEY
        self.model = "claude-haiku-4-5-20251001"  # Specific version requested
        
        if not self.api_key:
            logger.warning("CLAUDE_API_KEY is not set. S3_MAX Claude calls will fail until configured.")
        
        self._client: Optional[Anthropic] = None

    @property
    def client(self) -> Anthropic:
        """Lazy initialization of Anthropic client."""
        if self._client is None:
            self._client = Anthropic(api_key=self.api_key)
        return self._client

    def generate_response(self, prompt: str) -> str:
        """Generate a response using Claude model via Messages API.

        Args:
            prompt: The full prompt with context and history stringified.

        Returns:
            Generated text response.
        """
        try:
            # We treat the entire provided prompt as the user block 
            # as it already contains the System/Context structure built by the graphs.
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                temperature=0.3,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            # Anthropic returns a list of ContentBlocks
            generated_text = ""
            for block in response.content:
                if block.type == 'text':
                    generated_text += block.text
                    
            logger.info(
                "Generated Claude response",
                extra={
                    "extra_data": {
                        "model": self.model,
                        "prompt_length": len(prompt),
                        "response_length": len(generated_text),
                    }
                },
            )
            return generated_text

        except Exception as e:
            logger.error(
                f"Error generating response from Claude: {e}",
                extra={"extra_data": {"model": self.model}},
            )
            raise


# Create singleton instance
claude_service = ClaudeService()
