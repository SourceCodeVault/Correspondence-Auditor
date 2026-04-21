# // version: 7.0 (Added DeepSeek Reasoning & Model Extra Support)
# // path: shared/llm_utils.py

import os
from dotenv import load_dotenv
from pathlib import Path

# --- Setup ---
project_root = os.path.join(os.path.dirname(__file__), '..')
load_dotenv(os.path.join(project_root, '.env'))

import yaml
import json
import time 
import re 

from shared.ui_utils import print_warning, print_failure, print_info
from shared.api_clients.ollama_client import call_ollama_llm
from shared.api_clients.openrouter_client import call_openrouter_llm
from shared.api_clients.cerebras_client import call_cerebras_llm
from shared.string_utils import extract_json_from_string

import ollama
from jinja2 import Template
from datetime import datetime
from shared.logging_utils import log_llm_call, log_json_extraction_failure, save_raw_trace 



# --- Model & API Configuration ---

# Intelligent Context Window Configuration
CONTEXT_MULTIPLIER = 2.0  # Safety buffer for the output
MIN_CONTEXT_WINDOW = 4096 # A sensible minimum
MODEL_MAX_CONTEXTS = {
    "local-llama-guard-3": 131072,
    "local-openai-gpt-oss": 131072,
    "local-microsoft-phi-4": 16384,
    "default": 8192 # Fallback for unknown models
}

# Load cost mapping once at startup
_COST_MAPPING = {}
try:
    cost_map_path = os.path.join(project_root, 'config', 'cost_mapping.yaml.txt')
    with open(cost_map_path, 'r') as f:
        _COST_MAPPING = yaml.safe_load(f)
except FileNotFoundError:
    print_warning("config/cost_mapping.yaml.txt not found. Cost calculation will be disabled.", level=1)
except yaml.YAMLError as e:
    print_warning(f"Error parsing config/cost_mapping.yaml.txt: {e}. Cost calculation disabled.", level=1)

# -----------
def _normalize_response(raw_response: dict | str, execution_target: str) -> dict:
    """
    Standardizes response. Now supports Qwen <think> tag extraction.
    """
    if raw_response is None:
        return {'reasoning': None, 'final_answer': None, 'provider_specific': None}

    reasoning = None
    final_answer = None
    provider_specific = {}

    if execution_target == 'local_ollama':
        if isinstance(raw_response, dict) and 'has_reasoning' in raw_response:
            reasoning = raw_response.get('thinking')
            final_answer = raw_response.get('final_answer')
            provider_specific = raw_response
        else:
            final_answer = raw_response
            provider_specific = {'raw': raw_response}
            
    elif execution_target in ['remote_openrouter', 'remote_cerebras']:
        # 1. Check for standard/DeepSeek reasoning fields
        reasoning = getattr(raw_response, 'reasoning', None)
        if not reasoning and hasattr(raw_response, 'model_extra'):
            extras = raw_response.model_extra or {}
            reasoning = extras.get('reasoning') or extras.get('reasoning_details')

        final_answer = getattr(raw_response, 'content', "")
        if final_answer is None: final_answer = ""
        
        provider_specific = {'full_message_object': str(raw_response)}

    else:
        final_answer = str(raw_response)
        provider_specific = {'raw': raw_response}

    # --- UNIVERSAL <THINK> TAG EXTRACTION (For Qwen/DeepSeek via Content) ---
    # ALWAYS look for explicit <think> tags in the text, regardless of metadata
    if isinstance(final_answer, str) and "<think>" in final_answer:
        # Regex to extract content between <think> and </think>
        think_match = re.search(r"<think>(.*?)</think>", final_answer, re.DOTALL)
        if think_match:
            explicit_thoughts = think_match.group(1).strip()
            
            # If the API missed the native reasoning, use the explicit thoughts
            if not reasoning:
                reasoning = explicit_thoughts
            # If both exist, combine them so we don't lose telemetry
            else:
                reasoning += f"\n\n--- EXPLICIT PROMPT THINKING ---\n{explicit_thoughts}"
                
            # ALWAYS remove the thinking tags from the final answer
            final_answer = re.sub(r"<think>.*?</think>", "", final_answer, flags=re.DOTALL).strip()

    return {
        'reasoning': reasoning,
        'final_answer': final_answer,
        'provider_specific': provider_specific
    }

