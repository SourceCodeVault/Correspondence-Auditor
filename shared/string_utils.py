# In shared/string_utils.py
import re
import json
import uuid
from pathlib import Path
from datetime import datetime
from shared.ui_utils import print_warning, print_info, print_failure

# --- REQUIRED LIBRARY IMPORT ---
from json_repair import repair_json 
# --- END REQUIRED LIBRARY IMPORT ---

def _log_failed_json(
    session_folder: Path, 
    run_id: str, 
    clean_json_str: str, 
    e: Exception,
    context_name: str | None = None
):
    """
    Saves the malformed JSON string and the parse error to a debug file.
    """
    try:
        if not session_folder.exists():
            session_folder.mkdir(parents=True, exist_ok=True)
            
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_run_id = run_id.replace('/', '_').replace(':', '_')
        safe_context_name = f"{context_name.lower().replace(' ', '_').replace('/', '_')}_" if context_name else ""
        pos_info = f"Position: {e.pos}\n" if hasattr(e, 'pos') else ""

        filename = session_folder / f"failed_json__{safe_context_name}{safe_run_id}__{timestamp}.log"
        
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(f"--- JSON PARSE ERROR ---\n")
            if context_name:
                f.write(f"Analysis Context: {context_name}\n")
            f.write(f"Run ID: {run_id}\n")
            f.write(f"Timestamp: {timestamp}\n")
            f.write(f"Error: {e}\n")
            f.write(pos_info)
            f.write(f"--- FAILED STRING (CLEANED/REPAIRED) ---\n")
            f.write(clean_json_str)
            
        print_failure(f"JSON parse failed. Saved debug log to: {filename.name}", level=2)
    except Exception as log_e:
        print_failure(f"Could not write JSON debug log: {log_e}", level=2)

def _extract_candidate_string(text: str) -> str | None:
    """
    Attempts to find JSON-like structures within text.
    """
    # 1. Try to find markdown code blocks first
    markdown_pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
    match = re.search(markdown_pattern, text, re.IGNORECASE)
    if match:
        return match.group(1)

    # 2. Try to find the outermost JSON object {} or array []
    search_text = text.strip()
    
    obj_start = search_text.find('{')
    obj_end = search_text.rfind('}')
    
    arr_start = search_text.find('[')
    arr_end = search_text.rfind(']')

    candidate = None
    
    if obj_start != -1 and obj_end != -1 and obj_end > obj_start:
        if arr_start == -1 or arr_start > obj_start:
            candidate = search_text[obj_start : obj_end + 1]
            
    if arr_start != -1 and arr_end != -1 and arr_end > arr_start:
        if obj_start == -1 or obj_start > arr_start:
            candidate = search_text[arr_start : arr_end + 1]

    return candidate

def extract_json_from_string(
    text: str, 
    session_folder: Path | None = None, 
    run_id: str | None = None,
    context_name: str | None = None 
) -> dict | list | None:
    """
    Robust JSON extraction that addresses:
    1. Header-induced parsing errors (Robust Header Stripping).
    2. Model-induced list wrapping (Automatic List Unwrapping).
    3. Syntax error cleanup.
    """
    if not isinstance(text, str) or not text.strip():
        print_info("Input text is empty or not a string.", level=4)
        return None

    # Fix 1: Robust Header Stripping
    # Removes provenance tags like <provenance>...</provenance> at the start
    text = re.sub(r"^<.*?(\n|$)", "", text.strip()).strip()

    run_id = run_id or f"unknown_run_{uuid.uuid4()}" 

    # --- LEVEL 1: Fast Path (Standard JSON) ---
    try:
        parsed_json = json.loads(text)
        # Check for unwrap on fast path too
        if isinstance(parsed_json, list) and len(parsed_json) == 1 and isinstance(parsed_json[0], dict):
            return parsed_json[0]
        return parsed_json
    except (json.JSONDecodeError, TypeError):
        pass

    # --- LEVEL 2: Extraction Path (Mixed Text / Markdown) ---
    candidate = _extract_candidate_string(text)
    if candidate and candidate != text:
        try:
            parsed_json = json.loads(candidate)
            # Fix 2: Automatic List Unwrapping
            if isinstance(parsed_json, list) and len(parsed_json) == 1 and isinstance(parsed_json[0], dict):
                print_info("JSON extracted successfully. A_L_03..", level=4)
                return parsed_json[0]
            
            print_info("JSON extracted successfully. A_L_04.", level=4)
            return parsed_json
        except json.JSONDecodeError:
            pass

    # --- LEVEL 3: Repair Path (Syntax Fixing) ---
    try:
        print_warning("QA: Upgraded -> Passed", level=3)
        
        target_text = candidate if candidate else text
        repaired_json_str = repair_json(target_text)
        
        if not repaired_json_str or not repaired_json_str.strip():
            raise ValueError("Failed to repair malformed JSON structure.")

        parsed_json = json.loads(repaired_json_str)

        # Fix 2: Automatic List Unwrapping (Heuristic Check)
        if isinstance(parsed_json, list) and len(parsed_json) == 1 and isinstance(parsed_json[0], dict):
            print_info("JSON extracted successfully. A_L_01.", level=4)
            return parsed_json[0]

        print_info("JSON extracted successfully. A_L_02.", level=4)
        return parsed_json
        
    except Exception as e:
        print_failure(f"Failed to repair malformed JSON structure. F_L: {e}", level=3)
        if session_folder and run_id:
            log_str = candidate if candidate else text
            _log_failed_json(session_folder, run_id, log_str, e, context_name=context_name)
        return None