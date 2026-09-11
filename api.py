"""
Agent Infrastructure REST API
------------------------------
Run:  uvicorn api:app --reload --port 8000
"""
from __future__ import annotations

import hashlib
import os
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from compliance.auth import (
    AuthenticationError,
    AuthorizationError,
    Permission,
    Principal,
    Role,
    TokenIssuer,
    require_permission,
)
from compliance.audit_log import AuditLogger, Outcome
from compliance.pii_guardrail import PiiGuardrail, RiskLevel

load_dotenv()

# ---------------------------------------------------------------------------
# Compliance layer: auth/RBAC, audit logging, PII guardrail
# ---------------------------------------------------------------------------

_JWT_SECRET = os.getenv("AGENT_INFRA_JWT_SECRET", "")
if not _JWT_SECRET:
    # Fails loud rather than silently signing tokens with a predictable
    # default secret. Set AGENT_INFRA_JWT_SECRET in your .env (32+ chars).
    raise RuntimeError(
        "AGENT_INFRA_JWT_SECRET is not set. Add it to your .env (32+ characters)."
    )

_ISSUER = TokenIssuer(secret=_JWT_SECRET, issuer="agent-infra")
AUDIT = AuditLogger(path=os.getenv("AUDIT_LOG_PATH", "audit_log.jsonl"))
PII_GUARD = PiiGuardrail(block_at_or_above=RiskLevel.HIGH)


def get_current_principal(authorization: str = Header(default="")) -> Principal:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return _ISSUER.verify(token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))


def require(permission: Permission):
    def _dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        try:
            require_permission(principal, permission)
        except AuthorizationError as exc:
            AUDIT.log_event(principal.subject, permission.value, "endpoint", Outcome.DENIED,
                             {"reason": str(exc)})
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
        return principal
    return _dependency


# ---------------------------------------------------------------------------
# Lazy singletons
# ---------------------------------------------------------------------------

_memory = None
_publisher = None


def get_memory():
    global _memory
    if _memory is None:
        from core.memory import MemoryClient
        _memory = MemoryClient()
    return _memory


def get_publisher():
    global _publisher
    if _publisher is None:
        from core.kafka import KafkaPublisher
        _publisher = KafkaPublisher()
    return _publisher


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    if _memory:
        _memory.close()
    if _publisher:
        _publisher.flush()


