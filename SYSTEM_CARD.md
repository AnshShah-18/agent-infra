# System Card — Agent Infrastructure Compliance Layer

A short, honest description of what this add-on does and does not do, in the style of a responsible-AI system card. This covers only the compliance layer (auth/RBAC, audit logging, PII guardrail) — not the underlying multi-agent orchestration system it protects.

## Intended use

Gate and record privileged actions on the agent-infra REST API (`api.py`), which fronts the multi-agent pipeline (Planner/Executor/Critic/Summarizer): verify who is calling a sensitive endpoint, deny actions the caller's role doesn't permit, redact PII/secrets from goal descriptions and memory-search results before they are stored or displayed, and keep a tamper-evident record of what happened for later review.

## What the guardrails actually do

- **Authentication/RBAC** (`compliance/auth.py`): every API call carries a signed, short-lived (15 min default) bearer token identifying a subject and a role. Three roles (viewer/operator/admin) map to an explicit, reviewable set of permissions. Unknown roles or unmapped permissions are denied by default (fail closed), not allowed by default. Every endpoint in `api.py` except `/health` requires a token.
- **Audit logging** (`compliance/audit_log.py`): every privileged action (attempted or completed) is appended to a hash-chained, append-only log (`audit_log.jsonl`). Editing or deleting a past entry is detectable by recomputing the chain (`GET /audit-log` reports `chain_intact`) — it does not prevent tampering by someone with raw filesystem access, only makes it detectable.
- **PII/secret guardrail** (`compliance/pii_guardrail.py`): scans goal descriptions submitted via `POST /goals` and results returned via `GET /memory/search` for emails, phone numbers, SSNs, Luhn-valid credit card numbers, AWS access keys, and generic API-key-shaped strings. Matches are redacted to a type-only placeholder before storage/display. High-risk categories (SSN, card numbers, API/AWS keys) block the request outright at submission time rather than merely redacting it.

## Limitations

- PII detection is pattern-based, not a trained model — it will miss PII in formats it doesn't recognize (e.g., non-US ID numbers, names in free text) and is not a substitute for a dedicated DLP system in a regulated production deployment.
- The audit log's integrity guarantee is *tamper-evidence*, not *tamper-prevention*: it tells you after the fact that something was altered, and does not stop someone with write access to the file from altering it.
- No token revocation list exists yet; compromised tokens are mitigated by short expiry, not instant revocation.
- This layer governs actions that pass through the REST API. It does not by itself constrain what the underlying LLM (via the Claude/Anthropic API) generates inside the agent pipeline — that is the job of the existing LLM-as-judge quality gate in the Critic agent, which this layer complements rather than replaces.

## Human oversight

The audit log is designed to be read by a human reviewer (`GET /audit-log`, restricted to the `read_audit_log` permission — admin role only), not only by another automated system, so that access and denied actions are visible to a person, not just logged into a system nobody looks at.

## Testing

See `VV_TEST_PLAN.md` for the full risk-classified test plan and traceability matrix (33/33 tests passing at time of writing — run with `pytest tests/ -v`).
