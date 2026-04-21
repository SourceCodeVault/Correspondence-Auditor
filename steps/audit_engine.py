import json
import re
from pathlib import Path
from shared.llm_utils import load_and_run_prompt
from shared.string_utils import extract_json_from_string

# --- CONFIG ---
MODULE_DIR = Path(__file__).parent
# FIX: Go UP one level to find prompts
PROMPT_DIR = MODULE_DIR.parent / "prompts" 

# --- HELPER: THINKING TRACE EXTRACTOR ---
def normalize_claim(text: str) -> str:
    """Removes non-alphanumeric characters for robust matching."""
    return "".join(c.lower() for c in text if c.isalnum())


def parse_llm_response(response_obj: dict) -> tuple[dict, str]:
    """
    Extracts structured JSON and the raw 'Thinking' trace.
    Supports standard fields, <think> tags, and the new <<ANALYSIS>> format.
    """
    raw_response = response_obj.get('final_answer')
    thinking_trace = (
        response_obj.get('reasoning') or 
        response_obj.get('thoughts') or 
        response_obj.get('reasoning_content')
    )
    
    # Parser Strategy 1: The <<ANALYSIS>> format
    if isinstance(raw_response, str) and "<<ANALYSIS>>" in raw_response:
        # Extract content between markers
        match = re.search(r'<<ANALYSIS>>(.*?)<<END>>', raw_response, re.DOTALL)
        if match:
            thinking_trace = match.group(1).strip()
            # Remove the analysis block to isolate the JSON
            raw_response = re.sub(r'<<ANALYSIS>>.*?<<END>>', '', raw_response, flags=re.DOTALL).strip()

    # Parser Strategy 2: Fallback to <think> tags (Legacy)
    if not thinking_trace and isinstance(raw_response, str):
        match = re.search(r'<think>(.*?)</think>', raw_response, re.DOTALL)
        if match:
            thinking_trace = match.group(1).strip()
            raw_response = re.sub(r'<think>.*?</think>', '', raw_response, flags=re.DOTALL).strip()

    # Parse JSON
    data = None
    if isinstance(raw_response, dict):
        data = raw_response
    elif isinstance(raw_response, str):
        data = extract_json_from_string(raw_response)
        
    # --- FIX: Handle "List" Hallucinations ---
    if isinstance(data, list):
        # The model returned ["Analysis...", "Conclusion..."]
        # Collapse it into a single string and wrap in the expected format.
        combined_reasoning = "\n".join([str(x) for x in data])
        data = {
            "logic_status": "PASS" if "pass" in combined_reasoning.lower() else "FAIL", 
            "reasoning": combined_reasoning
        }
    # -----------------------------------------
        
    return data, thinking_trace

# --- GATE 1: SANITY CHECK ---
def run_gate_1_sanity(data: dict) -> dict:
    issues = []
    if "internal_monologue" not in data: issues.append("Missing internal_monologue")
    if "factors" not in data: issues.append("Missing factors")
    
    score = data.get("score")
    if not isinstance(score, (int, float)) or not (0 <= score <= 100):
        issues.append(f"Invalid Score: {score}")

    forbidden_terms = ["fuselage", "rivets"] 
    monologue = data.get("internal_monologue", "").lower()
    for term in forbidden_terms:
        if term in monologue:
            issues.append(f"Found forbidden term: {term}")

    status = "FAIL" if issues else "PASS"
    return {"status": status, "issues": issues}

# --- GATE 2: THE CORRESPONDENCE LIBRARIAN ---
def run_gate_2_facts(data: dict, source_text: str, execution_target: str, trace_id: str) -> dict:
    # FIX: Send only the 'factor' text to the Librarian.
    # Removed 'reason_headline' it is an interpretation, not part of the source text.
    claims = [f.get('factor') for f in data.get("factors", []) if f.get('factor')]
    claims_list = json.dumps(claims, indent=2)
    prompt_file = PROMPT_DIR / "P10_fact_checker_v3.1.json"
    
    try:
        response_obj, _ = load_and_run_prompt(
            manifest_path=str(prompt_file),
            execution_target=execution_target,
            calling_tool_name="Audit_Gate_2",
            run_id=trace_id,
            template_vars={"source_text": source_text, "claims_list": claims_list}
        )
        
        result, thinking = parse_llm_response(response_obj)
        
        if not result:
            return {
                "status": "ERROR", 
                "error": "Librarian returned invalid JSON", 
                "model_thinking": thinking
            }

        verdicts = result.get("verdicts", [])
        failures = [v for v in verdicts if v["status"] in ["CONTRADICTED", "UNSUPPORTED"]]
        
        status = "FAIL" if failures else "PASS"
        return {
            "status": status, 
            "verdicts": verdicts, 
            "failures": failures,
            "model_thinking": thinking
        }

    except Exception as e:
        return {"status": "ERROR", "error": str(e)}

# --- GATE 3: THE LOGIC ENGINE ---

# In steps/audit_engine.py

