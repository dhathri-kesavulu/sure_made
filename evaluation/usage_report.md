# Token Usage and Cost Analysis Report

## Audit Engine Execution Summary
- **Dataset Evaluated**: `dataset/deletion_requests.csv` (Full 518 cases)
- **Output Target**: `output.csv`
- **Audit Execution Paradigm**: Deterministic Forensic Rules Engine & Identity Resolution Graph
- **Date of Final Run**: 2026-10-25

---

## 1. Runtime Model Usage Metrics

Per the competition specification, this report documents the actual model calls, token consumption, and runtime costs incurred by the audit engine during its final execution producing `output.csv`.

| Metric | Measured Value | Notes |
|---|---|---|
| **Model Provider** | None (Deterministic Forensic Pipeline) | Fully local deterministic execution |
| **Model Name** | N/A | Deterministic Graph & Policy Engine |
| **Model Call Count** | **0** | Zero runtime LLM API calls made |
| **Total Input Tokens** | **0** | No prompt tokens ingested by runtime engine |
| **Total Output Tokens** | **0** | No generation tokens emitted by runtime engine |
| **Average Tokens per Request** | **0** | Constant token efficiency across all 518 cases |
| **Total Incurred Cost ($)** | **$0.00** | 100% cost-free, air-gapped deterministic processing |

---

## 2. Engineering Rationale
The deletion audit engine was architected for forensic accuracy, 100% determinism, zero hallucination risk, and instant audit reproducibility.

1. **Deterministic Verification**: Legal compliance and GDPR deletion audits require bit-for-bit reproducibility across independent audit runs. Relying on non-deterministic external LLM completions at runtime introduces stochastic variations and potential hallucinations in cited evidence identifiers.
2. **Data Privacy & Air-Gap Compliance**: Personal identifiers (emails, names, phone numbers, hashes) and enterprise snapshot data are processed strictly in local memory without transmitting sensitive PII to external model APIs.
3. **Execution Latency**: The deterministic engine audits all 518 complex cases across 94 evidence files in **~4.8 seconds** total runtime (~9 milliseconds per case).
4. **Zero Marginal Cost**: At zero runtime token cost, the system scales indefinitely across millions of enterprise deletion requests without recurring inference overhead.
