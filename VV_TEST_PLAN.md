# Verification & Validation Test Plan — Agent Infrastructure Compliance Layer

**Component:** RBAC/authentication (`compliance/auth.py`), audit logging (`compliance/audit_log.py`), PII/secret detection and redaction guardrail (`compliance/pii_guardrail.py`)
**Author:** Ansh Shah
**Status:** All test cases below are implemented and passing (`pytest tests/ -v`, 33/33 passed).

## 1. Purpose and scope

This document defines how the three compliance-layer modules were verified before being merged into agent-infra, using a risk-based approach: every requirement is assigned a risk level, and the depth of testing for that requirement is driven by that level (a HIGH-risk requirement — e.g. "an expired token must never be accepted" — gets both a positive and a negative test; a LOW-risk one may get a single test). This mirrors how verification effort is allocated in regulated software environments, scaled down to the size of a single-developer add-on.

In scope: unit-level verification of the three modules above, run in isolation with no external services (no real KMS, no real database — a temp file stands in for the audit log's storage). Out of scope for this pass: load/performance testing, and end-to-end testing against the live FastAPI service (this pass covers the compliance modules in isolation; `api.py` itself wires them into every endpoint and is exercised manually via `uvicorn api:app --reload` plus the full agent pipeline).

## 2. Risk classification method

Each requirement is scored on two axes, each Low/Medium/High:

- **Likelihood** — how likely is this failure mode to occur in normal or adversarial use (e.g., "attacker edits a log file directly" is more likely than "SHA-256 collision").
- **Severity** — what happens if it fails silently (e.g., "unauthorized role executes a privileged action" is more severe than "an email address is logged unredacted").

Risk = combination of the two, mapped to three tiers:

| Risk tier | Meaning | Verification depth required |
|---|---|---|
| High | Failure directly defeats the control's purpose (auth bypass, undetected log tampering, unredacted high-sensitivity PII reaching storage) | Positive + negative test, plus an explicit "attack simulation" test where applicable |
| Medium | Failure degrades the control but doesn't defeat it outright (e.g., a lower-sensitivity PII type missed) | Positive + at least one negative/edge test |
| Low | Failure is cosmetic or has a safe default | At least one test |

## 3. Requirements and traceability matrix

| Req ID | Requirement | Risk | Test case(s) | Result |
|---|---|---|---|---|
| AUTH-1 | A validly-signed, non-expired token with a known role must verify and yield the correct subject/role | Medium | `tests.test_auth.TestTokenIssuerAndVerification.test_valid_token_round_trips` | Pass |
| AUTH-2 | An expired token must be rejected | High | `test_expired_token_rejected` | Pass |
| AUTH-3 | A token whose signature was tampered with must be rejected | High | `test_tampered_signature_rejected` | Pass |
| AUTH-4 | A token signed with a different secret must be rejected (prevents forged tokens from another issuer) | High | `test_wrong_secret_rejected` | Pass |
| AUTH-5 | Issuing a token for an unrecognized role must fail at issue time, not silently succeed | Medium | `test_unknown_role_at_issue_time_rejected` | Pass |
| AUTH-6 | A missing/empty token must be rejected, not treated as anonymous-allowed | High | `test_missing_token_rejected` | Pass |
| AUTH-7 | Weak secrets (below a minimum length) must be rejected at construction | Medium | `test_short_secret_rejected_at_construction` | Pass |
| RBAC-1 | Viewer role may view output but not submit tasks | High | `test_viewer_can_view_but_not_submit` | Pass |
| RBAC-2 | Operator role may trigger runs but not manage agents (least privilege) | High | `test_operator_can_trigger_but_not_manage_agents` | Pass |
| RBAC-3 | Admin role has the full permission set (positive control) | Medium | `test_admin_has_full_permission_set` | Pass |
| RBAC-4 | An unauthorized action raises `AuthorizationError` rather than failing open | High | `test_require_permission_raises_authorization_error_when_denied` | Pass |
| RBAC-5 | An authorized action is allowed without raising | Medium | `test_require_permission_passes_silently_when_allowed` | Pass |
| AUDIT-1 | A fresh/empty audit log verifies as intact | Low | `tests.test_audit_log.TestAuditLogger.test_empty_log_verifies_clean` | Pass |
| AUDIT-2 | A single logged event is recorded with the correct sequence number and verifies | Medium | `test_single_entry_is_recorded_and_verifies` | Pass |
| AUDIT-3 | Multiple events chain correctly (each entry's `prev_hash` matches the prior entry's hash) | High | `test_entries_chain_in_order` | Pass |
| AUDIT-4 | Editing a field of a past entry after the fact is detected on verification (tamper-evidence) | High | `test_tampering_with_a_past_entry_is_detected` | Pass |
| AUDIT-5 | Deleting a past entry entirely is detected on verification | High | `test_deleting_a_past_entry_is_detected` | Pass |
| AUDIT-6 | The log is durable and continues its sequence/chain correctly across process restarts | Medium | `test_log_persists_across_logger_instances` | Pass |
| PII-1 | Email addresses are detected | Low | `tests.test_pii_guardrail.TestPiiDetection.test_detects_email` | Pass |
| PII-2 | Phone numbers are detected | Medium | `test_detects_phone` | Pass |
| PII-3 | SSNs are detected | High | `test_detects_ssn` | Pass |
| PII-4 | Credit-card-shaped numbers are checked against the Luhn algorithm before being flagged, to reduce false positives | Medium | `test_detects_valid_credit_card_via_luhn`, `test_rejects_luhn_invalid_digit_sequence_as_credit_card` | Pass |
| PII-5 | AWS access keys are detected | High | `test_detects_aws_access_key` | Pass |
| PII-6 | Generic API-key-shaped secrets (`sk-...`, `pk-...`, `rk-...`) are detected | High | `test_detects_generic_api_key` | Pass |
| PII-7 | Clean text with no PII produces no findings (no false positives on ordinary agent chatter) | Medium | `test_clean_text_has_no_findings` | Pass |
| PII-8 | Overlapping matches are resolved so a span isn't double-counted, and findings are returned in text order | Low | `test_multiple_findings_do_not_overlap_and_are_ordered` | Pass |
| PII-9 | Redaction replaces the value with a type-only placeholder and never leaks the original value | High | `test_redact_replaces_value_with_type_placeholder_only` | Pass |
| PII-10 | Redaction is idempotent (redacting already-redacted text is a no-op) | Low | `test_redact_is_idempotent_on_already_redacted_text` | Pass |
| PII-11 | Redaction preserves surrounding, non-sensitive text unchanged | Low | `test_redact_preserves_surrounding_text` | Pass |
| GUARD-1 | A HIGH-risk finding (e.g. SSN) blocks the call under the default policy | High | `test_high_risk_finding_blocks_by_default` | Pass |
| GUARD-2 | A LOW-risk finding (e.g. email) is redacted but does not block the call by default | Medium | `test_low_risk_finding_does_not_block_by_default` | Pass |
| GUARD-3 | The block threshold is configurable (a stricter policy can block MEDIUM-risk findings too) | Medium | `test_stricter_threshold_blocks_medium_risk_too` | Pass |

## 4. Test environment

- Python 3.11/3.12, pytest (the test cases are written as `unittest.TestCase` classes, which pytest collects and runs natively — no separate test-writing convention needed).
- Dependency: `PyJWT` for token signing/verification. No network, no external database, no real KMS — the audit log's backing store is a plain file (a temp file in tests), and the token secret is a constructor argument (a literal secrets-manager stand-in).
- Command: `pytest tests/ -v` from the repo root.

## 5. Results summary

33 test cases executed, 33 passed, 0 failed, 0 skipped, at the time this document was produced. Every High-risk requirement above has at least one passing negative test that exercises the failure mode directly (expired/tampered/wrong-secret tokens, denied authorization, tampered/deleted audit entries, high-risk PII).

## 6. Known limitations (carried into the system card)

- PII detection is regex + Luhn-based, not ML-based; it will miss PII that doesn't match a known pattern (e.g., a name alone, a non-US ID format) and can still false-positive on incidental 16-digit numbers that happen to be Luhn-valid. This is a deliberate tradeoff for determinism and testability, documented rather than hidden.
- The audit log's tamper-evidence (hash chaining) detects tampering after the fact; it does not by itself prevent someone with filesystem write access from replacing the entire file. Production deployment would pair this with write-once storage (e.g., S3 Object Lock) or shipping the log to a separate system in real time.
- Token revocation before natural expiry is not implemented (no denylist); tokens are deliberately short-lived (15-minute default TTL) to bound this.
