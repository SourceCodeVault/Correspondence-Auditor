# The Correspondence Auditor

A production-grade, open-source AI audit pipeline designed to detect sycophancy, hallucination, and evaluator drift in LLM-as-Judge evaluation workflows. 

As AI systems are deployed in high-stakes domains, LLM-as-Judge pipelines are increasingly vulnerable to sycophancy—where the evaluating model tells the evaluated model what it wants to hear rather than what is true. The Correspondence Auditor provides a deterministic, fail-closed control plane that sits downstream of these evaluations to enforce evidential parity and ground-truth verification.

## 🏗️ Architecture: The Three-Gate Gauntlet

The system processes LLM outputs through a strict, sequential three-gate validation pipeline:

* **Gate 1 (Sanity):** Performs deterministic structural validation without LLM involvement, ensuring inputs meet strict formatting and schema requirements.
* **Gate 2 (The Librarian):** Performs semantic truth verification against provided source text. Every claim is independently verified and receives a tripartite verdict: SUPPORTED, CONTRADICTED, or UNSUPPORTED.
* **Gate 3 (The Psychologist):** Evaluates logical coherence using Gate 2's results as context. It checks for internal contradictions, logical fallacies, and category errors.

## 🛡️ Enterprise GRC Principles

The Correspondence Auditor is not a theoretical prototype; it is an operational Governance, Risk, and Compliance (GRC) control plane built on established internal audit principles:
* **Separation of Duties:** Disk-based isolation between the generator and the auditor.
* **Continuous Control Monitoring:** Generates full reasoning traces and source-text quotes for every verdict to ensure independent verifiability.
* **Incident Management (Fail-Closed):** Failed outputs are actively quarantined with temporal archiving, ensuring infrastructure errors produce ERROR states rather than silent passes.

## 📜 License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPLv3)**. 

We are committed to preventing the commercial capture of critical AI safety infrastructure. The AGPLv3 license ensures that the core audit engine remains permanently free for the community to use, modify, and redistribute.
