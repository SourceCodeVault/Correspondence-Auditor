#  Correspondence Auditor — Multi-Stage Audit Engine

A three-gate audit pipeline that validates AI-generated simulations against source material. Part of the Source Code Vault Ltd cognitive simulation framework.

## What It Does

The audit engine takes simulated outputs (JSON files containing an AI persona's internal monologue, scored factors, and decision rationale) and runs them through three verification gates:

| Gate | Name | Purpose |
|------|------|---------|
| **Gate 1** | Sanity Check | Structural validation — required fields, score ranges, forbidden terms |
| **Gate 2** | The Librarian | Fact verification — every claim the persona made is checked against the original source material |
| **Gate 3** | The Logic Engine | Logic audit — verifies reasoning consistency and catches domain errors (e.g. demanding irrelevant features) |

Gates 2 and 3 are themselves LLM-powered, using configurable backends (local Ollama, OpenRouter, or Cerebras).

## Project Structure

```
├── run_audit.py                  # CLI entry point
├── steps/
│   └── audit_engine.py           # Core 3-gate audit logic
├── prompts/
│   ├── P10_fact_checker_v3.1.json   # Gate 2 prompt manifest
│   └── P11_logic_auditor_v3.0.json  # Gate 3 prompt manifest
├── shared/
│   ├── llm_utils.py              # Prompt loading, LLM dispatch, retry logic
│   ├── string_utils.py           # Robust JSON extraction from LLM output
│   ├── ui_utils.py               # Terminal formatting
│   ├── logging_utils.py          # Telemetry logging (stubbed for standalone use)
│   └── api_clients/
│       ├── ollama_client.py      # Local Ollama backend
│       ├── openrouter_client.py  # OpenRouter API backend
│       └── cerebras_client.py    # Cerebras API backend
├── requirements.txt
└── output/                       # Place simulation run folders here
```

## Setup

```bash
pip install -r requirements.txt
```

Configure API keys in a `.env` file at the project root:

```
OPENROUTER_API_KEY=sk-or-...
CEREBRAS_API_KEY_FREE=csk-...
CEREBRAS_API_KEY_PAID=csk-...
```

For local inference, ensure Ollama is running with the model specified in the prompt manifests.

## Usage

```bash
python run_audit.py
```

The CLI will prompt you to select a simulation run folder (from `output/`) and an execution target, then offer options to run audits, view results in an interactive HTML dashboard, or archive failures.

## Expected Input

Each simulation run folder should contain:
- `result_*.json` — Persona simulation outputs to audit
- An input file (`input_*.json` or `input_*.yaml`) containing the source material and evidence sources
- Optionally, a `_provenance/manifest.json` pointing to the input file

## Status

This is working production code extracted from a larger workflow. The upstream simulation engine and downstream report generator are not included in this repository.