def run_gate_3_logic(data: dict, fact_results: dict, execution_target: str, trace_id: str) -> dict:
    rich_summary = []
    
    # 1. Create a Normalized Map
    verdict_map = {}
    for v in fact_results.get('verdicts', []):
        norm_key = normalize_claim(v.get('claim', ''))
        verdict_map[norm_key] = v.get('status')
    
    # 2. Match factors
    for f in data.get("factors", []):
        fact_text = f.get('factor', '')
        norm_factor = normalize_claim(fact_text)
        # Robust Lookup
        status = verdict_map.get(norm_factor, "SKIPPED")
        
        rich_summary.append({
            "claim": fact_text,
            "verdict": status, 
            "interpretation": f.get('reason_headline'),       
            "reasoning": f.get('reason_detailed')             
        })

    # --- THE FIX: Convert Data to Markdown Report (Stops JSON Parroting) ---
    fact_report_lines = ["**VERIFIED FACTS REPORT:**"]
    for item in rich_summary:
        icon = "✅" if item['verdict'] == "SUPPORTED" else "❌" if item['verdict'] == "CONTRADICTED" else "⚠️"
        fact_report_lines.append(f"---")
        fact_report_lines.append(f"{icon} **VERDICT:** {item['verdict']}")
        fact_report_lines.append(f"   **Claim:** {item['claim']}")
        fact_report_lines.append(f"   **Interpretation:** {item['interpretation']}")
        if item.get('reasoning'):
            fact_report_lines.append(f"   **Reasoning:** {item['reasoning']}")
            
    fact_report_str = "\n".join(fact_report_lines)
    # -------------------------------------------------------------------

    prompt_file = PROMPT_DIR / "P11_logic_auditor_v3.0.json"

    try:
        response_obj, _ = load_and_run_prompt(
            manifest_path=str(prompt_file),
            execution_target=execution_target,
            calling_tool_name="Audit_Gate_3",
            run_id=trace_id, 
            template_vars={
                "role": data.get("identity", {}).get("role", "Unknown"),
                "score": data.get("score", 0),
                "action": data.get("action_taken", "Unknown"),
                "monologue": data.get("internal_monologue", ""),
                
                # PASS TEXT REPORT, NOT JSON
                "fact_check_summary": fact_report_str 
            }
        )
        
        result, thinking = parse_llm_response(response_obj)
        
        # Validation Check
        if not result or not isinstance(result, dict):
            return {
                "logic_status": "ERROR",
                "reasoning": f"Invalid JSON received. Got type: {type(result).__name__}",
                "model_thinking": thinking
            }

        result['model_thinking'] = thinking
        return result

    except Exception as e:
        return {"logic_status": "ERROR", "reasoning": str(e)}

# --- ORCHESTRATOR ---
def run_audit_gauntlet(target_file: Path, source_text: str, execution_target: str, company_name: str = "Unknown") -> dict:
    try:
        data = json.loads(target_file.read_text(encoding='utf-8'))
    except:
        return {"filename": target_file.name, "final_verdict": "ERROR", "reason": "Invalid JSON input"}
    # --- Construct Trace ID ---
    system_name = "correspondence-auditor"
    file_label = target_file.stem.replace("result_input_", "").replace("result_", "")
    base_trace_id = f"{system_name}_{company_name}_{file_label}"

    log = {"filename": target_file.name, "gates": {}}

    # Gate 1 (Sanity) - No LLM, no trace needed
    g1 = run_gate_1_sanity(data)
    log["gates"]["gate_1"] = g1
    if g1["status"] == "FAIL":
        log["final_verdict"] = "FAIL"
        log["reason"] = f"Sanity: {g1['issues']}"
        return log

    # Gate 2
    g2 = run_gate_2_facts(
        data, 
        source_text, 
        execution_target, 
        trace_id=f"{base_trace_id}_gate2_librarian" 
    )
    log["gates"]["gate_2"] = g2
    if g2["status"] == "FAIL":
        log["final_verdict"] = "FAIL"
        log["reason"] = f"Fact Check: {len(g2['failures'])} failures"
        return log
    if g2["status"] == "ERROR":
        log["final_verdict"] = "ERROR"
        log["reason"] = g2.get("error")
        return log

    # Gate 3
    g3 = run_gate_3_logic(
        data, 
        g2, 
        execution_target, 
        trace_id=f"{base_trace_id}_gate3_logic" 
    )

    log["gates"]["gate_3"] = g3
    
    # --- CRITICAL FIX: SAFE ACCESS ---
    logic_status = g3.get("logic_status", "ERROR")
    
    if logic_status == "FAIL":
        log["final_verdict"] = "FAIL"
        log["reason"] = f"Logic Audit: {g3.get('reasoning')}"
    elif logic_status == "ERROR":
        log["final_verdict"] = "ERROR"
        log["reason"] = f"Logic Audit Crash: {g3.get('reasoning')}"
    else:
        log["final_verdict"] = "PASS"
        log["reason"] = "All Gates Passed"

    return log