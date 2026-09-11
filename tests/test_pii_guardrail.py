import unittest

from compliance.pii_guardrail import find_pii, redact, PiiType, RiskLevel, PiiGuardrail, highest_risk


class TestPiiDetection(unittest.TestCase):
    def test_detects_email(self):
        findings = find_pii("contact me at ansh18june@gmail.com please")
        self.assertEqual([f.pii_type for f in findings], [PiiType.EMAIL])

    def test_detects_phone(self):
        findings = find_pii("call me at 312-478-1448 tomorrow")
        self.assertEqual([f.pii_type for f in findings], [PiiType.PHONE])

    def test_detects_ssn(self):
        findings = find_pii("SSN on file: 123-45-6789")
        self.assertEqual([f.pii_type for f in findings], [PiiType.SSN])

    def test_detects_valid_credit_card_via_luhn(self):
        # 4111111111111111 is the standard Luhn-valid Visa test number
        findings = find_pii("card number 4111 1111 1111 1111 on file")
        self.assertEqual([f.pii_type for f in findings], [PiiType.CREDIT_CARD])

    def test_rejects_luhn_invalid_digit_sequence_as_credit_card(self):
        # 16 digits but fails the Luhn checksum -> should not be flagged as a card
        findings = find_pii("reference number 1234 5678 9012 3456 for this ticket")
        self.assertNotIn(PiiType.CREDIT_CARD, [f.pii_type for f in findings])

    def test_detects_aws_access_key(self):
        findings = find_pii("export AWS_ACCESS_KEY_ID=AKIAABCDEFGHIJKLMNOP")
        self.assertIn(PiiType.AWS_ACCESS_KEY, [f.pii_type for f in findings])

    def test_detects_generic_api_key(self):
        findings = find_pii("Authorization: sk-abcdefghijklmnopqrstuvwxyz123456")
        self.assertIn(PiiType.API_KEY, [f.pii_type for f in findings])

    def test_clean_text_has_no_findings(self):
        findings = find_pii("The Critic agent retried three times before synthesis.")
        self.assertEqual(findings, [])

    def test_multiple_findings_do_not_overlap_and_are_ordered(self):
        text = "email ansh18june@gmail.com or call 312-478-1448"
        findings = find_pii(text)
        self.assertEqual([f.pii_type for f in findings], [PiiType.EMAIL, PiiType.PHONE])
        for a, b in zip(findings, findings[1:]):
            self.assertLessEqual(a.end, b.start)


class TestRedaction(unittest.TestCase):
    def test_redact_replaces_value_with_type_placeholder_only(self):
        text = "reach me at ansh18june@gmail.com"
        redacted, findings = redact(text)
        self.assertNotIn("ansh18june@gmail.com", redacted)
        self.assertIn("[REDACTED:EMAIL]", redacted)
        self.assertEqual(len(findings), 1)

    def test_redact_is_idempotent_on_already_redacted_text(self):
        text = "reach me at ansh18june@gmail.com"
        once, _ = redact(text)
        twice, findings_on_twice = redact(once)
        self.assertEqual(once, twice)
        self.assertEqual(findings_on_twice, [])

    def test_redact_preserves_surrounding_text(self):
        text = "Agent output: ansh18june@gmail.com was the requester."
        redacted, _ = redact(text)
        self.assertTrue(redacted.startswith("Agent output: "))
        self.assertTrue(redacted.endswith(" was the requester."))


class TestPiiGuardrail(unittest.TestCase):
    def test_high_risk_finding_blocks_by_default(self):
        guard = PiiGuardrail()  # default: block at HIGH
        _, findings, blocked = guard.check("SSN 123-45-6789 was provided")
        self.assertTrue(blocked)
        self.assertEqual(highest_risk(findings), RiskLevel.HIGH)

    def test_low_risk_finding_does_not_block_by_default(self):
        guard = PiiGuardrail()
        redacted, findings, blocked = guard.check("contact ansh18june@gmail.com")
        self.assertFalse(blocked)
        self.assertIn("[REDACTED:EMAIL]", redacted)

    def test_stricter_threshold_blocks_medium_risk_too(self):
        guard = PiiGuardrail(block_at_or_above=RiskLevel.MEDIUM)
        _, _, blocked = guard.check("call 312-478-1448")
        self.assertTrue(blocked)


if __name__ == "__main__":
    unittest.main()
