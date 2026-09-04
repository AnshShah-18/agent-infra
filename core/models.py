from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    REJECTED = "rejected"
    APPROVED = "approved"


class Task(BaseModel):
    """A unit of work published by the Planner and consumed by the Executor."""

    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    goal_id: str  # parent goal this task belongs to
    description: str  # what the executor should do
    context: dict[str, Any] = Field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TaskResult(BaseModel):
    """Result published by the Executor after completing a task."""

    task_id: str
    goal_id: str
    task_description: str = ""
    output: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CriticFeedback(BaseModel):
    """Published by the Critic to tasks.rejected when a result scores < 7."""

    task_id: str
    goal_id: str
    task_description: str
    output: str       # the rejected output
    score: int
    feedback: str     # what the executor should improve
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class GoalPlan(BaseModel):
    """Published by the Planner after decomposition so the Summarizer knows the expected task count."""

    goal_id: str
    description: str
    task_count: int
    planned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class FinalSummary(BaseModel):
    """Published by the Summarizer once all approved results for a goal are synthesized."""

    goal_id: str
    description: str
    summary: str
    task_count: int
    summarized_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Goal(BaseModel):
    """A high-level user goal that the Planner decomposes into tasks."""

    goal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    description: str
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
