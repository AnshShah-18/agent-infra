"""
pii_guardrail.py — Regex-based PII/secret detection and redaction guardrail.

Wraps (a) the goal/prompt going into the agent pipeline and (b) results
coming back out, so PII and credential-shaped strings are redacted before
they are published to Kafka, stored in Weaviate episodic memory, or shown
on the observability dashboard. This is a guardrail in the literal sense
used in the AI-safety literature: a fixed check that runs on every call,
independent of what the model itself decides to do.

Detection is intentionally simple (regex, no ML/NLP dependency) so that it
is deterministic, fast, has no model-drift risk, and is easy to unit test
exhaustively — which matters a great deal for a control that is supposed
to be verifiable, not just "usually works."

This is NOT a claim of perfect PII detection (no regex-only approach is).
See the system card for that limitation stated explicitly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class PiiType(str, Enum):
    EMAIL = "email"
    PHONE = "phone"
    SSN = "ssn"
    CREDIT_CARD = "credit_card"
    API_KEY = "api_key"
    AWS_ACCESS_KEY = "aws_access_key"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


RISK_BY_TYPE = {
    PiiType.EMAIL: RiskLevel.LOW,
    PiiType.PHONE: RiskLevel.MEDIUM,
    PiiType.SSN: RiskLevel.HIGH,
    PiiType.CREDIT_CARD: RiskLevel.HIGH,
    PiiType.API_KEY: RiskLevel.HIGH,
    PiiType.AWS_ACCESS_KEY: RiskLevel.HIGH,
}

# Order matters: more specific patterns (API keys, AWS keys) are checked
# before more general ones so a token isn't double-classified.
_PATTERNS = [
    (PiiType.AWS_ACCESS_KEY, re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (PiiType.API_KEY, re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9]{20,}\b")),
    (PiiType.SSN, re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    (
        PiiType.CREDIT_CARD,
        re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
    ),
    (PiiType.EMAIL, re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    (
        PiiType.PHONE,
        re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    ),
]


@dataclass(frozen=True)
class Finding:
    pii_type: PiiType
    risk: RiskLevel
    start: int
    end: int
    # Deliberately no `value` field: findings are safe to log/audit without
    # re-exposing the PII they describe.


def _luhn_valid(digits: str) -> bool:
    """Luhn checksum, used to cut down false positives on the credit-card regex."""
    total = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        n = int(d)
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def find_pii(text: str) -> list:
    """Scan text and return every PII/secret match found, most specific first."""
    findings = []
    claimed = []  # spans already attributed to a more specific type

    def overlaps(start: int, end: int) -> bool:
        return any(s < end and start < e for s, e in claimed)

    for pii_type, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            start, end = m.span()
            if overlaps(start, end):
                continue
            if pii_type == PiiType.CREDIT_CARD:
                digits = re.sub(r"[ -]", "", m.group())
                if len(digits) not in (13, 14, 15, 16) or not _luhn_valid(digits):
                    continue
            findings.append(Finding(pii_type, RISK_BY_TYPE[pii_type], start, end))
            claimed.append((start, end))

    findings.sort(key=lambda f: f.start)
    return findings


def redact(text: str, findings: Optional[list] = None):
    """
    Returns (redacted_text, findings). Each match is replaced with a
    fixed-width placeholder identifying only the *type* found, e.g.
    "[REDACTED:EMAIL]", never a partial or hashed version of the value
    (a partial mask can itself leak enough to re-identify a person).
    """
    if findings is None:
        findings = find_pii(text)
    out = []
    cursor = 0
    for f in findings:
        out.append(text[cursor:f.start])
        out.append(f"[REDACTED:{f.pii_type.value.upper()}]")
        cursor = f.end
    out.append(text[cursor:])
    return "".join(out), findings


def highest_risk(findings: list):
    order = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
    if not findings:
        return None
    return max((f.risk for f in findings), key=lambda r: order[r])


class PiiGuardrail:
    """
    Call-site wrapper: `guard.check(text)` returns the redacted text and
    lets the caller decide policy (e.g. block the call entirely if a
    HIGH-risk finding like an SSN or API key shows up, but allow LOW-risk
    findings like an email through in redacted form).
    """

    def __init__(self, block_at_or_above: RiskLevel = RiskLevel.HIGH):
        self._block_threshold = block_at_or_above

    def check(self, text: str):
        """Returns (redacted_text, findings, blocked)."""
        findings = find_pii(text)
        redacted_text, _ = redact(text, findings)
        risk = highest_risk(findings)
        order = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
        blocked = risk is not None and order[risk] >= order[self._block_threshold]
        return redacted_text, findings, blocked
