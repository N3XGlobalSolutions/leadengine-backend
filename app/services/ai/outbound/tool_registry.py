"""Self-registering tool registry for Outbound AI Agent."""

from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel


class ToolMetadata(BaseModel):
    name: str
    version: str
    description: str
    schema_version: str
    timeout: float = 10.0
    retry_policy: Dict[str, Any] = {"max_attempts": 3, "backoff": "exponential"}
    permissions: List[str] = ["use_tools"]
    category: str
    enabled: bool = True
    input_schema: Dict[str, Any]


class OutboundToolRegistry:
    """Registry managing tool handlers and Bedrock tool configurations."""

    def __init__(self):
        self._handlers: Dict[str, Callable] = {}
        self._metadata: Dict[str, ToolMetadata] = {}

    def register(
        self,
        name: str,
        description: str,
        input_schema: Dict[str, Any],
        version: str = "1.0.0",
        schema_version: str = "1.0.0",
        timeout: float = 10.0,
        retry_policy: Optional[Dict[str, Any]] = None,
        permissions: Optional[List[str]] = None,
        category: str = "general",
        enabled: bool = True,
    ) -> Callable:
        """Decorator to register a tool handler."""
        def decorator(func: Callable) -> Callable:
            self._handlers[name] = func
            self._metadata[name] = ToolMetadata(
                name=name,
                version=version,
                description=description,
                schema_version=schema_version,
                timeout=timeout,
                retry_policy=retry_policy or {"max_attempts": 3, "backoff": "exponential"},
                permissions=permissions or ["use_tools"],
                category=category,
                enabled=enabled,
                input_schema=input_schema
            )
            return func
        return decorator

    def get_handler(self, name: str) -> Optional[Callable]:
        """Fetch callable execution handler for a tool."""
        return self._handlers.get(name)

    def get_metadata(self, name: str) -> Optional[ToolMetadata]:
        """Fetch registered tool metadata."""
        return self._metadata.get(name)

    def list_tools(self) -> List[ToolMetadata]:
        """List metadata for all registered tools."""
        return list(self._metadata.values())

    def get_bedrock_tool_specs(self) -> List[Dict[str, Any]]:
        """Format all enabled tool schemas for Bedrock converse API toolConfig."""
        specs = []
        for name, meta in self._metadata.items():
            if not meta.enabled:
                continue
            specs.append({
                "toolSpec": {
                    "name": name,
                    "description": meta.description,
                    "inputSchema": {
                        "json": meta.input_schema
                    }
                }
            })
        return specs


# Global registry singleton
outbound_tool_registry = OutboundToolRegistry()
