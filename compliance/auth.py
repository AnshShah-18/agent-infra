"""
auth.py — Role-based access control (RBAC) and authentication for agent-infra.

Design goals
------------
* Every agent-to-agent and user-to-agent call carries an identity (a signed
  JWT) and every privileged action is checked against an explicit
  role -> permission map, rather than being implicitly trusted.
* Fails closed: unknown roles, expired tokens, and missing permissions are
  all denied by default.
* No framework dependency. This module is plain Python + PyJWT so it can be
  wired into the existing FastAPI layer of agent-infra, or called directly
  from the agent orchestration code (Planner/Executor/Critic/Summarizer)
  before an agent is allowed to call a sensitive tool.

This is intentionally small enough to read end-to-end in one sitting, which
is the point: an auditor (or a hiring manager) should be able to verify what
it does just by reading it, not by trusting a README.
"""

from __future__ import annotations

import time
import enum
from dataclasses import dataclass
from typing import Optional

import jwt  # PyJWT


class Role(str, enum.Enum):
    VIEWER = "viewer"          # read-only: can view agent outputs, logs
    OPERATOR = "operator"      # can submit tasks, trigger agent runs
    ADMIN = "admin"            # can manage agents, rotate keys, read audit log


class Permission(str, enum.Enum):
    VIEW_OUTPUT = "view_output"
    SUBMIT_TASK = "submit_task"
    TRIGGER_AGENT_RUN = "trigger_agent_run"
    MANAGE_AGENTS = "manage_agents"
    READ_AUDIT_LOG = "read_audit_log"
    ROTATE_CREDENTIALS = "rotate_credentials"


# Explicit, reviewable mapping of what each role is allowed to do.
# Anything not listed here is denied by default (see has_permission()).
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset({Permission.VIEW_OUTPUT}),
    Role.OPERATOR: frozenset({
        Permission.VIEW_OUTPUT,
        Permission.SUBMIT_TASK,
        Permission.TRIGGER_AGENT_RUN,
    }),
    Role.ADMIN: frozenset({
        Permission.VIEW_OUTPUT,
        Permission.SUBMIT_TASK,
        Permission.TRIGGER_AGENT_RUN,
        Permission.MANAGE_AGENTS,
        Permission.READ_AUDIT_LOG,
        Permission.ROTATE_CREDENTIALS,
    }),
}


class AuthenticationError(Exception):
    """Raised when a token is missing, malformed, expired, or has a bad signature."""


class AuthorizationError(Exception):
    """Raised when a token is valid but the principal lacks the required permission."""


@dataclass(frozen=True)
class Principal:
    """The authenticated identity extracted from a verified token."""

    subject: str          # user id or agent id, e.g. "user:ansh" or "agent:critic"
    role: Role
    issued_at: int
    expires_at: int
    token_id: str = ""     # jti, used for audit-log correlation


class TokenIssuer:
    """
    Issues and verifies HS256 JWTs for internal service-to-service and
    user-to-service auth.

    In production this secret would come from a secrets manager / KMS, not
    a constructor argument — the constructor argument is what makes this
    testable without any external service.
    """

    def __init__(self, secret: str, issuer: str = "agent-infra", default_ttl_seconds: int = 900):
        if not secret or len(secret) < 16:
            raise ValueError("secret must be a non-trivial string (>=16 chars)")
        self._secret = secret
        self._issuer = issuer
        self._default_ttl = default_ttl_seconds
        self._counter = 0  # used only to make token ids unique in tests

    def issue(self, subject: str, role: Role, ttl_seconds: Optional[int] = None) -> str:
        if role not in ROLE_PERMISSIONS:
            raise ValueError(f"unknown role: {role!r}")
        now = int(time.time())
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        self._counter += 1
        payload = {
            "sub": subject,
            "role": role.value,
            "iss": self._issuer,
            "iat": now,
            "exp": now + ttl,
            "jti": f"{subject}-{now}-{self._counter}",
        }
        return jwt.encode(payload, self._secret, algorithm="HS256")

    def verify(self, token: str) -> Principal:
        if not token or not isinstance(token, str):
            raise AuthenticationError("missing token")
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                issuer=self._issuer,
                options={"require": ["exp", "iat", "sub", "role"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("token expired") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError(f"invalid token: {exc}") from exc

        try:
            role = Role(payload["role"])
        except ValueError as exc:
            raise AuthenticationError(f"unknown role in token: {payload.get('role')!r}") from exc

        return Principal(
            subject=payload["sub"],
            role=role,
            issued_at=payload["iat"],
            expires_at=payload["exp"],
            token_id=payload.get("jti", ""),
        )


def has_permission(role: Role, permission: Permission) -> bool:
    """Fails closed: an unmapped role has zero permissions."""
    return permission in ROLE_PERMISSIONS.get(role, frozenset())


def require_permission(principal: Principal, permission: Permission) -> None:
    """
    Raise AuthorizationError if `principal` may not perform `permission`.
    Call this at the top of every privileged function/endpoint/tool call.
    """
    if not has_permission(principal.role, permission):
        raise AuthorizationError(
            f"subject {principal.subject!r} with role {principal.role.value!r} "
            f"lacks permission {permission.value!r}"
        )
