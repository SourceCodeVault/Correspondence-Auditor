# // path: shared/api_clients/ollama_client.py
import json
import time
import ollama
from shared.ui_utils import print_failure, print_warning, print_info

def call_ollama_llm(client: ollama.Client, messages: list[dict], model_name: str, params: dict, keep_alive: str) -> tuple[dict | str | None, dict]:
    """
    Handles API calls to a local Ollama server.
    """
    # --- 1. DEFINE SAFE NAME ---
    safe_name = params.get('display_name', model_name)

    # Use safe_name for the user-facing log
    print_info(f"Generating response from model '{safe_name}'...", level=3)

    # Build the options dictionary
    options = {}
    if 'temperature' in params: options['temperature'] = params['temperature']
    if 'top_p' in params: options['top_p'] = params['top_p']
    if 'top_k' in params: options['top_k'] = params['top_k']
    if 'stop' in params: options['stop'] = params['stop']
    if 'seed' in params: options['seed'] = params['seed']
    if 'min_p' in params: options['min_p'] = params['min_p']
    if 'max_tokens' in params: options['num_predict'] = params['max_tokens']
    if 'repetition_penalty' in params: options['repeat_penalty'] = params['repetition_penalty']
    if params.get('effective_num_ctx'):
        options['num_ctx'] = params.get('effective_num_ctx')

    # Logic Check: We still need the REAL name to detect if it is gpt-oss
    is_gpt_oss = 'gpt-oss' in model_name.lower()
    
    request_data = {
        "model": model_name, # <--- MUST USE REAL NAME HERE FOR THE API
        "messages": messages,
        "stream": False,
        "options": options,
        "keep_alive": keep_alive or ("10m" if is_gpt_oss else None)
    }

    # Enable structured thinking for GPT-OSS models
    if is_gpt_oss and not params.get('is_json', False):
        # You can choose to mask "GPT-OSS" here or leave it as a technical term
        print_info(f"Thinking capability enabled for '{safe_name}'.", level=4)
        request_data['think'] = True
        
        if 'num_ctx' not in options:
            options['num_ctx'] = max(8192, params.get('max_tokens', 0) * 2)
            print_info(f"Set context window to {options['num_ctx']} for reasoning", level=4)

    try:
        start_time = time.perf_counter()
        
        response = client.chat(**request_data)
        
        end_time = time.perf_counter()
        
        # --- Metrics Calculation ---
        duration_ns = response.get('total_duration')
        duration_s = (duration_ns / 1_000_000_000) if duration_ns and duration_ns > 0 else (end_time - start_time)
        output_tokens = response.get('eval_count') or 0
        input_tokens = response.get('prompt_eval_count') or 0
        tps = (output_tokens / duration_s) if duration_s > 0 else 0

        metrics = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tokens_per_second": round(tps, 2),
            "actual_cost_usd": 0.00
        }

        # --- Handle structured GPT-OSS response ---
        if request_data.get('think'):
            thinking_content = response.get('message', {}).get('thinking', '')
            final_answer = response.get('message', {}).get('content', '')

            if not final_answer and not thinking_content:
                print_warning(f"Model '{safe_name}' returned an empty response.", level=3)
                return None, {}

            parsed_response = {
                'has_reasoning': bool(thinking_content),
                'thinking': thinking_content,
                'final_answer': final_answer,
                'reasoning_tokens': 0,
                'final_tokens': 0,
            }
            
            print_info(f"Parsed response from '{safe_name}': Found thinking block ({len(thinking_content)} chars)", level=4)
            return parsed_response, metrics
        
        content = response.get('message', {}).get('content', '').strip()
        if not content:
            print_warning(f"Model '{safe_name}' returned an empty response string.", level=3)
            return None, {}

        return content, metrics

    except ollama.ResponseError as e:
        print_failure(f"API call to Ollama failed: {e.error}", level=1)
        if "not found" in e.error:
            # We print model_name here so the dev knows which actual file is missing
            print_warning(f"Have you run `ollama pull {model_name}`?", level=2)
            raise ValueError(f"Fatal: Ollama model not found: {e.error}")
        return None, {}
    except json.JSONDecodeError as e:
        print_failure(f"Failed to parse JSON response: {e}", level=1)
        return None, {}
    except Exception as e:
        print_failure(f"An unexpected error occurred with the Ollama client: {e}", level=1)
        return None, {}