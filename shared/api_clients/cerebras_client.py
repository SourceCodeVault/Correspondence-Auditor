"""
cerebras_client.py — UPDATED
Added: rate-limit header extraction baked into the returned metrics dict.

The harvester's RateLimitManager reads these keys from the metrics dict:
  x-ratelimit-limit-requests-day
  x-ratelimit-limit-tokens-minute
  x-ratelimit-remaining-requests-day
  x-ratelimit-remaining-tokens-minute
  x-ratelimit-reset-requests-day
  x-ratelimit-reset-tokens-minute
"""

import os
import time
import threading
from cerebras.cloud.sdk import Cerebras
from cerebras.cloud.sdk import APIConnectionError, RateLimitError
from shared.ui_utils import print_warning, print_info, print_failure

FREE_CLIENT = Cerebras(api_key=os.environ.get("CEREBRAS_API_KEY_FREE", "unconfigured_free_key"))
PAID_CLIENT = Cerebras(api_key=os.environ.get("CEREBRAS_API_KEY_PAID", "unconfigured_paid_key"))
# Per-model cooldown registry: {"llama3.1-8b": 1710000000.0}
_cooldown_lock = threading.Lock()
_free_tier_cooldowns: dict[str, float] = {}

# Rate-limit header names we want to surface
_RATE_LIMIT_HEADERS = (
    'x-ratelimit-limit-requests-day',
    'x-ratelimit-limit-tokens-minute',
    'x-ratelimit-remaining-requests-day',
    'x-ratelimit-remaining-tokens-minute',
    'x-ratelimit-reset-requests-day',
    'x-ratelimit-reset-tokens-minute',
)


def _is_free_tier_cooling_down(model_name: str) -> bool:
    """Returns True if the free tier for this model is currently on cooldown."""
    with _cooldown_lock:
        return time.time() < _free_tier_cooldowns.get(model_name, 0.0)


def _set_free_tier_cooldown(model_name: str, seconds: int = 60):
    """Puts the free tier for this model into cooldown for `seconds` seconds."""
    with _cooldown_lock:
        _free_tier_cooldowns[model_name] = time.time() + seconds


def _extract_rate_limit_headers(completion) -> dict:
    """
    Safely extracts Cerebras rate-limit headers from a completion response object.

    The Cerebras SDK wraps the raw HTTP response. Depending on the SDK version,
    headers may be accessible via:
      - completion.headers          (preferred — SDK >= some version)
      - completion._response.headers
      - completion.__dict__

    We try each approach gracefully and return whatever we find.
    Returns a flat dict with the header values (or empty dict if unavailable).
    """
    headers_obj = None

    # Attempt 1: direct .headers attribute
    if hasattr(completion, 'headers') and completion.headers:
        headers_obj = completion.headers

    # Attempt 2: internal _response attribute
    elif hasattr(completion, '_response') and hasattr(completion._response, 'headers'):
        headers_obj = completion._response.headers

    if not headers_obj:
        return {}

    extracted = {}
    for key in _RATE_LIMIT_HEADERS:
        value = None
        try:
            # Headers objects may support dict-style or attribute-style access
            value = headers_obj.get(key) if hasattr(headers_obj, 'get') else None
            if value is None and hasattr(headers_obj, key.replace('-', '_')):
                value = getattr(headers_obj, key.replace('-', '_'), None)
        except Exception:
            pass
        extracted[key] = value  # None if missing — RateLimitManager handles gracefully

    return extracted


