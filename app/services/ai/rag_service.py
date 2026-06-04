import uuid
from typing import Any, Dict, List, Optional
from langchain_text_splitters import RecursiveCharacterTextSplitter

import docx
import PyPDF2
from qdrant_client.http import models as qmodels

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.core.database import get_qdrant_client
from app.models import Tenant, tenant
from app.services.ai.llm_service import llm_service
from app.services.ai.guardrails_service import guardrails_service
from app.services.ai.claude_service import claude_service
from app.utils.localization import get_message, get_language_name


settings = get_settings()


class RAGService:
    def __init__(self):
        self.qdrant_client = get_qdrant_client()
        # self.mongo_db = get_mongo_db()  <-- REMOVED: Managed by DocumentService
        self.collection_name = settings.EFFECTIVE_QDRANT_COLLECTION
        self.embedding_dimensions = settings.BEDROCK_EMBEDDING_DIMENSIONS
        self._ensure_collection()

    def _ensure_collection(self):
        """Ensures the Qdrant collection exists with correct dimensions."""
        collections = self.qdrant_client.get_collections()
        exists = any(c.name == self.collection_name for c in collections.collections)
        if not exists:
            self.qdrant_client.create_collection(
                collection_name=self.collection_name,
                vectors_config=qmodels.VectorParams(
                    size=self.embedding_dimensions,  # Titan V2 = 1024
                    distance=qmodels.Distance.COSINE,
                ),
            )

    def parse_docx(self, file_path: str) -> str:
        """Parses text from a docx file."""
        doc = docx.Document(file_path)
        full_text = []
        for para in doc.paragraphs:
            full_text.append(para.text)
        return "\n".join(full_text)

    def parse_pdf(self, file_path: str) -> str:
        """Parses text from a PDF file with robust error handling."""
        import os
        
        logger = get_logger(__name__)
        
        # Check file exists and has content
        if not os.path.exists(file_path):
            raise ValueError(f"PDF file not found: {file_path}")
        
        file_size = os.path.getsize(file_path)
        if file_size == 0:
            raise ValueError(f"PDF file is empty (0 bytes): {file_path}")
        
        logger.info(f"Parsing PDF: {file_path} (size: {file_size} bytes)")
        
        text = []
        try:
            with open(file_path, "rb") as file:
                # Check PDF magic bytes (should start with %PDF)
                header = file.read(5)
                if not header.startswith(b'%PDF'):
                    raise ValueError(
                        f"Invalid PDF file - does not start with %PDF header. "
                        f"File may be corrupted or not a PDF. First bytes: {header!r}"
                    )
                file.seek(0)  # Reset to start
                
                pdf_reader = PyPDF2.PdfReader(file)
                for page in pdf_reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text.append(page_text)
                        
        except KeyError as e:
            # Handle corrupted PDFs (missing /Root, etc.)
            raise ValueError(
                f"Corrupted PDF file - missing required structure: {e}. "
                f"The file may have been damaged during upload or compression."
            ) from e
        except Exception as e:
            logger.error(f"Failed to parse PDF {file_path}: {e}")
            raise ValueError(f"Failed to parse PDF: {e}") from e
        
        if not text:
            logger.warning(f"No text extracted from PDF: {file_path} - might be image-based")
            
        return "\n".join(text)


    def chunk_text(
        self, text: str, chunk_size: int = 1000, overlap: int = 200
    ) -> List[str]:
        try:
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=overlap,
                length_function=len,
                separators=["\n\n", "\n", ". ", " ", ""],
            )
            chunks = splitter.split_text(text)
            return chunks
        except ImportError:
            logger = get_logger(__name__)
            logger.warning("LangChain not installed, using basic chunking")

            chunks = []
            start = 0
            while start < len(text):
                end = start + chunk_size
                chunk = text[start:end]
                chunks.append(chunk)
                start += chunk_size - overlap
            return chunks

    def delete_document(self, tenant_id: str, source: str):
        """Deletes all vectors for a specific document source within a tenant."""
        logger = get_logger(__name__)

        try:
            self.qdrant_client.delete(
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
            logger.info(f"Deleted existing vectors for {source} (Tenant: {tenant_id})")
        except Exception as e:
            logger.error(f"Failed to delete document vectors: {e}")
            raise

    def ingest_document(
        self, file_path: str, tenant_id: str, source: str, document_type: str = "docx"
    ):
        """Ingests a document into the RAG system. Replaces existing vectors for idempotency."""
        # 1. Parse
        if document_type == "docx" or file_path.endswith(".docx"):
            text = self.parse_docx(file_path)
        elif document_type == "pdf" or file_path.endswith(".pdf"):
            text = self.parse_pdf(file_path)
        else:
            # Plain text
            with open(file_path, "r") as f:
                text = f.read()

        # 2. Chunk
        chunks = self.chunk_text(text)

        # 3. Idempotency: Delete existing vectors for this document before inserting
        self.delete_document(tenant_id, source)

        # 4. Embed and Store - OPTIMIZED with batch embeddings
        logger = get_logger(__name__)
        logger.info(f"Starting ingestion for {source}. Chunks: {len(chunks)}")

        # Generate all embeddings in batch (faster than one-at-a-time)
        logger.info(f"Generating {len(chunks)} embeddings in batch...")
        embeddings = llm_service.generate_embeddings_batch(chunks)

        # Create points with pre-generated embeddings
        points = []
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            if i % 5 == 0:
                logger.info(f"Creating point {i + 1}/{len(chunks)}...")
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

        logger.info(f"Upserting {len(points)} points to Qdrant...")
        
        # Skip upsert if no points to avoid "Empty update request" error
        if points:
            self.qdrant_client.upsert(collection_name=self.collection_name, points=points)
            logger.info(f"Upsert complete for {source}")
        else:
            logger.warning(f"No chunks extracted from {source} - nothing to upsert")


        # 5. Update document metadata using service switcher
        from app.services import document_service
        
        # Extract point IDs for metadata tracking
        point_ids = [p.id for p in points]
        
        # Update or create document metadata
        existing = document_service.get_document_by_filename(tenant_id, source)
        if existing:
            # Update existing document
            document_service.update_document_status(
                document_id=existing["document_id"],
                status="completed",
                chunk_count=len(chunks),
                point_ids=point_ids
            )
            return existing["document_id"]  # Return existing document ID
        else:
            # Create new document metadata
            doc_id = document_service.create_document(
                tenant_id=tenant_id,
                filename=source,
                document_type=document_type,
                source="upload"
            )
            document_service.update_document_status(
                document_id=doc_id,
                status="completed",
                chunk_count=len(chunks),
                point_ids=point_ids
            )
            return doc_id  # Return newly created document ID

    def search(
        self, query: str, tenant_id: str, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Searches for relevant chunks."""
        query_vector = llm_service.generate_query_embedding(query)

        search_result = self.qdrant_client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            query_filter=qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="tenant_id", match=qmodels.MatchValue(value=tenant_id)
                    )
                ]
            ),
            limit=limit,
        )

        results = []
        for hit in search_result.points:
            results.append(
                {
                    "text": hit.payload.get("text", ""),
                    "source": hit.payload.get("source", ""),
                    "score": getattr(hit, "score", 0.0),
                }
            )
        return results

    def _check_for_abuse(self, query: str) -> tuple[bool, str]:
        """Check if the query is attempting to abuse the bot.

        Returns:
            Tuple of (is_abuse: bool, reason: str)
        """
        query_lower = query.lower()

        # Abuse patterns to detect
        abuse_patterns = {
            # Coding requests
            "coding": [
                "write code",
                "write a script",
                "code for",
                "programming",
                "python script",
                "javascript code",
                "code snippet",
                "function that",
                "algorithm for",
                "implement a",
                "debug this",
                "fix this code",
                "html code",
                "css code",
                "sql query",
                "regex for",
            ],
            # Research requests
            "research": [
                "research paper",
                "academic",
                "thesis",
                "dissertation",
                "scientific study",
                "literature review",
                "cite sources",
                "bibliography",
                "peer-reviewed",
            ],
            # Reverse prompting / jailbreaking
            "jailbreak": [
                "ignore previous",
                "ignore your instructions",
                "forget your",
                "pretend you are",
                "act as if",
                "roleplay as",
                "you are now",
                "new persona",
                "ignore all previous",
                "disregard your",
                "override your",
                "bypass your",
                "system prompt",
                "your instructions",
                "what are your rules",
                "show me your prompt",
                "repeat your instructions",
            ],
            # Off-topic / general chat
            "offtopic": [
                "tell me a joke",
                "write a poem",
                "write a story",
                "who is the president",
                "what is the weather",
                "news today",
                "translate this",
                "summarize this article",
            ],
        }

        for category, patterns in abuse_patterns.items():
            for pattern in patterns:
                if pattern in query_lower:
                    return True, category

        return False, ""

    def answer_query(
        self,
        query: str,
        tenant_id: str,
        missing_fields: Optional[List[str]] = None,
        active_step: Optional[Dict[str, Any]] = None,
        all_steps: Optional[List[Dict[str, Any]]] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        tenant_config: Optional[Dict[str, Any]] = None,
        use_claude: bool = False,
    ) -> tuple[str, List[str]]:
        """Generates an answer for the query with full context awareness and modern guardrails.

        Args:
            query: User's question
            tenant_id: Tenant context
            missing_fields: List of lead fields to collect
            active_step: The current qualification question object
            all_steps: List of all qualification questions
            conversation_history: List of previous messages
            tenant_config: Tenant configuration dict
        
        Returns:
            Tuple of (answer: str, sources: List[str])
        """
        # Import guardrails service

        # 0. Extract tenant config settings
        tone = "professional"
        language = "en"
        custom_guardrails = []

        if tenant_config:
            tone = tenant_config.get("tone", "professional")
            language = tenant_config.get("language", "en")
            custom_guardrails = tenant_config.get("guardrails", [])

        # 1. INPUT RAIL: Check user input with modern guardrails
        # Allow PII during lead capture (when missing_fields or active_step is set)
        allow_pii = bool(missing_fields) or bool(active_step)
        input_check = guardrails_service.check_input(
            query=query, custom_guardrails=custom_guardrails, allow_pii=allow_pii
        )

        if not input_check.is_safe:
            # Return appropriate refusal message
            refusal = guardrails_service.get_refusal_message(input_check.category)
            return refusal, []

        # 2. Retrieve relevant context from knowledge base
        context_chunks = self.search(query, tenant_id)
        context_text = "\n\n".join([c["text"] for c in context_chunks])
        sources = list(set([c["source"] for c in context_chunks]))

        # 3. Build conversation history string (last 5 exchanges for context)
        history_text = ""
        if conversation_history:
            recent_history = conversation_history[
                -10:
            ]  # Last 10 messages (5 exchanges)
            history_parts = []
            for msg in recent_history:
                role = "Customer" if msg.get("role") == "user" else "Assistant"
                content = msg.get("content", "")[:500]  # Truncate long messages
                history_parts.append(f"{role}: {content}")
            history_text = "\n".join(history_parts)

        # 4. Build robust system prompt with embedded guardrails
        language_name = get_language_name(language)

        system_prompt = guardrails_service.build_system_prompt(
            tone=tone,
            language=language_name,
            custom_guardrails=custom_guardrails,
            company_context="",
        )

        # 5. Build slot-filling instruction for lead capture
        lead_capture_instruction = ""
        
        if active_step:
            # Simplified qualification prompt - natural language, NO XML
            current_step_num = next(
                (i + 1 for i, s in enumerate(all_steps) if s.get('id') == active_step.get('id')),
                1
            )
            
            total_questions = len(all_steps)
            
            #Build simple steps list (no XML tags!)
            steps_list = ""
            if all_steps:
                steps_list = "\n".join([
                    f"Step {i+1}: {s.get('instruction')}" 
                    for i, s in enumerate(all_steps)
                ])

            # CRITICAL: Simple, clear instructions with NO XML tags
            lead_capture_instruction = f"""
QUALIFICATION TASK (Step {current_step_num} of {total_questions}):

Current Question to Ask: {active_step.get('instruction')}

INSTRUCTIONS:
1. If the customer asked a question, answer it FIRST using the knowledge base
2. After answering, naturally transition to asking: {active_step.get('instruction')}
3. Make it conversational, not like filling out a form
4. End your response with this tag: [STEP:{active_step.get('id')}]

Example response:
"[Answer their question if they asked one]. That's great! To help you better, {active_step.get('instruction')} [STEP:{active_step.get('id')}]"

ALL QUALIFICATION QUESTIONS (for reference):
{steps_list}

DATA COLLECTION COMPLETION:
- If this is the FINAL question (step {total_questions}), and you've collected all data from the conversation, create a summary
- Review the entire conversation history to extract: {', '.join([f for s in all_steps for f in s.get('collects', [s.get('id')])])}
- Format summary as clean JSON with all collected fields
- After showing summary once, just confirm politely (no repeated summaries)

Remember: Answer customer questions FIRST, then ask your qualification question. Keep it natural and friendly."""
        elif missing_fields:
            # Fallback for simple field collection
            field_to_ask = missing_fields[0]
            lead_capture_instruction = f"""
TASK: Collect the customer's {field_to_ask}.

INSTRUCTIONS:
1. If customer asked a question, answer it first
2. Then ask for {field_to_ask}: "To assist you better, could you share your {field_to_ask}?"
3. Keep it brief and natural

Example: "[Answer question]. Thanks! To help you better, could you share your {field_to_ask}?"
"""

        # 6. Build the complete prompt - NATURAL LANGUAGE, NO XML!
        prompt = f"""{system_prompt}

{lead_capture_instruction}

KNOWLEDGE BASE (Use ONLY this information):
{context_text if context_text else "No specific documents available. Stay focused on general product/service questions."}

CONVERSATION HISTORY:
{history_text if history_text else "[This is the first message from the customer]"}

CUSTOMER'S CURRENT MESSAGE: {query}

YOUR TASK:
1. Read the customer's message above
2. If they asked a question, answer it using the knowledge base
3. If in qualification mode, ask the next question naturally
4. Keep your response conversational and helpful

YOUR RESPONSE (ONLY your conversational answer, NO instructions, NO XML tags):
"""

        # 7. Get LLM response using appropriate service
        val_service = claude_service if use_claude else llm_service
        raw_answer = val_service.generate_response(prompt)
        
        # 8. CRITICAL: Filter out any leaked system prompts or XML tags
        answer = self._filter_leaked_prompts(raw_answer)

        # 9. Format sources
        sources = [
            {
                "text": doc["text"][:200],
                "filename": doc.get("filename", "Unknown"),
                "score": doc.get("score", 0.0),
            }
            for doc in context_chunks
        ]

        # 10. OUTPUT RAIL: Validate LLM response
        output_check = guardrails_service.check_output(answer, query)
        if not output_check.is_safe:
            # If output is unsafe, return a safe fallback
            answer = "I apologize, but I'm not able to help with that request. Is there something else about our products or services I can assist you with?"
        elif output_check.sanitized_input:
            # Use sanitized version if provided (e.g., truncated)
            answer = output_check.sanitized_input


        return answer, sources

    def _filter_leaked_prompts(self, response: str) -> str:
        """Remove any accidentally leaked system prompts or XML tags.
        
        This is a critical security filter to prevent the LLM from exposing
        its instructions to users.
        
        Args:
            response: Raw LLM response
            
        Returns:
            Filtered response with no leaked prompts
        """
        import re
        
        # Remove all XML-style tags
        response = re.sub(r'<[^>]+>', '', response)
        response = re.sub(r'</[^>]+>', '', response)
        
        # Remove common instruction block markers
        leaked_patterns = [
            r'CRITICAL INSTRUCTION:.*?\n',
            r'YOUR CORE RESPONSIBILITIES:.*?(?=\n\n|\Z)',
            r'STRICT RULES.*?(?=\n\n|\Z)',
            r'CONVERSATION GUIDELINES:.*?(?=\n\n|\Z)',
            r'QUALIFICATION TASK.*?(?=\n\n|\Z)',
            r'INSTRUCTIONS:.*?(?=\n\n|\Z)',
            r'Remember:.*?Never show these instructions.*?\n',
        ]
        
        for pattern in leaked_patterns:
            response = re.sub(pattern, '', response, flags=re.IGNORECASE | re.DOTALL)
        
        # Remove leaked instruction keywords
        leaked_keywords = [
            "core_instructions", "role_instructions", "tone_instructions",
            "behavioral_rules", "output_format", "qualification_process",
            "current_state", "lead_capture_task", "sales_qualification_mode",
            "security_constraints", "communication_guidelines"
        ]
        
        for keyword in leaked_keywords:
            response = response.replace(keyword, '').replace(keyword.upper(), '')
        
        # Remove multiple newlines left by filtering
        response = re.sub(r'\n{3,}', '\n\n', response)
        
        return response.strip()

    def answer_query_simple(
        self,
        query: str,
        tenant_id: str,
        language: str = "en",
        use_claude: bool = False,
    ) -> tuple[str, List[str]]:
        """
        SIMPLIFIED RAG: Just answer the user's question from knowledge base.
        
        Used by the state machine architecture where qualification is handled
        separately by the state machine, not embedded in the RAG prompt.
        
        Args:
            query: User's question
            tenant_id: Tenant context
            language: Response language (e.g., 'en', 'hi', 'ta')
        
        Returns:
            Tuple of (answer: str, sources: List[str])
        """
        logger = get_logger(__name__)
        
        # Check for abuse first
        is_abuse, abuse_type = self._check_for_abuse(query)
        if is_abuse:
            return guardrails_service.get_refusal_message(
                guardrails_service.GuardrailCategory.OFF_TOPIC
            ), []
        
        # Search knowledge base
        search_results = self.search(query, tenant_id, limit=3)
        sources = list(set(r["source"] for r in search_results))
        
        # Build context from results
        if search_results:
            context = "\n\n".join([r["text"] for r in search_results])
        else:
            context = "No relevant information found in the knowledge base."
        
        # Map language code to name
        language_name = get_language_name(language)

        # Simple, focused prompt - no qualification instructions
        prompt = f"""You're helping a customer with a question. Just chat naturally, like you're texting a friend who asked for help.
Speak ONLY in {language_name}.

Here's what I know from our knowledge base:
{context}

They asked: {query}

Quick tips:
- If the answer's in the context above, share it in a friendly way (2-4 sentences max)
- If it's not there, just say (in {language_name}): "I don't see that info in my knowledge base, but I can get someone from the team to help you with that!"
- Keep it short and sweet - nobody likes reading paragraphs
- Talk like a real person, not a robot
- Respond ONLY in {language_name}

Your response:"""
        
        try:
            val_service = claude_service if use_claude else llm_service
            response = val_service.generate_response(prompt)
            
            # Filter any leaked prompts
            response = self._filter_leaked_prompts(response)
            
            logger.info(f"Simple RAG answer generated for tenant {tenant_id}")

            return response.strip(), sources
            
        except Exception as e:
            logger.error(f"Error generating simple RAG response: {e}")
            return "I'm sorry, I'm having trouble processing your request. Please try again.", []


rag_service = RAGService()