# --- Primary Entry Point ---

def load_and_run_prompt(manifest_path: str, execution_target: str, calling_tool_name: str, run_id: str, template_vars: dict = None, param_overrides: dict | None = None, keep_alive: str | None = "2m", ollama_client_instance: ollama.Client | None = None, skip_normalization: bool = False, session_folder: Path | None = None) -> tuple[dict | str | None, dict]:
    if template_vars is None:
        template_vars = {}
    
    debug_enabled = os.getenv('DEBUG_CONVERSATIONS', 'false').lower() == 'true'
    
    try:
        with open(manifest_path, 'r') as f:
            if manifest_path.endswith('.json'):
                manifest = json.load(f)
            else: # Assume YAML for .yaml, .txt, etc.
                manifest = yaml.safe_load(f)
    except (FileNotFoundError, yaml.YAMLError, json.JSONDecodeError) as e:
        print_failure(f"Failed to load or parse manifest file: {manifest_path}. Error: {e}")
        return None, {}
    
    params = manifest.get('parameters', {})
    prompt_def = manifest.get('prompt', {})
    
    # Robustly find the model configuration and provide clear errors
    model_config = None
    config_key_used = None
    if 'model_config' in manifest:
        model_config = manifest['model_config']
        config_key_used = 'model_config'
    elif 'models' in manifest:
        model_config = manifest['models']
        config_key_used = 'models'
    
    if not model_config:
        print_failure(f"Manifest is missing a 'model_config' (for YAML) or 'models' (for JSON) key: {manifest_path}")
        return None, {}

    model_id = model_config.get(execution_target)
    
    if not model_id:
        print_failure(f"Execution target '{execution_target}' not found in the manifest's '{config_key_used}' section.")
        print_warning(f"  Available targets in this manifest are: {list(model_config.keys())}")
        return None, {}
    
    # === [ STATIC ROUTING PERMISSION CHECK ] ===
    model_entry = _COST_MAPPING.get(model_id, _COST_MAPPING.get('default', {}))
    is_allowed = model_entry.get('allow_routing', False) 

    # Retrieve Trade Secret Name
    display_name = model_entry.get('display_name', model_id) 

    if not is_allowed:
        print_failure(f"❌ ROUTING REJECTION: Model ID '{model_id}' is not explicitly authorized. Add 'allow_routing: true' to its entry in 'config/cost_mapping.yaml.txt' to enable.")
        placeholder_metrics = {'duration_seconds': 0.0, 'input_tokens': 0, 'output_tokens': 0}
        return None, placeholder_metrics
    # === [ END STATIC ROUTING PERMISSION CHECK ] ===
    
    if param_overrides:
        params.update(param_overrides)
    
    # Inject Display Name into Params so clients can use it for logging
    params['display_name'] = display_name

    messages = []
    
    # Check for the JSON 'messages_template' format first
    if 'messages_template' in manifest:
        message_template_list = manifest.get('messages_template', [])
        for message_template in message_template_list:
            role = message_template.get('role')
            content_jinja = message_template.get('content')
            
            if not role or not content_jinja:
                print_warning(f"Skipping malformed message in manifest: {message_template}")
                continue
            
            template = Template(content_jinja)
            rendered_content = template.render(template_vars)
            messages.append({"role": role, "content": rendered_content})

    # FALLBACK: Check for the old YAML 'prompt:' format
    elif 'prompt' in manifest:
        print_warning(f"Using legacy 'prompt:' key from manifest. Please upgrade to 'messages_template' list.", level=4)
        prompt_def = manifest.get('prompt', {})
        if prompt_def.get('system'):
            system_template = Template(prompt_def['system'])
            messages.append({"role": "system", "content": system_template.render(template_vars)})
        if prompt_def.get('user'):
            user_template = Template(prompt_def['user'])
            messages.append({"role": "user", "content": user_template.render(template_vars)})
    
    if not messages:
        print_failure(f"No valid messages could be constructed from manifest: {manifest_path}")
        return None, {}

    raw_response, metrics = None, {}

    # --- START UNIVERSAL RETRY LOGIC (Exponential Backoff) ---
    MAX_RETRIES = 5
    initial_delay = 1.0 
    
    for attempt in range(MAX_RETRIES):
        raw_response, metrics = None, {}
        start_time = time.monotonic()
        
        # --- Dispatcher ---
        try:
            if execution_target == 'local_ollama':
                client_to_use = ollama_client_instance if ollama_client_instance else ollama.Client()
                raw_response, metrics = call_ollama_llm(client_to_use, messages, model_id, params, keep_alive)

            elif execution_target == 'remote_openrouter':
                raw_response, metrics = call_openrouter_llm(messages, model_id, params, _COST_MAPPING)
            
            elif execution_target == 'remote_cerebras':
                raw_response, metrics = call_cerebras_llm(messages, model_id, params, _COST_MAPPING)

            else:
                print_failure(f"Unknown execution_target '{execution_target}' in load_and_run_prompt.")
                return None, {}

            # Check for immediate success
            if raw_response is not None:
                break 

        except Exception as e:
            error_msg = str(e)
            # Hard-fail immediately on missing API keys, auth errors, or missing models
            if "OPENROUTER_API_KEY" in error_msg or "api_key" in error_msg.lower() or "401" in error_msg or "fatal" in error_msg.lower():
                print_failure(f"Unrecoverable Error: {error_msg}", level=1)
                break 
                
            print_warning(f"Attempt {attempt + 1}/{MAX_RETRIES} failed with exception for {execution_target}: {e}")

        # Retry Delay
        if attempt < MAX_RETRIES - 1:
            delay = initial_delay * (2 ** attempt)
            print_info(f"   Waiting {delay:.2f}s before retrying call to {execution_target}...", level=3)
            time.sleep(delay)
        else:
            print_failure(f"FATAL: LLM call to {execution_target} failed after {MAX_RETRIES} attempts.", level=1)

    # --- END UNIVERSAL RETRY LOGIC ---

    end_time = time.monotonic()
    duration_seconds = end_time - start_time

    if skip_normalization:
        print_warning("Skipping normalization. Returning raw provider response.", level=4)
        return raw_response, metrics


    # --- NORMALIZATION ---
    # Standardize the response from any provider into a single format
    std_response = _normalize_response(raw_response, execution_target)

    # --- NEW: METADATA EXTRACTION ---
    # 1. Provider ID (e.g. OpenRouter gen-id)
    provider_id = "N/A"
    if execution_target in ['remote_openrouter', 'remote_cerebras'] and raw_response:
        provider_id = getattr(raw_response, 'id', 'N/A')
    
    # 2. Real Model ID (The specific version used)
    real_model = display_name # Default to the config name
    if hasattr(raw_response, 'model'):
        real_model = raw_response.model # Capture actual model reported by API

    # 3. Alias (The config key, e.g. "FUDO")
    # We can assume 'display_name' passed in params is the Alias/Display Name
    model_alias = params.get('display_name', 'Unknown')

    # --- SCIENTIFIC PROVENANCE LOGGING ---
    trace_content = std_response.get('final_answer', '') or ""
    trace_thinking = std_response.get('reasoning', '')
    
    if trace_content or trace_thinking:
        save_raw_trace(
            run_id=run_id, 
            model_id=real_model, 
            raw_content=str(trace_content), 
            thinking_content=str(trace_thinking),
            prompt_messages=messages,
            # --- PASS NEW METADATA ---
            provider_id=provider_id,
            alias=model_alias,
            params=params,
            execution_target=execution_target
        )
    # ------------------------------------------

    
    # Check for empty content (Fail if both content and reasoning are missing)
    if std_response.get('final_answer') is None and std_response.get('reasoning') is None:
        print_failure(f"Provider {execution_target} returned no content (final result after retries).", level=3)
        metrics['duration_seconds'] = duration_seconds
        return None, metrics


    original_content_for_logging = std_response.get('final_answer')
    json_extraction_attempts = []


    # --- AUTOMATIC JSON EXTRACTION ---
    if std_response.get('final_answer') is not None and params.get('is_json', False):
        text_to_parse = std_response['final_answer']

        if isinstance(text_to_parse, str):
            extracted_data = extract_json_from_string(text_to_parse, 
                session_folder=session_folder,
                run_id=run_id)

            if debug_enabled:
                json_extraction_attempts.append({"strategy": "extract_json_from_string", "success": extracted_data is not None})

            if extracted_data:
                try:
                    # Check if extractor already parsed it
                    if isinstance(extracted_data, (dict, list)):
                        std_response['final_answer'] = extracted_data
                    else: # It's a string, so we parse it.
                        std_response['final_answer'] = json.loads(extracted_data)
                    print_info(f"JSON response parsed successfully.", level=4)
                except json.JSONDecodeError as e:
                    print_warning(f"I_J was true and response was found, but decoding failed: {e}", level=3)
                    log_json_extraction_failure(run_id, calling_tool_name, model_id, text_to_parse, json_extraction_attempts)
            else:
                log_json_extraction_failure(run_id, calling_tool_name, model_id, text_to_parse, json_extraction_attempts)

        elif isinstance(text_to_parse, (dict, list)):
            print_info(f"JSON response parsed successfully.", level=4)

    # --- Centralized Logging ---
    if metrics:
        cost_save_usd = 0.0
        provider = "unknown"
        final_params = params.copy()

        if execution_target.startswith('local_'):
            provider = execution_target.replace('local_', '')
            commercial_model_id = manifest.get('model_config', {}).get('remote_openrouter') or manifest.get('model_config', {}).get('remote_cerebras')
            if commercial_model_id and commercial_model_id in _COST_MAPPING:
                costs = _COST_MAPPING[commercial_model_id]
                input_cost = (metrics.get('input_tokens', 0) / 1_000_000) * costs['input_cost_per_mtok']
                output_cost = (metrics.get('output_tokens', 0) / 1_000_000) * costs['output_cost_per_mtok']
                cost_save_usd = input_cost + output_cost
        elif execution_target.startswith('remote_'):
            provider = execution_target.replace('remote_', '')
            # Append the tier if it's Cerebras!
            if execution_target == 'remote_cerebras' and 'tier_used' in metrics:
                provider = f"{provider}_{metrics['tier_used'].lower()}"

        metrics['duration_seconds'] = duration_seconds
        metrics['cost_save_usd'] = cost_save_usd

        log_data = {
            "run_id": run_id,
            "timestamp_utc": datetime.utcnow().isoformat(),
            "calling_tool": calling_tool_name,
            "prompt_manifest": os.path.basename(manifest_path),
            "model_id": display_name,
            "provider": provider,
            "input_tokens": metrics.get('input_tokens', 0),
            "output_tokens": metrics.get('output_tokens', 0),
            "tokens_per_second": round(metrics.get('tokens_per_second', 0), 2),
            "duration_seconds": round(metrics.get('duration_seconds', 0), 2),
            "actual_cost_usd": round(metrics.get('actual_cost_usd', 0.0), 6),
            "cost_save_usd": round(metrics.get('cost_save_usd', 0.0), 6),
            "parameters_used": json.dumps(final_params)
        }
        
        if debug_enabled:
            log_data.update({
                "template_vars": template_vars or {},
                "rendered_prompt_messages": messages,
                "full_response_text": str(original_content_for_logging), 
                "std_response": std_response,
                "json_extraction_attempts": json_extraction_attempts
            })
        
        log_llm_call(log_data)
    
    return std_response, metrics