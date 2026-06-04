"""Text cleaning utilities for LLM output sanitization.

LLMs (Nova, Claude, Llama) often return text with markdown formatting
(bold, italic, headers, etc.) that should NOT be stored in structured data
like lead names, phone numbers, or answer summaries.

This module provides a centralized `clean_llm_text` function used by
all state machines to strip formatting artifacts before persisting data.
"""

import re
from typing import Optional


def clean_llm_text(text: Optional[str]) -> Optional[str]:
    """Strip markdown and formatting artifacts from LLM-extracted text.

    Handles:
        - Bold: **text** or __text__
        - Italic: *text* or _text_ (single markers)
        - Headers: # text, ## text, etc.
        - Inline code: `text`
        - Quotes: > text
        - Escaped characters: \\* \\_ etc.
        - Leading/trailing whitespace and quotes

    Args:
        text: Raw text from LLM output

    Returns:
        Cleaned text with formatting removed, or None if input is None/empty
    """
    if not text:
        return text

    cleaned = text

    # Remove bold markers: **text** or __text__
    cleaned = re.sub(r'\*\*(.+?)\*\*', r'\1', cleaned)
    cleaned = re.sub(r'__(.+?)__', r'\1', cleaned)

    # Remove italic markers: *text* or _text_ (but not inside words like don_t)
    # Only match when markers are at word boundaries
    cleaned = re.sub(r'(?<!\w)\*(.+?)\*(?!\w)', r'\1', cleaned)
    cleaned = re.sub(r'(?<!\w)_(.+?)_(?!\w)', r'\1', cleaned)

    # Remove inline code: `text`
    cleaned = re.sub(r'`(.+?)`', r'\1', cleaned)

    # Remove markdown headers: # text, ## text, etc.
    cleaned = re.sub(r'^#{1,6}\s+', '', cleaned, flags=re.MULTILINE)

    # Remove blockquote markers: > text
    cleaned = re.sub(r'^>\s?', '', cleaned, flags=re.MULTILINE)

    # Remove escaped markdown characters
    cleaned = re.sub(r'\\([*_`#>~\[\]])', r'\1', cleaned)

    # Remove strikethrough: ~~text~~
    cleaned = re.sub(r'~~(.+?)~~', r'\1', cleaned)

    # Strip surrounding quotes and whitespace
    cleaned = cleaned.strip().strip('"\'')

    # Collapse multiple spaces into one
    cleaned = re.sub(r' {2,}', ' ', cleaned)

    return cleaned.strip() if cleaned.strip() else None
