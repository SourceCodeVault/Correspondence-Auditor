# // version: 2.9 (Fixed OpenRouter Provider Routing via extra_body)
# // path: shared/api_clients/openrouter_client.py
import os
import json
import re
import time
from openai import OpenAI, APIError
from shared.ui_utils import print_warning, print_failure, print_info
from shared.string_utils import extract_json_from_string

def call_openrouter_llm(messages: list[dict], model_id: str, params: dict, cost_mapping: dict) -> tuple[dict | str | None, dict]:
    """Handles API calls to the OpenRouter service, returning the full message object."""
    
    safe_name = params.get('display_name', model_id) 
    print_info(f"Routing request to '{safe_name}'...", level=3)
    
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is missing from your .env file.")
        
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        timeout=900.0,
    )

    # Standard OpenAI params
    standard_params = ['temperature', 'max_tokens', 'stop', 'seed']
    # Extra params supported by OpenRouter/Qwen (passed via extra_body)
    extra_params_keys = ['top_k', 'top_p', 'repetition_penalty', 'min_p']

    # --- QWEN 3 SPECIFIC LOGIC ---
    if "qwen/qwen3" in model_id.lower():
        if 'temperature' not in params: params['temperature'] = 0.6
        if 'top_p' not in params: params['top_p'] = 0.95
        if 'top_k' not in params: params['top_k'] = 20
        if 'min_p' not in params: params['min_p'] = 0.0
        print_info(f"Applying Config: T={params['temperature']}, P={params['top_p']}, K={params['top_k']}", level=4)

    # --- SCIENTIFIC ROUTING (High Precision Only) ---
    # We strictly filter out Int4/Int8 to prevent "logic degradation".
    # We allow:
    # - fp8, fp16, bf16, fp32: High precision formats
    # - unknown: REQUIRED for Google Vertex / Bedrock which don't report quant status
    provider_preferences = {
        "quantizations": ["fp8", "bf16", "fp16", "fp32", "unknown"],
        "allow_fallbacks": True # Strict mode? True means best value only, False allows more expensive providers
    }
    
    # 1. Build Base Request
    request_params = {
        "model": model_id, 
        "messages": messages,
        # "provider": ...  <-- REMOVED: This causes the crash in OpenAI SDK
    }
    
    for param in standard_params:
        if param in params:
            request_params[param] = params[param]

    # 2. Build extra_body (The Container for OpenRouter Specials)
    extra_body = {}
    
    # Inject Provider Preferences Here
    extra_body['provider'] = provider_preferences  # <--- FIXED LOCATION

    # Add other extra params (top_k, etc)
    for param in extra_params_keys:
        if param in params:
            extra_body[param] = params[param]

    # DeepSeek Reasoning Logic
    if "deepseek" in model_id.lower() and "reasoning" not in extra_body:
        extra_body["reasoning"] = {"enabled": True}
    
    # Attach extra_body to request
    if extra_body:
        request_params['extra_body'] = extra_body

    # JSON Mode Handling
    is_gpt_oss_model = 'gpt-oss' in model_id.lower()
    is_json_requested = params.get('is_json', False)

    if is_json_requested and not is_gpt_oss_model:
        request_params["response_format"] = {"type": "json_object"}
    
    try:
        start_time = time.monotonic()
        completion = client.chat.completions.create(**request_params)
        duration_s = time.monotonic() - start_time

        message_object = completion.choices[0].message

        if not message_object:
            print_warning(f"API call returned empty message.", level=3)
            return None, {}

        # --- METRICS ---
        input_tokens = completion.usage.prompt_tokens
        output_tokens = completion.usage.completion_tokens
        tps = (output_tokens / duration_s) if duration_s > 0 else 0
        
        model_costs = cost_mapping.get(model_id, cost_mapping.get('default', {}))
        input_cost = (input_tokens / 1_000_000) * model_costs.get('input_cost_per_mtok', 0)
        output_cost = (output_tokens / 1_000_000) * model_costs.get('output_cost_per_mtok', 0)
        
        metrics = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tokens_per_second": round(tps, 2),
            "actual_cost_usd": round(input_cost + output_cost, 6)
        }

        return message_object, metrics

    except APIError as e:
        print_failure(f"Routing payload call failed: {e.message}", level=1)
        return None, {}
    except Exception as e:
        print_failure(f"Unexpected error: {e}", level=1)
        return None, {}