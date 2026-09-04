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
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

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
def submit_goal(body: GoalRequest) -> dict:
    from core.models import Goal
    goal = Goal(description=body.description)
    pub = get_publisher()
    pub.publish("goals.submitted", goal, key=goal.goal_id)
    pub.flush()
    return {"goal_id": goal.goal_id, "description": goal.description}


@app.get("/goals/{goal_id}/status")
def goal_status(goal_id: str) -> dict[str, Any]:
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
def goal_tasks(goal_id: str) -> list[dict]:
    mem = get_memory()
    executor_entries = mem.fetch_all_by_goal(goal_id, agent_id="executor")
    critic_entries = mem.fetch_all_by_goal(goal_id, agent_id="critic")
    return _reconstruct_tasks(executor_entries, critic_entries)


@app.get("/goals/{goal_id}/summary")
def goal_summary(goal_id: str) -> dict:
    mem = get_memory()
    summary = mem.fetch_summary(goal_id)
    if summary is None:
        return {"goal_id": goal_id, "status": "pending", "summary": None}
    return {"goal_id": goal_id, "status": "complete", "summary": summary}


@app.get("/memory/search")
def memory_search(q: str, agent_id: str | None = None, limit: int = 5) -> list[dict]:
    if not q.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    mem = get_memory()
    return mem.search(query=q, agent_id=agent_id or None, limit=min(limit, 20))
