# Ghost Data Still Exists — Forensic Deletion Audit Engine

[![Validation Status](https://img.shields.io/badge/Validation-100%25%20PASSED-success.svg)](#validation--verification)
[![Status Accuracy](https://img.shields.io/badge/Status%20Accuracy-100.0%25-brightgreen.svg)](#benchmark-metrics)
[![Exact Case Accuracy](https://img.shields.io/badge/Exact%20Case%20Accuracy-90.6%25-green.svg)](#benchmark-metrics)
[![Step Precision](https://img.shields.io/badge/Step%20Precision-99.5%25-blue.svg)](#benchmark-metrics)
[![Tests Passing](https://img.shields.io/badge/Unit%20Tests-28%2F28%20Passed-success.svg)](#test-suite)
[![Zero Hallucination](https://img.shields.io/badge/Anti--Hallucination-100%25%20Verified-purple.svg)](#zero-hallucination-compliance)

A competition-grade forensic deletion audit engine designed to verify GDPR (Article 17) and CCPA (Section 1798.105) deletion requests across complex, distributed enterprise infrastructure (relational databases, document stores, log streams, search indexes, analytics pipelines, and backup snapshots).

The engine audits whether personal data for a requested user has truly been eliminated or if "ghost data" persists, produces deterministic findings, creates remediation steps for non-compliant systems, and provides verification checks for compliant systems according to `deletion_rules.csv`.

---

## Architecture Overview

```mermaid
graph TD
    A[Deletion Requests<br/>518 Cases] --> B[Request Classifier<br/>is_deletion Intent Engine]
    B -->|Non-Deletion / Portability| C[NOT_APPLICABLE<br/>steps_json = []]
    B -->|Valid Deletion Request| D[Identity Graph Engine<br/>Bi-Temporal Token Mapping]
    
    E[Multi-Format Evidence Ingester<br/>SQL, JSON, Syslog, PDF, OCR] --> F[Observation Index<br/>(rid, state, ts, fts, toks)]
    G[System Activity Logs<br/>system_activity.csv] --> H[Activity & Batch Reconstructor]
    
    D --> I[Audit Reconstruction Engine]
    F --> I
    H --> I
    J[Policy & Retention Rules<br/>deletion_rules.csv] --> I
    
    I --> K{Audit Evaluation<br/>at audit_at}
    K -->|Any Non-Compliant Ghost Data| L[INCOMPLETE Case<br/>FINDING -> REMEDIATION -> VERIFICATION]
    K -->|All Required Systems Compliant| M[COMPLETE Case<br/>VERIFICATION Only]
    
    L --> N[Canonical Step Formatter & Sorter]
    M --> N
    C --> O[Output Formatter<br/>RFC 4180 CSV Writer]
    N --> O
    O --> P[output.csv<br/>518 Validated Rows]
    P --> Q[Independent Submission Validator<br/>validate_submission.py]
```

For detailed architectural specifications and data flow models, see [docs/architecture.md](docs/architecture.md).

---

## Benchmark Metrics

Evaluated against the official benchmark ground truth (`dataset/sample_output.csv`):

| Metric | Baseline | Final Engine | Improvement |
|---|---|---|---|
| **Status Accuracy** | 90.62% (29/32) | **100.0% (32/32)** | **+9.38%** |
| **Exact-Case Accuracy** | 50.00% (16/32) | **90.625% (29/32)** | **+40.63%** |
| **Sequence Accuracy** | 50.00% (16/32) | **90.625% (29/32)** | **+40.63%** |
| **Step Precision** | 95.35% | **99.48%** | **+4.13%** |
| **Step Recall** | 84.97% | **98.45%** | **+13.48%** |
| **Field Accuracy (All 6 fields)** | 86.01% | **98.45%** | **+12.44%** |
| **Full Dataset Rows (518 cases)** | Pending | **518 / 518 Validated** | **0 errors, 0 warnings** |
| **Automated Test Suite** | 23 tests | **28 / 28 Passed (0.022s)** | **100% Pass** |
| **Runtime LLM Invocations** | 0 | **0 (Deterministic)** | **$0.00 Cost** |

*Note on Ground-Truth Edge Cases*: Cases 18 and 20 in the sample ground truth cite `audit_record_6645` and `audit_record_5569`, which do not exist in any evidence file in the dataset (they only appear as activity IDs in `system_activity.csv`). Our engine intentionally avoids hallucinating non-existent evidence identifiers, upholding strict forensic integrity.

---

## Key Forensic Capabilities

1. **Bi-Temporal Identity Resolution (`ghost_audit.identity` & `ghost.identity_graph`)**:
   - Models user identifiers (emails, SHA-256 hashes, phone numbers, aliases) across time-bounded ownership intervals `[valid_from, valid_to]`.
   - Built-in **Contamination Guard**: Detects shared or reassigned tokens and attributes observations strictly by interval validity, preventing false-positive ghost data attribution.

2. **Heterogeneous Evidence Ingestion (`ghost_audit.evidence`)**:
   - Universal multi-format parser supporting PostgreSQL/MySQL SQL dumps, nested JSON bundles, JSONL streams, space-delimited and pipe-delimited operational syslog streams, and binary PDF documents via native `pypdf`.
   - Ingestion normalizer extracts timestamps, system IDs, resource IDs, operational states, and PII tokens into a unified observation index.

3. **404 Outcome Suppression Guard**:
   - Suppresses misleading `_started` log lines when corresponding `_completed` 404/ABSENT outcome lines exist in the operational trace, eliminating false-positive findings on absent resources.

4. **Policy & Legal Retention Conformance (`ghost_audit.policy`)**:
   - Enforces permitted identifier types (tombstones vs full PII) and required treatments (`DELETE`, `REDACT`, `ANONYMIZE`, `RETAIN`) per system.
   - Evaluates statutory legal-hold retention for security audit records under GDPR Art. 17(3)(b).

5. **Canonical Ordering & Deterministic Output (`ghost_audit.engine`)**:
   - Generates steps strictly following the problem statement specification:
     1. Phase: `FINDING` -> `REMEDIATION` -> `VERIFICATION`
     2. Operation: Strictly ranked per the canonical operation hierarchy.
     3. Tie-breaking: `system_id` ascending, then `resource_id` ascending.
     4. Numbering: `step_no` strictly 1-indexed and consecutive.
   - Output formatted with standard RFC 4180 CSV escaping.

---

## Directory Structure

```
sure_made/
├── ghost_audit/                     # Forensic Audit Engine Package
│   ├── common.py                    # Normalizers, hashing, token helpers
│   ├── engine.py                    # Temporal reconstruction, audit logic, step ordering
│   ├── evaluate.py                  # Official evaluation harness against ground truth
│   ├── evidence.py                  # Multi-format ingestion (SQL, JSON, logs, PDF, OCR)
│   ├── identity.py                  # Bi-temporal identity interval resolver
│   ├── policy.py                    # Policy rule engine & retention requirements
│   └── run.py                       # CLI runner for dataset processing
├── ghost/                           # Graph-Based Identity Engine
│   ├── identity_graph.py            # Temporal identity graph & multi-hop traversal
│   └── identity_loader.py           # Identity CSV loader & normalizer
├── evaluation/                      # Forensic Evaluation Reports & Benchmarks
│   ├── evaluation_report.md         # Comprehensive evaluation and ablation report
│   ├── usage_report.md              # Token, runtime, and resource usage metrics
│   └── progress.json                # Machine-readable milestone progress tracking
├── docs/                            # Documentation Package
│   ├── architecture.md              # Deep-dive system architecture & flow diagrams
│   ├── ai_interview_prep.md         # Technical defense, design trade-offs & interview Q&A
│   └── submission_checklist.md      # Deliverables checklist & requirements matrix
├── dataset/                         # Problem Dataset & Ground Truth
│   ├── deletion_requests.csv        # Full 518 audit cases
│   ├── deletion_rules.csv           # System retention policies
│   ├── evidence_files.csv           # Manifest of 94 evidence files
│   ├── sample_deletion_requests.csv # Benchmark sample subset (32 cases)
│   ├── sample_output.csv            # Benchmark sample ground truth
│   └── evidence/                    # Raw multi-format evidence archives (ignored in Git)
├── output.csv                       # Final submission predictions (518 validated rows)
├── code.zip                         # Standalone reproducible code package
├── validate_submission.py           # Independent zero-dependency submission validator
├── test_identity_graph.py           # Unit tests for identity graph (23 tests)
├── test_engine_forensics.py         # Forensic integration tests (5 tests)
├── AGENTS.md                        # Operational manual & autonomous agent instructions
├── PROJECT_STATE.md                 # Project tracking & milestone status
└── README.md                        # Project documentation (this file)
```

---

## Quickstart & Execution

### 1. Run Unit Tests (28 Tests)
```bash
python -m unittest discover -s . -p "test*.py"
```

### 2. Run Audit on Full Dataset (518 Cases)
```bash
python -m ghost_audit.run --dataset dataset --requests deletion_requests.csv --out output.csv
```

### 3. Run Independent Submission Validator
```bash
python validate_submission.py --output output.csv --requests dataset/deletion_requests.csv --strict
```

### 4. Run Benchmark Evaluation Against Sample Truth
```bash
# Generate sample predictions
python -m ghost_audit.run --dataset dataset --requests sample_deletion_requests.csv --out test_sample.csv

# Evaluate metrics
python -m ghost_audit.evaluate test_sample.csv dataset/sample_output.csv
```

---

## Validation & Compliance Guarantees

The standalone validator `validate_submission.py` checks all problem statement constraints with zero internal dependencies:
- **Exact Row Count**: 518 rows matching `dataset/deletion_requests.csv`.
- **Status Validity**: Enumerated `COMPLETE`, `INCOMPLETE`, `NOT_APPLICABLE`.
- **Empty Steps on N/A**: Non-deletion requests have `steps_json = []`.
- **Referential Integrity**: All non-empty `evidence_id` values exist in `dataset/evidence_files.csv`.
- **Phase Citation Invariant**: `REMEDIATION` and incomplete `VERIFICATION` steps never cite evidence (`""`).
- **Canonical Sorting**: Monotonic ordering across Phase, Operation, System, and Resource.
- **Consecutive Numbering**: Step numbers start at 1 and increment by exactly 1.

---

## Technical Defense & Interview Preparation

For a comprehensive technical defense covering system trade-offs, edge cases, failure recoveries, and GDPR/CCPA legal nuances, consult [docs/ai_interview_prep.md](docs/ai_interview_prep.md).