def call_cerebras_llm(
    messages: list[dict],
    model_id: str,
    params: dict,
    cost_mapping: dict,
) -> tuple[dict | str | None, dict]:
    """
    Calls the Cerebras API with free-tier-first routing and cost tracking.

    Returns:
      (message_object | None, metrics_dict)

    metrics_dict keys:
      input_tokens, output_tokens, cached_tokens,
      tokens_per_second, actual_cost_usd, tier_used,
      + all x-ratelimit-* headers (may be None if not returned by API)
    """
    model_config = cost_mapping.get(model_id, {})
    free_config  = model_config.get('free', {})
    paid_config  = model_config.get('paid', {})

    # Strip provider prefix for the actual API call
    clean_model_id = model_id.split('/')[-1] if '/' in model_id else model_id

    strategy    = params.get('cerebras_tier', 'auto').lower()
    temperature = params.get('temperature', 0.2)
    max_tokens  = params.get('max_tokens', 1024)
    top_p       = params.get('top_p', 1.0)

    completion = None
    tier_used  = None
    start_time = time.monotonic()

    # ── Phase 1: Free Tier ───────────────────────────────────────────────────
    if strategy in ('auto', 'free_only'):
        if not free_config.get('allow_routing', False):
            if strategy == 'free_only':
                print_failure(f"[{clean_model_id}] Free tier requested but allow_routing is FALSE.")
                return None, {}
            # auto: fall through to paid
        elif _is_free_tier_cooling_down(clean_model_id):
            if strategy == 'free_only':
                print_failure(f"[{clean_model_id}] Free tier on cooldown. Strategy is free_only — aborting.")
                return None, {}
            # auto: fall through to paid
        else:
            try:
                completion = FREE_CLIENT.chat.completions.create(
                    messages=messages,
                    model=clean_model_id,
                    max_completion_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    stream=False,
                    reasoning_format="parsed",
                )
                tier_used = "FREE"
            except RateLimitError:
                print_warning(f"[{clean_model_id}] FREE tier 429. Setting 60s cooldown.")
                _set_free_tier_cooldown(clean_model_id, 60)
                if strategy == 'free_only':
                    return None, {}
            except Exception as e:
                print_failure(f"[{clean_model_id}] FREE tier error: {e}")
                if "401" in str(e) or "Wrong API Key" in str(e):
                    raise ValueError(f"Fatal: Cerebras Auth Error: {e}")
                if strategy == 'free_only':
                    return None, {}

    # ── Phase 2: Paid Tier (failover or explicit) ────────────────────────────
    if tier_used is None and strategy in ('auto', 'paid_only'):
        if not paid_config.get('allow_routing', False):
            print_failure(f"[{clean_model_id}] Paid tier routing disabled in config.")
            return None, {}

        try:
            start_time = time.monotonic()   # reset: only time the call that succeeded
            completion = PAID_CLIENT.chat.completions.create(
                messages=messages,
                model=clean_model_id,
                max_completion_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                stream=False,
                reasoning_format="parsed",
            )
            tier_used = "PAID"
        except Exception as e:
            print_failure(f"[{clean_model_id}] PAID tier error: {e}")
            if "401" in str(e) or "Wrong API Key" in str(e):
                raise ValueError(f"Fatal: Cerebras Auth Error: {e}")
            return None, {}

    if not completion:
        return None, {}

    # ── Metrics & Cost Calculation ───────────────────────────────────────────
    duration_s = time.monotonic() - start_time

    message_object = completion.choices[0].message
    input_tokens   = completion.usage.prompt_tokens
    output_tokens  = completion.usage.completion_tokens

    # Cached tokens from nested details (may not be present on all SDK versions)
    cached_tokens = 0
    if hasattr(completion.usage, 'prompt_tokens_details') and completion.usage.prompt_tokens_details:
        cached_tokens = getattr(completion.usage.prompt_tokens_details, 'cached_tokens', 0)

    tps = (output_tokens / duration_s) if duration_s > 0 else 0

    active_config = free_config if tier_used == "FREE" else paid_config
    input_cost  = (input_tokens  / 1_000_000) * active_config.get('input_cost_per_mtok',  0.0)
    output_cost = (output_tokens / 1_000_000) * active_config.get('output_cost_per_mtok', 0.0)

    metrics: dict = {
        "input_tokens":    input_tokens,
        "output_tokens":   output_tokens,
        "cached_tokens":   cached_tokens,
        "tokens_per_second": round(tps, 2),
        "actual_cost_usd":   round(input_cost + output_cost, 6),
        "tier_used":       tier_used,
    }

    # ── Rate-limit headers (new) ─────────────────────────────────────────────
    # Merged into the same metrics dict so all callers get them transparently.
    # Values will be None if the SDK version doesn't expose headers — the
    # RateLimitManager in the harvester handles None values gracefully.
    metrics.update(_extract_rate_limit_headers(completion))

    return message_object, metrics