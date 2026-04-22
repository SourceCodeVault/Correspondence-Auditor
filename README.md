# Correspondence Auditor — Three-Stage Gauntlet for LLM-as-Judge Pipelines

An open-source, high-integrity audit layer that sits **downstream** of any LLM-as-Judge pipeline. It catches sycophancy, hallucination, and reasoning failures before evaluated outputs reach downstream consumers.

## The Problem

LLM-as-Judge pipelines are increasingly used to evaluate AI outputs in safety-critical domains. But the evaluating model can tell the evaluated model what it wants to hear rather than what is true. There is currently no standard infrastructure for auditing the judge's own work.

The Correspondence Auditor provides that infrastructure.

## How It Works

The audit engine runs every judged output through a **Three-Stage Gauntlet**:

| Gate | Name | What It Does | LLM? |
|------|------|--------------|------|
| **Gate 1** | Sanity Check | Deterministic structural validation — required fields, score ranges, forbidden terms | No |
| **Gate 2** | The Librarian | Semantic truth verification — every claim is checked against **provided source text**, producing tripartite verdicts: `SUPPORTED` / `CONTRADICTED` / `UNSUPPORTED` | Yes |
| **Gate 3** | The Logic Engine | Logical coherence audit — uses Gate 2's results as context to catch internal contradictions, category errors, and reasoning failures | Yes |

Gates 2 and 3 are LLM-powered but **separated by duty** and **grounded against provided evidence**, not against the model's own beliefs. This is a fundamentally different approach to the "who watches the watchmen" problem.

## Design Principles

**Fail-closed, not fail-open.** Infrastructure errors produce `ERROR` states rather than silent passes. No output reaches downstream consumers without clearing all three gates.

**High recall, false-positive bias.** The system is tuned to minimise the risk of an undetected sycophantic or hallucinated error slipping through. Borderline cases are flagged, not passed.

**Quarantine with reasoning traces.** Failed outputs are quarantined with full reasoning traces for human review — not just a pass/fail label, but the auditor's working shown in full.

**Backend-agnostic.** Gates 2 and 3 support configurable LLM backends (local Ollama, OpenRouter, or Cerebras), so you can run the audit pipeline entirely on-premises if your threat model requires it.

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
└── output/                       # Place outputs to audit here
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

The CLI will prompt you to select an output folder and execution target, then offer options to run audits, view results in an interactive HTML dashboard, or archive failures.

## Expected Input

Each audit run folder should contain:

- JSON outputs from your LLM-as-Judge pipeline (the evaluated outputs to audit)
- Source material (JSON or YAML) containing the evidence the judge was supposed to evaluate against
- Optionally, a `_provenance/manifest.json` pointing to the source material

The auditor is **domain-agnostic** — it validates any LLM-as-Judge output against any provided source text. The current reference implementation audits cognitive simulation outputs, but the architecture applies wherever an LLM is judging another LLM's work.

## Status

Working production code with 16 months of deployment data. Currently being transitioned to community-owned infrastructure under **AGPLv3**.

## License

AGPL-3.0 — ensuring this tool remains a public good and cannot disappear behind a paywall.
