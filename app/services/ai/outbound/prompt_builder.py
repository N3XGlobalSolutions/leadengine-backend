"""Dynamic Prompt Builder for Outbound AI Sales Agent."""

from typing import Any, Dict, List, Tuple
from app.services.ai.outbound.conversation_pipeline import OutboundAISession


class OutboundPromptBuilder:
    """Assembles prompt layers into a cohesive system instruction set."""

    def build_prompts(self, session: OutboundAISession) -> Tuple[str, str]:
        """Compile modular segments into system and user prompts.

        Returns:
            Tuple: (system_prompt, user_prompt)
        """
        system_parts = []
        
        # 1. System Core Voice and Instructions
        settings = session.agent_settings
        ai_name = settings.ai_name if settings else "Sales Executive"
        tone = settings.sales_tone if settings else "Professional and helpful"
        style = settings.conversation_style if settings else "Informative and natural"
        lang = settings.primary_language if settings else "en"
        audience = settings.target_audience if settings else "general prospects"

        system_parts.append(
            f"You are {ai_name}, an experienced outbound human sales executive. Your goal is to guide prospects "
            f"through the sales lifecycle naturally. Be friendly, professional, and act like a real person over text. "
            f"Tone: {tone}. Style: {style}. Primary Language: {lang}. Target Audience: {audience}.\n"
            f"Rules:\n"
            f"- Speak like a human sales executive. Keep your messages clear, friendly, structured for WhatsApp (use line breaks and simple bullet points).\n"
            f"- Be concise and helpful: provide the key answer directly without writing massive walls of text.\n"
            f"- Do NOT use raw markdown formatting unsupported by WhatsApp (no brackets [], no raw code blocks, use simple bullet points with asterisks * or dashes -).\n"
            f"- If customer asks questions about products or the company, use the search_company_knowledge or search_products tools to answer.\n"
            f"- If customer is interested in scheduling a meeting, check for calendar slots and book using scheduling tools.\n"
            f"- Actively guide the prospect to the next sales stage. Maintain a consultative, pressure-free approach."
        )

        # 2. Business Profile Context
        business_summary = settings.business_summary if settings else ""
        if business_summary:
            system_parts.append(
                f"### Company Overview:\n"
                f"{business_summary}"
            )

        # 3. Campaign Prompt Context
        campaign_info = ""
        # Let's see: we can query the campaign or add template description
        if session.conversation:
            campaign_info = f"Campaign Context: The customer is responding to an outbound WhatsApp message campaign. Respect this initial context."
        if campaign_info:
            system_parts.append(
                f"### Campaign Context:\n"
                f"{campaign_info}"
            )

        # 4. Memory & Stage Details
        memory = session.memory
        if memory:
            memory_summary = (
                f"- Current Conversation Stage: {memory.conversation_stage.value}\n"
                f"- Customer Summary: {memory.conversation_summary or 'No summary yet'}\n"
                f"- Budget details: {memory.budget or 'Not discussed'}\n"
                f"- Interested Products: {', '.join(memory.interested_products) if memory.interested_products else 'None discussed'}\n"
                f"- Meeting booking status: {memory.meeting_status}\n"
                f"- Customer Intent/Objecions: {memory.customer_intent or 'None tracked'}"
            )
            system_parts.append(
                f"### Prospect Sales Profile (Memory):\n"
                f"{memory_summary}"
            )

            # Check if enterprise sales memory exists
            enterprise_data = memory.preferences.get("enterprise")
            if enterprise_data:
                persona = enterprise_data.get("persona", {})
                qual = enterprise_data.get("qualification", {})
                health = enterprise_data.get("health", {})
                active_goal = enterprise_data.get("active_goal", "COLD_OUTREACH")
                objections = enterprise_data.get("detected_objections", [])
                
                enterprise_summary = (
                    f"### Enterprise Sales Strategy Directives:\n"
                    f"- Current Objective Goal: {active_goal}\n"
                    f"- Qualification: {qual.get('qualification_status', 'UNQUALIFIED')} (Lead Score: {qual.get('lead_score', 0)}/100)\n"
                    f"- Decision Maker Probability: {qual.get('decision_maker_probability', 0.0) * 100:.0f}%\n"
                    f"- Objections Detected: {', '.join(objections) if objections else 'None'}\n"
                    f"- Persona Profile: Industry: {persona.get('industry') or 'Unknown'} | Role: {persona.get('role') or 'Unknown'}\n"
                    f"- Health: Risk Score: {health.get('risk_score', 0)}/100 | Momentum: {health.get('momentum_score', 50)}/100"
                )
                system_parts.append(enterprise_summary)

        # 5. Tool Context Guidelines
        system_parts.append(
            "### Tool Call Instructions:\n"
            "- You have tools for searching knowledge, products, and calendar bookings.\n"
            "- Do NOT tell the customer you are 'calling a tool' or 'looking up database'. Simply explain that you are fetching the info or checking availability.\n"
            "- Only answer using facts returned from tools. If you cannot find the answer, politely offer to loop in a human representative."
        )

        # 6. Assemble System Prompt
        system_prompt = "\n\n".join(system_parts)

        # 7. Ground User Prompt (including RAG knowledge retrieved, if any)
        user_prompt_parts = []
        
        # Grounding facts from RAG
        if session.retrieved_knowledge:
            knowledge_blocks = []
            for item in session.retrieved_knowledge:
                text = item.get("text", "")
                source = item.get("source", "Knowledge Base")
                knowledge_blocks.append(f"[Source: {source}]\n{text}")
            
            user_prompt_parts.append(
                "### Grounded Reference Information (RAG Knowledge):\n"
                "Use the following verified facts to answer the user's questions:\n" +
                "\n---\n".join(knowledge_blocks)
            )

        # Grounding details from searched products
        if session.selected_products:
            prod_blocks = []
            for p in session.selected_products:
                features = ", ".join(p.features) if p.features else "None"
                benefits = ", ".join(p.benefits) if p.benefits else "None"
                prod_blocks.append(
                    f"Product: {p.name} (ID: {p.product_id})\n"
                    f"Price: {p.price} {p.currency}\n"
                    f"Category: {p.category}\n"
                    f"Short Description: {p.short_description}\n"
                    f"Features: {features}\n"
                    f"Benefits: {benefits}"
                )
            user_prompt_parts.append(
                "### Grounded Product Catalog Data:\n"
                "Use the following product specifications to answer product questions:\n" +
                "\n---\n".join(prod_blocks)
            )

        # Add current user incoming text
        user_prompt_parts.append(
            f"Customer Message: {session.incoming_text}"
        )

        user_prompt = "\n\n".join(user_prompt_parts)

        return system_prompt, user_prompt


# Singleton instance
outbound_prompt_builder = OutboundPromptBuilder()