app = FastAPI(title="Agent Infrastructure API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class GoalRequest(BaseModel):
    description: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _task_id(description: str) -> str:
    return hashlib.md5(description.encode()).hexdigest()[:12]


def _parse_planner_entry(content: str) -> int:
    """Count expected tasks from a planner memory entry."""
    count = 0
    in_tasks = False
    for line in content.splitlines():
        if line.strip() == "Tasks:":
            in_tasks = True
        elif in_tasks and line.startswith("- "):
            count += 1
        elif in_tasks and line and not line.startswith("- "):
            break
    return count


def _reconstruct_tasks(executor_entries: list[dict], critic_entries: list[dict]) -> list[dict]:
    tasks: dict[str, dict] = {}

    for entry in executor_entries:
        content = entry["content"]
        if not content.startswith("Task: "):
            continue
        result_sep = "\nResult: "
        ri = content.find(result_sep)
        if ri == -1:
            desc, output = content[6:], ""
        else:
            desc = content[6:ri]
            output = content[ri + len(result_sep):]
        tid = _task_id(desc)
        if tid in tasks:
            tasks[tid]["retry_count"] += 1
            tasks[tid]["output"] = output  # keep latest
        else:
            tasks[tid] = {
                "id": tid,
                "description": desc,
                "status": "running",
                "output": output,
                "score": None,
                "feedback": "",
                "retry_count": 0,
            }

    for entry in critic_entries:
        content = entry["content"]
        if not content.startswith("Task: "):
            continue
        score_sep = "\nScore: "
        si = content.find(score_sep)
        if si == -1:
            continue
        desc = content[6:si]
        tid = _task_id(desc)

        rest = content[si + len(score_sep):]
        score_line_end = rest.find("\n")
        score_str = rest[:score_line_end] if score_line_end != -1 else rest
        try:
            score = int(score_str.replace("/10", "").strip())
        except ValueError:
            score = 0

        feedback = ""
        fi = content.find("\nFeedback: ")
        if fi != -1:
            feedback = content[fi + 11:]

        approved = score >= 7
        if tid in tasks:
            existing = tasks[tid]
            # Keep the best score seen (approved supersedes rejected)
            if existing["score"] is None or score > existing["score"]:
                existing["score"] = score
                existing["feedback"] = feedback
                existing["status"] = "approved" if approved else "rejected"
        else:
            tasks[tid] = {
                "id": tid,
                "description": desc,
                "status": "approved" if approved else "rejected",
                "output": "",
                "score": score,
                "feedback": feedback,
                "retry_count": 0,
            }

    return list(tasks.values())


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    kafka_ok = False
    weaviate_ok = False

    try:
        from confluent_kafka.admin import AdminClient
        admin = AdminClient({"bootstrap.servers": os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")})
        admin.list_topics(timeout=3)
        kafka_ok = True
    except Exception:
        pass

    try:
        import weaviate
        client = weaviate.connect_to_local(
            host=os.getenv("WEAVIATE_HOST", "localhost"),
            port=int(os.getenv("WEAVIATE_PORT", "8080")),
            skip_init_checks=True,
        )
        weaviate_ok = client.is_ready()
        client.close()
    except Exception:
        pass

    return {"kafka": kafka_ok, "weaviate": weaviate_ok}


@app.post("/goals", status_code=201)
def submit_goal(body: GoalRequest,
                 principal: Principal = Depends(require(Permission.SUBMIT_TASK))) -> dict:
    from core.models import Goal

    # Redact PII/secrets before the goal description is ever published to
    # Kafka or stored in Weaviate memory. High-risk findings (SSNs, API
    # keys, etc.) block the submission outright rather than merely redacting.
    redacted_description, findings, blocked = PII_GUARD.check(body.description)
    if blocked:
        AUDIT.log_event(principal.subject, "submit_goal", "goal", Outcome.DENIED,
                         {"reason": "high-risk PII in goal description",
                          "finding_types": [f.pii_type.value for f in findings]})
        raise HTTPException(status_code=422, detail="goal description contains high-risk PII and was blocked")

    goal = Goal(description=redacted_description)
    pub = get_publisher()
    pub.publish("goals.submitted", goal, key=goal.goal_id)
    pub.flush()

    AUDIT.log_event(principal.subject, "submit_goal", f"goal:{goal.goal_id}", Outcome.SUCCESS,
                     {"pii_findings": [f.pii_type.value for f in findings]})
    return {"goal_id": goal.goal_id, "description": goal.description}


@app.get("/goals/{goal_id}/status")
def goal_status(goal_id: str,
                 principal: Principal = Depends(require(Permission.VIEW_OUTPUT))) -> dict[str, Any]:
    mem = get_memory()
    counts = mem.count_by_goal(goal_id)

    # Parse expected task count from planner entry
    task_count = 0
    planner_entries = mem.fetch_all_by_goal(goal_id, agent_id="planner")
    for e in planner_entries:
        n = _parse_planner_entry(e["content"])
        if n > task_count:
            task_count = n

    executor_count = counts.get("executor", 0)
    critic_count = counts.get("critic", 0)
    summarizer_count = counts.get("summarizer", 0)

    # Reconstruct approved count from critic entries
    approved = 0
    critic_entries = mem.fetch_all_by_goal(goal_id, agent_id="critic")
    for e in critic_entries:
        si = e["content"].find("\nScore: ")
        if si != -1:
            rest = e["content"][si + 8:]
            line_end = rest.find("\n")
            score_str = rest[:line_end] if line_end != -1 else rest
            try:
                if int(score_str.replace("/10", "").strip()) >= 7:
                    approved += 1
            except ValueError:
                pass

    retries = max(0, executor_count - task_count) if task_count else 0
    progress = round(approved / task_count * 100) if task_count else 0

    def stage_status(count: int, expected: int | None = None) -> str:
        if count == 0:
            return "pending"
        if expected and count >= expected:
            return "done"
        return "active"

    return {
        "goal_id": goal_id,
        "task_count": task_count,
        "approved": approved,
        "retries": retries,
        "progress": progress,
        "stages": {
            "planner":    {"status": stage_status(counts.get("planner", 0), 1),    "count": counts.get("planner", 0)},
            "executor":   {"status": stage_status(executor_count, task_count),      "count": executor_count},
            "critic":     {"status": stage_status(critic_count, task_count),        "count": critic_count},
            "summarizer": {"status": stage_status(summarizer_count, 1),             "count": summarizer_count},
        },
    }


@app.get("/goals/{goal_id}/tasks")
def goal_tasks(goal_id: str,
                principal: Principal = Depends(require(Permission.VIEW_OUTPUT))) -> list[dict]:
    mem = get_memory()
    executor_entries = mem.fetch_all_by_goal(goal_id, agent_id="executor")
    critic_entries = mem.fetch_all_by_goal(goal_id, agent_id="critic")
    return _reconstruct_tasks(executor_entries, critic_entries)


@app.get("/goals/{goal_id}/summary")
def goal_summary(goal_id: str,
                   principal: Principal = Depends(require(Permission.VIEW_OUTPUT))) -> dict:
    mem = get_memory()
    summary = mem.fetch_summary(goal_id)
    if summary is None:
        return {"goal_id": goal_id, "status": "pending", "summary": None}
    return {"goal_id": goal_id, "status": "complete", "summary": summary}


@app.get("/memory/search")
def memory_search(q: str, agent_id: str | None = None, limit: int = 5,
                    principal: Principal = Depends(require(Permission.VIEW_OUTPUT))) -> list[dict]:
    if not q.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    mem = get_memory()
    results = mem.search(query=q, agent_id=agent_id or None, limit=min(limit, 20))
    # Redact PII/secrets from stored content before it's ever returned to a client.
    for r in results:
        if isinstance(r.get("content"), str):
            redacted_content, _, _ = PII_GUARD.check(r["content"])
            r["content"] = redacted_content
    return results


@app.get("/audit-log")
def read_audit_log(principal: Principal = Depends(require(Permission.READ_AUDIT_LOG))) -> dict:
    AUDIT.log_event(principal.subject, "read_audit_log", "audit_log", Outcome.SUCCESS, {})
    ok, reason = AUDIT.verify_chain()
    return {"chain_intact": ok, "reason": reason, "entries": [e.__dict__ for e in AUDIT.read_all()]}
