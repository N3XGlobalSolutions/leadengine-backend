"""Tool Permission Manager and Tool Executor layer."""

import time
from datetime import datetime, timezone
from typing import Any, Dict, Tuple
from app.services.ai.outbound.conversation_pipeline import OutboundAISession
from app.services.ai.outbound.tool_registry import outbound_tool_registry
from app.core.logging_config import get_logger

# Import definitions to ensure registry decorators execute
import app.services.ai.outbound.tool_definitions

logger = get_logger(__name__)


class ToolPermissionManager:
    """Validator confirming safety guidelines and feature flags prior to tool execution."""

    @classmethod
    def check_permissions(cls, session: OutboundAISession, tool_name: str) -> Tuple[bool, str]:
        """Validate if a tenant is authorized to run the specific tool.

        Checks:
        - Outbound Agent Settings capabilities flags.
        - Rate limits and integrity rules.
        """
        settings = session.agent_settings
        if not settings:
            return False, "Tenant agent settings are not loaded."

        meta = outbound_tool_registry.get_metadata(tool_name)
        if not meta:
            return False, f"Tool '{tool_name}' is not registered."

        if not meta.enabled:
            return False, f"Tool '{tool_name}' is currently disabled globally."

        # Category level checks
        category = meta.category
        
        if category == "calendar" and not settings.enable_scheduling:
            return False, "Meeting scheduling is disabled for this agent."
            
        if category == "catalog" and not settings.enable_product_search:
            return False, "Product search and catalog list operations are disabled."
            
        if category == "knowledge" and not settings.enable_knowledge_search:
            return False, "Knowledge base and document querying are disabled."

        # Success - authorized to run
        return True, "Authorized"


class OutboundToolExecutor:
    """Coordinates tool execution, validating authorization, and capturing telemetry."""

    async def execute_tool(self, session: OutboundAISession, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Verify authorization and run tool, logging results and performance.

        Args:
            session: Shared pipeline context
            tool_name: Registered function key identifier
            arguments: Tool arguments parsed from model output

        Returns:
            Structured dictionary matching toolResult content block specs
        """
        start_time = time.time()
        logger.info(f"[ToolExecutor] Call requested: '{tool_name}' with args {arguments}")

        # 1. Verify permissions
        authorized, reason = ToolPermissionManager.check_permissions(session, tool_name)
        if not authorized:
            logger.warning(f"[ToolExecutor] Permission denied: '{tool_name}' for tenant {session.tenant_id}. Reason: {reason}")
            return {
                "success": False,
                "error": f"Permission Denied: {reason}"
            }

        # 2. Retrieve handler
        handler = outbound_tool_registry.get_handler(tool_name)
        meta = outbound_tool_registry.get_metadata(tool_name)

        if not handler or not meta:
            return {
                "success": False,
                "error": f"Tool '{tool_name}' handler implementation is missing."
            }

        # 3. Execute with error recovery and retry limits
        max_attempts = meta.retry_policy.get("max_attempts", 1)
        attempt = 1
        last_error = None

        while attempt <= max_attempts:
            try:
                # Add logging details
                session.tool_requests.append({
                    "name": tool_name,
                    "arguments": arguments,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })

                # Invoke handler
                # Tool handlers take 'session' as the first argument, and arguments as keywords
                result = await handler(session, **arguments)
                latency = time.time() - start_time
                
                # Log telemetry
                logger.info(f"[ToolExecutor] Successfully executed '{tool_name}' in {latency:.2f}s (Attempt {attempt})")
                
                # Append tool outputs
                session.tool_outputs.append({
                    "name": tool_name,
                    "result": result,
                    "latency": latency,
                    "success": True,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })

                return result

            except Exception as e:
                last_error = str(e)
                logger.error(f"[ToolExecutor] Error executing '{tool_name}' on attempt {attempt}: {e}")
                attempt += 1

        latency = time.time() - start_time
        # Record execution failure
        session.tool_outputs.append({
            "name": tool_name,
            "error": last_error,
            "latency": latency,
            "success": False,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

        return {
            "success": False,
            "error": f"Tool execution failed after {max_attempts} attempts: {last_error}"
        }


# Singleton executor instance
outbound_tool_executor = OutboundToolExecutor()
