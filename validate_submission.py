#!/usr/bin/env python3
"""Standalone independent validator for Ghost Data Still Exists submission contract.

Verifies output.csv against problem_statement.md specifications independently from
engine implementation assumptions.
"""

import argparse
import csv
import json
import os
import re
import sys
from typing import Dict, List, Set, Tuple, Any

EXPECTED_COLUMNS = ["case_id", "deletion_status", "steps_json"]
VALID_STATUSES = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}

# Allowed operations per phase with their required exact state
ALLOWED_PHASE_OPS = {
    "FINDING": {
        "REPORT_REMAINING_RECORD": "PRESENT",
        "REPORT_EXPOSED_PII": "PRESENT",
        "REPORT_ACTIVE_DATA_FLOW": "ACTIVE",
        "REPORT_RESTORE_RISK": "ACTIVE",
    },
    "REMEDIATION": {
        "DISABLE_DATA_FLOW": "DISABLED",
        "BLOCK_RESTORE": "BLOCKED",
        "DELETE_RECORD": "ABSENT",
        "REDACT_RECORD": "REDACTED",
        "ANONYMIZE_RECORD": "ANONYMIZED",
    },
    "VERIFICATION": {
        "VERIFY_RECORD_ABSENT": "ABSENT",
        "VERIFY_RECORD_PRESENT": "PRESENT",
        "VERIFY_RECORD_REDACTED": "REDACTED",
        "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
        "VERIFY_DATA_FLOW_DISABLED": "DISABLED",
        "VERIFY_RESTORE_BLOCKED": "BLOCKED",
    },
}

PHASE_CANONICAL_ORDER = ["FINDING", "REMEDIATION", "VERIFICATION"]
PHASE_RANK = {p: i for i, p in enumerate(PHASE_CANONICAL_ORDER)}

OP_CANONICAL_ORDER = {
    "FINDING": [
        "REPORT_REMAINING_RECORD",
        "REPORT_EXPOSED_PII",
        "REPORT_ACTIVE_DATA_FLOW",
        "REPORT_RESTORE_RISK",
    ],
    "REMEDIATION": [
        "DISABLE_DATA_FLOW",
        "BLOCK_RESTORE",
        "DELETE_RECORD",
        "REDACT_RECORD",
        "ANONYMIZE_RECORD",
    ],
    "VERIFICATION": [
        "VERIFY_RECORD_ABSENT",
        "VERIFY_RECORD_PRESENT",
        "VERIFY_RECORD_REDACTED",
        "VERIFY_RECORD_ANONYMIZED",
        "VERIFY_DATA_FLOW_DISABLED",
        "VERIFY_RESTORE_BLOCKED",
    ],
}
OP_RANK = {
    phase: {op: i for i, op in enumerate(ops)}
    for phase, ops in OP_CANONICAL_ORDER.items()
}

REQUIRED_STEP_KEYS = {
    "step_no",
    "phase",
    "system_id",
    "resource_id",
    "operation",
    "state",
    "evidence_id",
}


