import unittest
import os
import json
from ghost_audit.engine import is_deletion, KEEP_WORDS
from ghost_audit.evidence import log_line, tokens_in, rid_prefix, clean
from validate_submission import validate_submission

class TestEngineForensics(unittest.TestCase):
    def test_request_intent_classification(self):
        # True deletion requests
        self.assertTrue(is_deletion("Please delete my account and all associated personal data."))
        self.assertTrue(is_deletion("Intenté eliminar mi cuenta ayer, por favor completar la eliminación."))
        self.assertTrue(is_deletion("Je demande la suppression définitive du compte associé à cette adresse e-mail."))
        self.assertTrue(is_deletion("Please remove any remaining copy of my profile.")) # Critical case 05 regression test
        self.assertTrue(is_deletion("I want to erase all records and close my profile forever."))
        
        # Non-deletion requests (NOT_APPLICABLE)
        self.assertFalse(is_deletion("Please keep my account active, I only want to unsubscribe from marketing emails."))
        self.assertFalse(is_deletion("Please provide a downloadable copy of my data and purchase history."))
        self.assertFalse(is_deletion("I would like to export a copy of all my personal data."))
        self.assertFalse(is_deletion("Could you please update my phone number and fix a typo in my name?"))
        self.assertFalse(is_deletion("I am locked out, please help me reset the login credentials."))

    def test_pipe_log_parsing(self):
        # Test pipe format from operations_stream_*.log
        line = '2026-02-03T05:57:51Z | DEBUG | account_service | tr-0668675 | profile_lookup_completed | instance="account-6" pod="account-c133" region="eu-central-1" resource_id="account_1334" rows="1" http_status="200" duration_ms="14" correlation_key="m.moreau38@yahoo.com"'
        parsed = log_line(line)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.get('service'), 'account_service')
        self.assertEqual(parsed.get('trace_id'), 'tr-0668675')
        self.assertEqual(parsed.get('event'), 'profile_lookup_completed')
        self.assertEqual(parsed.get('resource_id'), 'account_1334')
        self.assertEqual(parsed.get('http_status'), '200')
        self.assertEqual(parsed.get('rows'), '1')
        self.assertEqual(parsed.get('correlation_key'), 'm.moreau38@yahoo.com')

    def test_security_audit_pipe_parsing(self):
        line = '2026-07-03T07:51:32Z | DEBUG | security_service | tr-5156005 | audit_record_read_completed | instance="security-6" pod="security-7e9b" region="eu-central-1" resource_id="audit_record_4413" integrity_check="passed" rows="1" correlation_key="user_211"'
        parsed = log_line(line)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.get('service'), 'security_service')
        self.assertEqual(parsed.get('resource_id'), 'audit_record_4413')
        self.assertEqual(parsed.get('correlation_key'), 'user_211')

    def test_token_extraction(self):
        text = "Contact priya.raman@gmail.com at +91 98765 73142 regarding user_07 and order ORD-1234"
        toks = tokens_in(text)
        self.assertIn("priya.raman@gmail.com", toks)
        self.assertIn("+919876573142", toks)
        self.assertIn("user_07", toks)

    def test_validator_on_output_csv(self):
        if os.path.exists('output.csv') and os.path.exists('dataset/deletion_requests.csv'):
            res = validate_submission('output.csv', 'dataset/deletion_requests.csv', 'dataset', check_evidence_ids=True)
            self.assertEqual(len(res.errors), 0, f"Validator errors found: {res.errors}")
            self.assertEqual(res.total_rows, 518)
            self.assertTrue(res.passed)

if __name__ == '__main__':
    unittest.main()
