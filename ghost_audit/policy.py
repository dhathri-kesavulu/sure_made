"""Policy-as-code: deletion_rules.csv -> executable treatment table."""
import csv, os

# required_action (from CSV) -> (violating states, finding op, remediation op, verification op, verification state)
ACTION = {
    'DELETE': ({'FOUND', 'IDENTIFIABLE'}, 'REPORT_REMAINING_RECORD', 'DELETE_RECORD', 'VERIFY_RECORD_ABSENT', 'ABSENT'),
    'REDACT': ({'IDENTIFIABLE'}, 'REPORT_EXPOSED_PII', 'REDACT_RECORD', 'VERIFY_RECORD_REDACTED', 'REDACTED'),
    'ANONYMIZE': ({'IDENTIFIABLE'}, 'REPORT_EXPOSED_PII', 'ANONYMIZE_RECORD', 'VERIFY_RECORD_ANONYMIZED', 'ANONYMIZED'),
    'RETAIN': (set(), None, None, 'VERIFY_RECORD_PRESENT', 'PRESENT'),
}
# categories whose rule is a *process* control rather than a record treatment
FLOW = {'DATA_FLOW': ({'ACTIVE'}, 'REPORT_ACTIVE_DATA_FLOW', 'DISABLE_DATA_FLOW', 'VERIFY_DATA_FLOW_DISABLED', 'DISABLED')}
RESTORE = {'BACKUP_ARCHIVE': ({'ACTIVE'}, 'REPORT_RESTORE_RISK', 'BLOCK_RESTORE', 'VERIFY_RESTORE_BLOCKED', 'BLOCKED')}

PHASE_ORDER = {
    'FINDING': ['REPORT_REMAINING_RECORD', 'REPORT_EXPOSED_PII', 'REPORT_ACTIVE_DATA_FLOW', 'REPORT_RESTORE_RISK'],
    'REMEDIATION': ['DISABLE_DATA_FLOW', 'BLOCK_RESTORE', 'DELETE_RECORD', 'REDACT_RECORD', 'ANONYMIZE_RECORD'],
    'VERIFICATION': ['VERIFY_RECORD_ABSENT', 'VERIFY_RECORD_PRESENT', 'VERIFY_RECORD_REDACTED', 'VERIFY_RECORD_ANONYMIZED',
                     'VERIFY_DATA_FLOW_DISABLED', 'VERIFY_RESTORE_BLOCKED'],
}
REM_STATE = {'DISABLE_DATA_FLOW': 'DISABLED', 'BLOCK_RESTORE': 'BLOCKED', 'DELETE_RECORD': 'ABSENT', 'REDACT_RECORD': 'REDACTED', 'ANONYMIZE_RECORD': 'ANONYMIZED'}
FIND_STATE = {'REPORT_REMAINING_RECORD': 'PRESENT', 'REPORT_EXPOSED_PII': 'PRESENT', 'REPORT_ACTIVE_DATA_FLOW': 'ACTIVE', 'REPORT_RESTORE_RISK': 'ACTIVE'}


class Policy:
    def __init__(self, ds):
        self.rules = {r['data_category']: r for r in csv.DictReader(open(os.path.join(ds, 'deletion_rules.csv'), encoding='utf-8', errors='replace'))}

    def treatment(self, rtype):
        """-> (viol_states, find_op, rem_op, ver_op, ver_state) or None if no rule applies"""
        if rtype in FLOW: return FLOW[rtype]
        if rtype in RESTORE: return RESTORE[rtype]
        r = self.rules.get(rtype)
        return ACTION.get(r['required_action']) if r else None