def load_valid_evidence_ids(dataset_dir: str) -> Set[str]:
    """Load all valid evidence IDs from evidence_files.csv, system_activity.csv, user_audit_log.csv."""
    valid_ids = set()
    
    # 1. file_ids from evidence_files.csv
    ef_path = os.path.join(dataset_dir, "evidence_files.csv")
    if os.path.exists(ef_path):
        with open(ef_path, encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                if row.get("file_id"):
                    valid_ids.add(row["file_id"].strip())
                    
    # 2. activity_ids from system_activity.csv
    sa_path = os.path.join(dataset_dir, "system_activity.csv")
    if os.path.exists(sa_path):
        with open(sa_path, encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                if row.get("activity_id"):
                    valid_ids.add(row["activity_id"].strip())
                    
    # 3. audit_ids from user_audit_log.csv
    ua_path = os.path.join(dataset_dir, "user_audit_log.csv")
    if os.path.exists(ua_path):
        with open(ua_path, encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                if row.get("audit_id"):
                    valid_ids.add(row["audit_id"].strip())

    return valid_ids


class ValidationResult:
    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.total_rows: int = 0
        self.status_counts: Dict[str, int] = collections.defaultdict(int)
        self.total_steps: int = 0
        self.phase_counts: Dict[str, int] = collections.defaultdict(int)

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0

    def add_error(self, msg: str):
        self.errors.append(msg)

    def add_warning(self, msg: str):
        self.warnings.append(msg)


import collections


def validate_submission(
    output_path: str,
    requests_path: str,
    dataset_dir: str = "dataset",
    check_evidence_ids: bool = True,
) -> ValidationResult:
    res = ValidationResult()

    if not os.path.exists(output_path):
        res.add_error(f"Output file does not exist: {output_path}")
        return res

    if not os.path.exists(requests_path):
        res.add_error(f"Requests file does not exist: {requests_path}")
        return res

    # 1. Load expected cases from requests_path
    expected_case_ids = []
    with open(requests_path, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            expected_case_ids.append(row["case_id"].strip())

    expected_set = set(expected_case_ids)
    expected_count = len(expected_case_ids)

    # 2. Load evidence IDs for provenance verification
    valid_evidence_ids = set()
    if check_evidence_ids:
        valid_evidence_ids = load_valid_evidence_ids(dataset_dir)

    # 3. Parse output CSV
    rows = []
    with open(output_path, encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != EXPECTED_COLUMNS:
            res.add_error(
                f"Invalid CSV header. Expected {EXPECTED_COLUMNS}, got {header}"
            )

        for line_no, row in enumerate(reader, start=2):
            if len(row) != 3:
                res.add_error(
                    f"Line {line_no}: Expected 3 columns, found {len(row)}: {row}"
                )
                continue
            rows.append((line_no, row[0].strip(), row[1].strip(), row[2]))

    res.total_rows = len(rows)

    # Check total rows
    if len(rows) != expected_count:
        res.add_error(
            f"Row count mismatch. Expected exactly {expected_count} data rows, found {len(rows)}"
        )

    # Check case ID set and order
    actual_case_ids = [r[1] for r in rows]
    if len(actual_case_ids) != len(set(actual_case_ids)):
        duplicates = [
            cid
            for cid, count in collections.Counter(actual_case_ids).items()
            if count > 1
        ]
        res.add_error(f"Duplicate case_ids found: {duplicates[:10]}")

    missing_cases = expected_set - set(actual_case_ids)
    if missing_cases:
        res.add_error(
            f"Missing {len(missing_cases)} expected case_ids: {sorted(list(missing_cases))[:10]}"
        )

    unexpected_cases = set(actual_case_ids) - expected_set
    if unexpected_cases:
        res.add_error(
            f"Found {len(unexpected_cases)} unexpected case_ids: {sorted(list(unexpected_cases))[:10]}"
        )

    # Check sorting
    sorted_case_ids = sorted(actual_case_ids)
    if actual_case_ids != sorted_case_ids:
        res.add_error("Rows are not sorted by case_id ascending")

    # 4. Detailed row & steps_json validation
    for line_no, case_id, status, steps_raw in rows:
        res.status_counts[status] += 1

        if status not in VALID_STATUSES:
            res.add_error(
                f"Line {line_no} ({case_id}): Invalid deletion_status '{status}'. Expected one of {VALID_STATUSES}"
            )
            continue

        # JSON parsing
        try:
            steps = json.loads(steps_raw)
        except Exception as e:
            res.add_error(
                f"Line {line_no} ({case_id}): Invalid JSON in steps_json: {e}"
            )
            continue

        if not isinstance(steps, list):
            res.add_error(
                f"Line {line_no} ({case_id}): steps_json must be a JSON array, got {type(steps).__name__}"
            )
            continue

        # Status consistency
        if status == "NOT_APPLICABLE":
            if steps:
                res.add_error(
                    f"Line {line_no} ({case_id}): NOT_APPLICABLE status must have empty steps_json '[]', found {len(steps)} steps"
                )
            continue

        if status == "COMPLETE":
            for step in steps:
                phase = step.get("phase")
                if phase != "VERIFICATION":
                    res.add_error(
                        f"Line {line_no} ({case_id}): COMPLETE case has forbidden step phase '{phase}'. Only VERIFICATION allowed."
                    )
                    break

        if status == "INCOMPLETE":
            phases_in_case = {s.get("phase") for s in steps}
            if "FINDING" not in phases_in_case:
                res.add_warning(
                    f"Line {line_no} ({case_id}): INCOMPLETE case has no FINDING phase steps"
                )

        # Step-by-step schema, numbering, operations, and ordering
        prev_step_no = 0
        canonical_sort_keys = []

        for idx, step in enumerate(steps, start=1):
            res.total_steps += 1
            if not isinstance(step, dict):
                res.add_error(
                    f"Line {line_no} ({case_id}) step {idx}: Step must be a JSON object"
                )
                continue

            # Keys check
            missing_keys = REQUIRED_STEP_KEYS - set(step.keys())
            if missing_keys:
                res.add_error(
                    f"Line {line_no} ({case_id}) step {idx}: Missing required keys: {missing_keys}"
                )

            # Step numbering consecutive check
            step_no = step.get("step_no")
            if step_no != idx or step_no != prev_step_no + 1:
                res.add_error(
                    f"Line {line_no} ({case_id}) step {idx}: Non-consecutive step_no {step_no}, expected {idx}"
                )
            prev_step_no = idx

            phase = step.get("phase")
            res.phase_counts[phase] += 1
            op = step.get("operation")
            state = step.get("state")
            sys_id = step.get("system_id", "")
            res_id = step.get("resource_id", "")
            ev_id = step.get("evidence_id", "")

            # Phase check
            if phase not in ALLOWED_PHASE_OPS:
                res.add_error(
                    f"Line {line_no} ({case_id}) step {idx}: Invalid phase '{phase}'"
                )
                continue

            # Operation check
            if op not in ALLOWED_PHASE_OPS[phase]:
                res.add_error(
                    f"Line {line_no} ({case_id}) step {idx}: Invalid operation '{op}' for phase '{phase}'"
                )
                continue

            # State check
            expected_state = ALLOWED_PHASE_OPS[phase][op]
            if state != expected_state:
                res.add_error(
                    f"Line {line_no} ({case_id}) step {idx}: Invalid state '{state}' for op '{op}'. Expected '{expected_state}'"
                )

            # Evidence ID validation
            if status == "COMPLETE":
                if not ev_id:
                    res.add_error(
                        f"Line {line_no} ({case_id}) step {idx}: COMPLETE verification step has empty evidence_id"
                    )
                elif check_evidence_ids and valid_evidence_ids and ev_id not in valid_evidence_ids:
                    res.add_warning(
                        f"Line {line_no} ({case_id}) step {idx}: evidence_id '{ev_id}' not found in known evidence manifest/activity logs"
                    )
            elif status == "INCOMPLETE":
                if phase == "FINDING":
                    if not ev_id:
                        res.add_error(
                            f"Line {line_no} ({case_id}) step {idx}: FINDING step has empty evidence_id"
                        )
                    elif check_evidence_ids and valid_evidence_ids and ev_id not in valid_evidence_ids:
                        res.add_warning(
                            f"Line {line_no} ({case_id}) step {idx}: evidence_id '{ev_id}' not found in known evidence manifest/activity logs"
                        )
                else:
                    # REMEDIATION and future VERIFICATION must use empty evidence_id per problem statement
                    if ev_id != "":
                        res.add_error(
                            f"Line {line_no} ({case_id}) step {idx}: INCOMPLETE {phase} step must have empty evidence_id (''), got '{ev_id}'"
                        )

            # Ordering key: (phase_rank, op_rank, sys_id, res_id)
            p_rank = PHASE_RANK.get(phase, 99)
            o_rank = OP_RANK.get(phase, {}).get(op, 99)
            canonical_sort_keys.append((p_rank, o_rank, sys_id, res_id))

        # Check ordering across the case steps
        for i in range(len(canonical_sort_keys) - 1):
            if canonical_sort_keys[i] > canonical_sort_keys[i + 1]:
                res.add_error(
                    f"Line {line_no} ({case_id}): Step ordering violation between step {i+1} and {i+2}. "
                    f"Steps must be ordered by phase, then operation order, then system_id, then resource_id."
                )
                break

    return res


def main():
    parser = argparse.ArgumentParser(
        description="Ghost Data Still Exists Independent Submission Validator"
    )
    parser.add_argument(
        "--output", default="output.csv", help="Path to submission output CSV"
    )
    parser.add_argument(
        "--requests",
        default="dataset/deletion_requests.csv",
        help="Path to deletion_requests.csv",
    )
    parser.add_argument(
        "--dataset", default="dataset", help="Dataset directory"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors",
    )
    args = parser.parse_args()

    print(f"=== Independent Submission Validator ===")
    print(f"Validating output:   {args.output}")
    print(f"Against requests:    {args.requests}")
    print(f"Dataset root:        {args.dataset}")

    result = validate_submission(
        args.output, args.requests, args.dataset, check_evidence_ids=True
    )

    print(f"\n--- Validation Summary ---")
    print(f"Total Rows Checked: {result.total_rows}")
    print(f"Status Breakdown:   {dict(result.status_counts)}")
    print(f"Total Steps:        {result.total_steps}")
    print(f"Phase Breakdown:    {dict(result.phase_counts)}")
    print(f"Errors Found:       {len(result.errors)}")
    print(f"Warnings Found:     {len(result.warnings)}")

    if result.warnings:
        print(f"\n[!] Warnings ({min(len(result.warnings), 10)} of {len(result.warnings)} shown):")
        for w in result.warnings[:10]:
            print(f"  - {w}")

    if result.errors:
        print(f"\n[X] Errors ({min(len(result.errors), 25)} of {len(result.errors)} shown):")
        for err in result.errors[:25]:
            print(f"  - {err}")
        print("\nVALIDATION FAILED")
        sys.exit(1)

    if args.strict and result.warnings:
        print("\nVALIDATION FAILED (Strict mode: warnings present)")
        sys.exit(1)

    print("\n[V] VALIDATION PASSED — Submission contract fully satisfied!")
    sys.exit(0)


if __name__ == "__main__":
    main()
