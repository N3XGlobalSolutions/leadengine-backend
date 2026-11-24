"""AI Sales Executive Reasoning Agent using Bedrock and GLM-5."""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
from app.services.ai.outbound.conversation_pipeline import OutboundAISession
from app.services.ai.outbound.tool_registry import outbound_tool_registry
from app.services.ai.outbound.tool_executor import outbound_tool_executor
from app.services.ai.outbound_llm_service import outbound_llm_service
from app.services.ai.outbound.prompt_builder import outbound_prompt_builder
from app.models.outbound.outbound_entities import OutboundAIMessage
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class OutboundSalesAgent:
    """Outbound Sales Agent managing LLM reasoning loop and tool dispatching."""

    def _map_history_to_bedrock(
        self,
        history: List[OutboundAIMessage],
        user_prompt: str
    ) -> List[Dict[str, Any]]:
        """Map OutboundAIMessage list into clean, strictly valid Bedrock converse message structures.

        Enforces Bedrock Converse API constraints:
        1. Alternating roles: user -> assistant -> user -> assistant ...
        2. Every toolUse in assistant message MUST be immediately followed by a user message containing matching toolResult blocks.
        3. Adjacent messages of the same role are merged into a single message.
        4. The current user prompt is merged into the final user message.
        """
        raw_messages: List[Dict[str, Any]] = []

        pending_tool_use_ids: List[str] = []
        pending_tool_results: List[Dict[str, Any]] = []

        for msg in history:
            role = msg.role

            if role == "system":
                continue

            if role == "assistant":
                if pending_tool_use_ids:
                    missing_ids = set(pending_tool_use_ids) - {r["toolResult"]["toolUseId"] for r in pending_tool_results}
                    for m_id in missing_ids:
                        pending_tool_results.append({
                            "toolResult": {
                                "toolUseId": m_id,
                                "content": [{"json": {"status": "error", "message": "Incomplete execution"}}],
                                "status": "error"
                            }
                        })
                    raw_messages.append({"role": "user", "content": pending_tool_results})
                    pending_tool_use_ids = []
                    pending_tool_results = []

                content = []
                if msg.content:
                    content.append({"text": msg.content})

                tool_ids_in_msg = []
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        t_id = tc.get("tool_use_id") or tc.get("toolUseId")
                        if t_id:
                            content.append({
                                "toolUse": {
                                    "toolUseId": t_id,
                                    "name": tc["name"],
                                    "input": tc.get("input", {})
                                }
                            })
                            tool_ids_in_msg.append(t_id)

                if not content:
                    content.append({"text": "..."})

                raw_messages.append({"role": "assistant", "content": content})
                pending_tool_use_ids = tool_ids_in_msg

            elif role == "tool":
                tool_use_id = (msg.metadata and msg.metadata.get("tool_use_id")) if msg.metadata else None
                if not tool_use_id:
                    try:
                        parsed = json.loads(msg.content)
                        tool_use_id = parsed.get("tool_use_id")
                    except Exception:
                        pass

                if tool_use_id:
                    try:
                        result_json = json.loads(msg.content)
                    except Exception:
                        result_json = {"result": msg.content}

                    tool_res = {
                        "toolResult": {
                            "toolUseId": tool_use_id,
                            "content": [{"json": result_json}],
                            "status": "success" if (msg.metadata and msg.metadata.get("success", True) is not False) else "error"
                        }
                    }

                    pending_tool_results.append(tool_res)
                    if pending_tool_use_ids:
                        collected_ids = {r["toolResult"]["toolUseId"] for r in pending_tool_results}
                        if set(pending_tool_use_ids).issubset(collected_ids):
                            raw_messages.append({"role": "user", "content": pending_tool_results})
                            pending_tool_use_ids = []
                            pending_tool_results = []

            elif role == "user":
                if pending_tool_use_ids:
                    missing_ids = set(pending_tool_use_ids) - {r["toolResult"]["toolUseId"] for r in pending_tool_results}
                    for m_id in missing_ids:
                        pending_tool_results.append({
                            "toolResult": {
                                "toolUseId": m_id,
                                "content": [{"json": {"status": "error", "message": "Incomplete execution"}}],
                                "status": "error"
                            }
                        })
                    raw_messages.append({"role": "user", "content": pending_tool_results})
                    pending_tool_use_ids = []
                    pending_tool_results = []

                text_content = msg.content if msg.content else ""
                raw_messages.append({"role": "user", "content": [{"text": text_content}]})

        if pending_tool_use_ids:
            missing_ids = set(pending_tool_use_ids) - {r["toolResult"]["toolUseId"] for r in pending_tool_results}
            for m_id in missing_ids:
                pending_tool_results.append({
                    "toolResult": {
                        "toolUseId": m_id,
                        "content": [{"json": {"status": "error", "message": "Incomplete execution"}}],
                        "status": "error"
                    }
                })
            raw_messages.append({"role": "user", "content": pending_tool_results})

        # Merge adjacent messages with identical roles
        cleaned_messages: List[Dict[str, Any]] = []
        for m in raw_messages:
            if not cleaned_messages:
                cleaned_messages.append(m)
            else:
                last_msg = cleaned_messages[-1]
                if last_msg["role"] == m["role"]:
                    last_msg["content"].extend(m["content"])
                else:
                    cleaned_messages.append(m)

        # Merge user_prompt into the final user message
        if not cleaned_messages:
            cleaned_messages.append({"role": "user", "content": [{"text": user_prompt}]})
        elif cleaned_messages[-1]["role"] == "user":
            last_content = cleaned_messages[-1]["content"]
            has_prompt = any("text" in block and block["text"] == user_prompt for block in last_content)
            if not has_prompt:
                text_blocks = [b for b in last_content if "text" in b]
                if text_blocks:
                    text_blocks[-1]["text"] = user_prompt
                else:
                    last_content.append({"text": user_prompt})
        else:
            cleaned_messages.append({"role": "user", "content": [{"text": user_prompt}]})

        # Ensure starting message has role "user"
        if cleaned_messages and cleaned_messages[0]["role"] != "user":
            cleaned_messages.insert(0, {"role": "user", "content": [{"text": "Hello"}]})

        return cleaned_messages

    async def execute_reasoning_loop(self, session: OutboundAISession) -> str:
        """Execute GLM-5 converse reasoning loop, handling multiple tool calling hops.

        Returns:
            Final string text response from the agent
        """
        # 1. Build modular system and user prompt segments
        system_prompt, user_prompt = outbound_prompt_builder.build_prompts(session)
        
        # 2. Map existing chat history and current user prompt to Bedrock format
        bedrock_messages = self._map_history_to_bedrock(session.history, user_prompt)

        
        # Get tool specifications registered in tool registry
        tool_specs = outbound_tool_registry.get_bedrock_tool_specs()
        
        # Limit loop to 5 tool-calling hops to prevent infinite loops
        max_hops = 5
        hop = 0
        
        while hop < max_hops:
            hop += 1
            # Call Bedrock Converse
            output_msg, latency, token_usage, cost = outbound_llm_service.converse(
                messages=bedrock_messages,
                system_prompt=system_prompt,
                tools=tool_specs if tool_specs else None,
                temperature=0.7,
                max_tokens=1024
            )
            
            # Aggregate metrics
            session.token_usage["prompt"] += token_usage["prompt"]
            session.token_usage["completion"] += token_usage["completion"]
            session.token_usage["total"] += token_usage["total"]
            session.estimated_cost += cost
            
            # Append output message to Bedrock history (to continue conversation thread)
            bedrock_messages.append(output_msg)
            
            # Extract content parts
            content_parts = output_msg.get("content", [])
            tool_calls = [part["toolUse"] for part in content_parts if "toolUse" in part]
            text_response = "".join(part["text"] for part in content_parts if "text" in part).strip()
            
            if not tool_calls:
                # LLM finished reasoning and returned final text response
                logger.info(f"[SalesAgent] Completed reasoning loop in {hop} hops.")
                return text_response
                
            # Handle tool calls
            logger.info(f"[SalesAgent] Model requested {len(tool_calls)} tool calls (Hop {hop}).")
            
            # Record assistant turn with tool calls internally in session message history
            tool_call_list = []
            for tc in tool_calls:
                tool_call_list.append({
                    "tool_use_id": tc["toolUseId"],
                    "name": tc["name"],
                    "input": tc["input"]
                })
                
            assistant_turn = OutboundAIMessage(
                message_id=str(uuid.uuid4()),
                conversation_id=session.conversation_id,
                role="assistant",
                content=text_response,
                timestamp=datetime.now(timezone.utc),
                tool_calls=tool_call_list,
                prompt_version=session.prompt_version
            )
            session.history.append(assistant_turn)
            
            # Execute each tool and append toolResult blocks
            tool_results_content = []
            for tc in tool_calls:
                t_name = tc["name"]
                t_args = tc["input"]
                t_id = tc["toolUseId"]
                
                # Execute tool using permission manager and executor
                result = await outbound_tool_executor.execute_tool(session, t_name, t_args)
                
                # Append tool result to Bedrock history
                tool_results_content.append({
                    "toolResult": {
                        "toolUseId": t_id,
                        "content": [{"json": result}],
                        "status": "success" if result.get("success", True) is not False else "error"
                    }
                })
                
                # Save tool message turn internally in session history
                tool_turn = OutboundAIMessage(
                    message_id=str(uuid.uuid4()),
                    conversation_id=session.conversation_id,
                    role="tool",
                    content=json.dumps(result),
                    timestamp=datetime.now(timezone.utc),
                    metadata={"tool_use_id": t_id, "success": result.get("success", True) is not False}
                )
                session.history.append(tool_turn)
                
            # Append tool results as a single user message back to Bedrock Converse
            bedrock_messages.append({
                "role": "user",
                "content": tool_results_content
            })
            
        logger.warning("[SalesAgent] Reached maximum tool execution hops. Returning fallback response.")
        return "I'm checking that information right now. Could you please give me a moment?"


# Singleton agent instance
outbound_sales_agent = OutboundSalesAgent()
