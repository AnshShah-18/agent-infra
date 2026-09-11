"""
issue_token.py — CLI helper to mint a bearer token for the agent-infra API.

Usage:
    python -m compliance.issue_token --subject user:ansh --role admin
    python -m compliance.issue_token --subject agent:planner --role operator --ttl 3600

Then call the API with:
    curl -H "Authorization: Bearer <token>" http://localhost:8000/goals/...

Requires AGENT_INFRA_JWT_SECRET to be set in the environment (same value
the API server itself uses — see .env).
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from compliance.auth import Role, TokenIssuer


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Issue a signed bearer token for agent-infra.")
    parser.add_argument("--subject", required=True, help='e.g. "user:ansh" or "agent:planner"')
    parser.add_argument("--role", required=True, choices=[r.value for r in Role])
    parser.add_argument("--ttl", type=int, default=900, help="token lifetime in seconds (default 900)")
    args = parser.parse_args()

    secret = os.getenv("AGENT_INFRA_JWT_SECRET", "")
    if not secret:
        print("AGENT_INFRA_JWT_SECRET is not set in your environment/.env", file=sys.stderr)
        return 1

    issuer = TokenIssuer(secret=secret, issuer="agent-infra")
    token = issuer.issue(args.subject, Role(args.role), ttl_seconds=args.ttl)
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
