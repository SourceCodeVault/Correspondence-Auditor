# Stub — Logging utilities for LLM call telemetry.
# In production this writes to a structured log/datalake.
# Stubbed here so the audit workflow runs standalone.

import json
import os
from pathlib import Path
from datetime import datetime

_LOG_DIR = Path(__file__).parent.parent.parent / "output" / "_logs"


def log_llm_call(log_data: dict):
    """Append a single LLM call record to a JSONL log file."""
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = _LOG_DIR / "llm_calls.jsonl"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_data, default=str) + "\n")
    except Exception:
        pass  # Non-critical — never crash the caller


def log_json_extraction_failure(run_id, calling_tool, model_id, raw_text, attempts):
    """Record a JSON extraction failure for debugging."""
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = _LOG_DIR / "json_failures.jsonl"
        record = {
            "timestamp": datetime.utcnow().isoformat(),
            "run_id": run_id,
            "calling_tool": calling_tool,
            "model_id": model_id,
            "raw_text_len": len(raw_text) if raw_text else 0,
            "attempts": attempts,
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass


def save_raw_trace(run_id, model_id, raw_content, thinking_content="",
                   prompt_messages=None, provider_id="N/A", alias="Unknown",
                   params=None, execution_target="unknown"):
    """Persist the raw LLM trace for reproducibility / provenance."""
    try:
        trace_dir = _LOG_DIR / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        safe_run = run_id.replace("/", "_").replace(":", "_")
        trace_file = trace_dir / f"trace_{safe_run}_{ts}.json"
        trace_file.write_text(json.dumps({
            "run_id": run_id,
            "model_id": model_id,
            "provider_id": provider_id,
            "alias": alias,
            "execution_target": execution_target,
            "params": params or {},
            "prompt_messages": prompt_messages or [],
            "raw_content": raw_content[:5000],
            "thinking_content": (thinking_content or "")[:5000],
        }, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass
