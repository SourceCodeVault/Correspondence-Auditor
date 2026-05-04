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

## Interactive Dashboard

The auditor generates a standalone HTML dashboard to visualize run telemetry, failure rates, and the LLM's deep reasoning traces.

This observability is critical for distinguishing between basic epistemic errors (factual hallucinations) and complex cognitive failures (where the model ties itself in logical knots to justify a bad decision).

[![View Live Dashboard](https://SourceCodeVault.github.io/Correspondence-Auditor/output/sample_run/dashboard.png)](https://SourceCodeVault.github.io/Correspondence-Auditor/output/sample_run/audit_dashboard.html)

*(To view the live demo, click the image above. The dashboard runs entirely in the browser with no backend required).*

## Example Input & Output (The Data Shape)

Developers integrating this pipeline usually want to see exactly what the auditor catches. Here is a real example of the pipeline catching a hallucinated penalty.

**1. The LLM Judge's Output (Input to Auditor)**
Your initial LLM judge evaluates a vendor and penalizes them for supposedly lacking a feature:
```json
{
  "factor": "Missing Independent Recovery Mechanism",
  "polarity": -1,
  "reason_headline": "Structural Risk Gap"
}
```

**2. The Ground Truth Source Material**
The auditor checks the judge's claim against the actual text provided to the judge:
```text
"Entra ID provides the independent recovery paths and architectural safeguards required for fiduciary peace of mind."
```

**3. The Quarantine Trace (Gate 2 Output)**
Gate 2 catches the contradiction. It fails the run, prevents the output from moving downstream, and generates a strict reasoning trace for the human auditor:
```json
{
  "status": "FAIL",
  "failures": [
    {
      "claim": "Missing Independent Recovery Mechanism",
      "status": "CONTRADICTED",
      "source": "Main Body",
      "quote": "Entra ID provides the independent recovery paths and architectural safeguards required for fiduciary peace of mind."
    }
  ],
  "model_thinking": "Text explicitly states 'independent recovery paths' in main body ('Entra ID provides the independent recovery paths...'), contradicting the 'missing' claim."
}
```

## Integrating into Existing Pipelines

The Correspondence Auditor is designed with strict segregation of duties in mind. While it comes with a CLI (`run_audit.py`) for asynchronous batch processing of files, the core engine is fully modular.

You can import the individual gates directly into your existing Python application and pass them standard dictionaries, entirely bypassing the file system:

```python
from steps.audit_engine import run_gate_1_sanity, run_gate_2_facts, run_gate_3_logic

# Pass your in-memory JSON objects directly to the gates
sanity_result = run_gate_1_sanity(llm_output_dict)
```

## Design Principles

**Fail-closed, not fail-open.** Infrastructure errors produce `ERROR` states rather than silent passes. No output reaches downstream consumers without clearing all three gates.

**High recall, false-positive bias.** The system is tuned to minimise the risk of an undetected sycophantic or hallucinated error slipping through. Borderline cases are flagged, not passed.

**Quarantine with reasoning traces.** Failed outputs are quarantined with full reasoning traces for human review — not just a pass/fail label, but the auditor's working shown in full.

**Backend-agnostic.** Gates 2 and 3 support configurable LLM backends (local Ollama, OpenRouter, or Cerebras), so you can run the audit pipeline entirely on-premises if your threat model requires it.

## Deployment Modes & Governance Posture

The Correspondence Auditor supports two deployment modes with fundamentally different governance postures.

### Mode A: Inline Halt Gate (Recommended)

The Auditor sits directly in the execution path between the LLM-as-Judge and any downstream consumer. No output propagates unless it clears all three gates. If a claim is contradicted by source text, or the judge's reasoning is internally incoherent, the run is halted and the output is quarantined with full reasoning traces for human review.

In this mode, disabling the Auditor changes what reaches production. This is the mode in which the tool functions as **independent, differently-biased observation** — a separate lens with different failure modes to the system it oversees.

### Mode B: Retrospective Evidence Generation

The tool can be run asynchronously against historical outputs to generate evidence, reasoning traces, and failure analyses for human review. This is useful for assessing the reliability of an existing LLM-as-Judge pipeline or for building a body of evidence about systemic failure patterns.

**Note:** In this mode, the Auditor is a detective control, not a preventive one. It tells you what happened. It does not prevent hallucinated or sycophantic outputs from reaching downstream consumers. Running Mode B and treating it as Mode A is a governance failure — it is the retrospective observation that this tool was designed to move beyond.

### Transparency: The Non-Deterministic Gates

Gates 2 and 3 are LLM-powered. While they are configured for near-deterministic behaviour (temperature 0.1, top_p ≤ 0.2) and grounded against provided source text rather than the model's own beliefs, they remain probabilistic. The system is tuned for high recall with a deliberate false-positive bias: borderline cases are quarantined, not passed. This means the Auditor may flag outputs that are in fact correct. It will not silently pass outputs that are flawed.

The reasoning traces captured at each gate exist precisely because these judgments are not infallible. They provide the human reviewer with the Auditor's full working, so that the final determination rests with a person — not with another model.

Just as no human auditor is fully deterministic, this system does not need to be either. But it does need to provide a level of determinism that delivers reasonable assurance. The fail-closed architecture, the segregation of duties between gates, and the grounding against source evidence are the mechanisms by which that assurance is achieved.

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

Working production code. The core concepts have been battle tested in production over 9 months of active development, evolving as the third line of defence within a larger 16-month production workflow.  Currently being transitioned to community-owned infrastructure under **AGPLv3**.

## License

AGPL-3.0 — ensuring this tool remains a public good and cannot disappear behind a paywall.

## Contact & Maintainer
Adrian St. Vaughan
* LinkedIn: [Adrian St. Vaughan](https://www.linkedin.com/in/adrianstvaughan/)