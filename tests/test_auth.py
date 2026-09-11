import time
import unittest

from compliance.auth import (
    TokenIssuer, Role, Permission, Principal,
    AuthenticationError, AuthorizationError,
    has_permission, require_permission,
)


class TestTokenIssuerAndVerification(unittest.TestCase):
    def setUp(self):
        self.issuer = TokenIssuer(secret="unit-test-secret-value-32bytesok", issuer="agent-infra-test")

    def test_valid_token_round_trips(self):
        token = self.issuer.issue("agent:executor", Role.OPERATOR)
        principal = self.issuer.verify(token)
        self.assertEqual(principal.subject, "agent:executor")
        self.assertEqual(principal.role, Role.OPERATOR)

    def test_expired_token_rejected(self):
        token = self.issuer.issue("agent:executor", Role.OPERATOR, ttl_seconds=-1)
        with self.assertRaises(AuthenticationError):
            self.issuer.verify(token)

    def test_tampered_signature_rejected(self):
        token = self.issuer.issue("agent:executor", Role.OPERATOR)
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        with self.assertRaises(AuthenticationError):
            self.issuer.verify(tampered)

    def test_wrong_secret_rejected(self):
        token = self.issuer.issue("agent:executor", Role.OPERATOR)
        other_issuer = TokenIssuer(secret="a-completely-different-secret-32b", issuer="agent-infra-test")
        with self.assertRaises(AuthenticationError):
            other_issuer.verify(token)

    def test_unknown_role_at_issue_time_rejected(self):
        with self.assertRaises(ValueError):
            self.issuer.issue("agent:executor", "not-a-real-role")  # type: ignore[arg-type]

    def test_missing_token_rejected(self):
        with self.assertRaises(AuthenticationError):
            self.issuer.verify("")

    def test_short_secret_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            TokenIssuer(secret="short")


class TestRoleBasedAccessControl(unittest.TestCase):
    def test_viewer_can_view_but_not_submit(self):
        self.assertTrue(has_permission(Role.VIEWER, Permission.VIEW_OUTPUT))
        self.assertFalse(has_permission(Role.VIEWER, Permission.SUBMIT_TASK))

    def test_operator_can_trigger_but_not_manage_agents(self):
        self.assertTrue(has_permission(Role.OPERATOR, Permission.TRIGGER_AGENT_RUN))
        self.assertFalse(has_permission(Role.OPERATOR, Permission.MANAGE_AGENTS))

    def test_admin_has_full_permission_set(self):
        for perm in Permission:
            self.assertTrue(has_permission(Role.ADMIN, perm))

    def test_require_permission_raises_authorization_error_when_denied(self):
        principal = Principal(subject="user:ansh", role=Role.VIEWER,
                               issued_at=int(time.time()), expires_at=int(time.time()) + 900)
        with self.assertRaises(AuthorizationError):
            require_permission(principal, Permission.ROTATE_CREDENTIALS)

    def test_require_permission_passes_silently_when_allowed(self):
        principal = Principal(subject="user:ansh", role=Role.ADMIN,
                               issued_at=int(time.time()), expires_at=int(time.time()) + 900)
        require_permission(principal, Permission.ROTATE_CREDENTIALS)  # should not raise


if __name__ == "__main__":
    unittest.main()
