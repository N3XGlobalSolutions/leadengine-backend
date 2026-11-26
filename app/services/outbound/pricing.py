"""Token pricing and cost conversion for the WhatsApp outbound AI.

Prices are AWS Bedrock on-demand (standard tier) list prices for Asia Pacific
(Mumbai, ap-south-1), taken from the AWS public price list
(offers/v1.0/aws/AmazonBedrock, publication 2026-11-25):

- GLM 5 (zai.glm-5):          $0.0012 per 1K input tokens, $0.00384 per 1K output tokens
- Titan Text Embeddings V2:   $0.000024 per 1K input tokens

GLM 5 has no cached-token price on Bedrock (prompt caching is not offered for
this model), so cache tokens are tracked but priced at the input rate until a
model with caching is used.

Every value can be overridden with environment variables so prices or the
exchange rate can be updated without a code change.
"""

import os
from typing import Dict

# USD per 1,000 tokens
DEFAULT_PRICES: Dict[str, Dict[str, float]] = {
    "zai.glm-5": {"input": 0.0012, "output": 0.00384, "cache_read": 0.0012, "cache_write": 0.0012},
    "amazon.titan-embed-text-v2:0": {"input": 0.000024},
}

# ECB reference rate for 2026-11-25 (1 USD = 92.24 INR)
DEFAULT_USD_INR_RATE = 92.24
DEFAULT_USD_INR_RATE_DATE = "2026-11-25"
PRICE_SOURCE = "AWS Bedrock on-demand list price, ap-south-1 (Mumbai)"


def _env_float(name: str, default: float) -> float:
    try:
        value = os.environ.get(name)
        return float(value) if value not in (None, "") else default
    except ValueError:
        return default


def usd_inr_rate() -> float:
    return _env_float("OUTBOUND_USD_INR_RATE", DEFAULT_USD_INR_RATE)


def usd_inr_rate_date() -> str:
    return os.environ.get("OUTBOUND_USD_INR_RATE_DATE") or DEFAULT_USD_INR_RATE_DATE


def llm_prices(model_id: str) -> Dict[str, float]:
    """USD per 1K tokens for the reply model (env overrides win)."""
    base = DEFAULT_PRICES.get(model_id, DEFAULT_PRICES["zai.glm-5"])
    input_price = _env_float("OUTBOUND_LLM_INPUT_PRICE_PER_1K", base["input"])
    return {
        "input": input_price,
        "output": _env_float("OUTBOUND_LLM_OUTPUT_PRICE_PER_1K", base["output"]),
        "cache_read": _env_float("OUTBOUND_LLM_CACHE_READ_PRICE_PER_1K", base.get("cache_read", input_price)),
        "cache_write": _env_float("OUTBOUND_LLM_CACHE_WRITE_PRICE_PER_1K", base.get("cache_write", input_price)),
    }


def embedding_price(model_id: str) -> float:
    base = DEFAULT_PRICES.get(model_id, DEFAULT_PRICES["amazon.titan-embed-text-v2:0"])
    return _env_float("OUTBOUND_EMBEDDING_PRICE_PER_1K", base["input"])


def llm_cost_usd(model_id: str, input_tokens: int, output_tokens: int,
                 cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    """Cost of one LLM call. Bedrock reports cache tokens separately from inputTokens."""
    p = llm_prices(model_id)
    return (
        input_tokens / 1000.0 * p["input"]
        + output_tokens / 1000.0 * p["output"]
        + cache_read_tokens / 1000.0 * p["cache_read"]
        + cache_write_tokens / 1000.0 * p["cache_write"]
    )


def embedding_cost_usd(model_id: str, tokens: int) -> float:
    return tokens / 1000.0 * embedding_price(model_id)


def to_inr(usd: float) -> float:
    return usd * usd_inr_rate()


def pricing_info(llm_model_id: str, embedding_model_id: str) -> Dict[str, object]:
    """Pricing details shown on the analytics page so every number can be traced."""
    p = llm_prices(llm_model_id)
    rate = usd_inr_rate()
    return {
        "llm_model": llm_model_id,
        "embedding_model": embedding_model_id,
        "usd_per_1k": {
            "input": p["input"],
            "output": p["output"],
            "cache_read": p["cache_read"],
            "cache_write": p["cache_write"],
            "embedding": embedding_price(embedding_model_id),
        },
        "inr_per_1k": {
            "input": p["input"] * rate,
            "output": p["output"] * rate,
            "cache_read": p["cache_read"] * rate,
            "cache_write": p["cache_write"] * rate,
            "embedding": embedding_price(embedding_model_id) * rate,
        },
        "usd_inr_rate": rate,
        "usd_inr_rate_date": usd_inr_rate_date(),
        "source": PRICE_SOURCE,
        "prompt_caching_supported": llm_model_id.startswith(("anthropic.", "amazon.nova", "us.anthropic.", "apac.anthropic.")),
        "note": (
            "GLM 5 on Bedrock has no prompt caching, so cached tokens stay 0. "
            "WhatsApp (Meta) conversation charges are billed by Meta and are not included."
        ),
    }
