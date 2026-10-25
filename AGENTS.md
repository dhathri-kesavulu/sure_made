# AGENTS.md — Ghost Audit Autonomous Agent Operating Guide

## System Overview
The **Ghost Data Still Exists** deletion audit engine is a competition-grade forensic audit system designed to verify GDPR/CCPA-style deletion requests across complex distributed infrastructure (databases, caches, object stores, logs, search indexes, analytics exports, and backup/restore pipelines).

The audit engine verifies whether personal data for a requested user has truly been eliminated or if "ghost data" persists, produces deterministic findings, creates remediation steps for non-compliant systems, and provides verification checks for compliant systems according to `deletion_rules.csv`.

---

## Architecture Overview
The system consists of modular forensic audit components:
1. **Identity Resolution (`ghost_audit.identity` / `ghost.identity_graph`)**:
   - Resolves current and historical identifiers (`user_id`, `email`, aliases, SHA-256 hashed emails, phone numbers, account keys).
   - Tracks timeline of identity transitions and audit events.
2. **Evidence Parser & Indexer (`ghost_audit.evidence`)**:
   - Multi-format ingestion: SQL dump snapshots, JSON bundles, application & syslog logs, extracted PDF/PNG documents.
   - Extracts system IDs, resource IDs, exposed PII, and timestamps.
3. **Policy Engine (`ghost_audit.policy`)**:
   - Encodes retention rules, required treatments (`DELETE`, `REDACT`, `ANONYMIZE`, `RETAIN`), permitted identifiers (e.g., tombstones vs full PII).
4. **Audit Engine (`ghost_audit.engine`)**:
   - Reconstructs operational timeline (syncs, exports, restores, deletion attempts).
   - Determines ground-truth status at `audit_at` timestamp: `COMPLETE`, `INCOMPLETE`, or `NOT_APPLICABLE`.
   - Generates strictly ordered steps (`FINDING` -> `REMEDIATION` -> `VERIFICATION`) per the problem statement specification.
5. **Independent Submission Validator (`validate_submission.py`)**:
   - Standalone validator verifying schema, constraints, consecutive numbering, canonical ordering, and referential integrity against `problem_statement.md`.

---

## Required Output Contract
- **File**: `output.csv` (CSV format with header: `case_id,deletion_status,steps_json`).
- **Row count**: Exactly 518 data rows matching all cases in `dataset/deletion_requests.csv`, sorted ascending by `case_id`.
- **Status values**: `COMPLETE`, `INCOMPLETE`, `NOT_APPLICABLE`.
  - For `NOT_APPLICABLE`: `steps_json` must be `[]`.
  - For `COMPLETE`: verification steps only, citing valid `evidence_id`.
  - For `INCOMPLETE`: findings (citing evidence), remediation actions (empty evidence), verification checks (empty evidence).
- **Step ordering**:
  1. Phase: `FINDING` -> `REMEDIATION` -> `VERIFICATION`
  2. Operation: Ordered exactly per `problem_statement.md` operation table.
  3. Tie-breaking: `system_id` ascending, then `resource_id` ascending.
  4. Numbering: `step_no` strictly consecutive starting at 1.

---

## Operational Commands

### 1. Run Unit Tests
```bash
python -m unittest discover -s . -p "test*.py"
```

### 2. Run Audit on Full Dataset (518 Cases)
```bash
python -m ghost_audit.run --dataset dataset --requests deletion_requests.csv --out output.csv
```

### 3. Run Audit on Sample Dataset (32 Cases)
```bash
python -m ghost_audit.run --dataset dataset --requests sample_deletion_requests.csv --out sample_predictions.csv
```

### 4. Evaluate Predictions Against Sample Truth
```bash
python -m ghost_audit.evaluate sample_predictions.csv dataset/sample_output.csv
```

### 5. Run Independent Submission Validator
```bash
python validate_submission.py --output output.csv --requests dataset/deletion_requests.csv
```

---

## Coding Constraints
- **Zero Hallucination**: Never invent evidence identifiers or hardcode case IDs.
- **Deterministic**: Execution must be 100% reproducible with identical output across runs.
- **No Unsafe Deserialization**: Cache operations must be strictly local and validated.
- **Evidence Provenance**: Every finding and complete-case verification must cite an authentic `file_id`, `activity_id`, or `audit_id`.
- **Safe Serialization**: `output.csv` must write JSON `steps_json` cleanly with standard CSV escaping.

---

## Resume Instructions
If execution is interrupted:
1. Review `PROJECT_STATE.md` for current milestone and metrics.
2. Check `git status` and recent commit logs.
3. Run `python -m unittest discover -s . -p "test*.py"` to ensure baseline test pass.
4. Run `python validate_submission.py` to check current output status.
5. Resume the active improvement cycle documented in `PROJECT_STATE.md`.
