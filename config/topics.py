from enum import Enum


class Topic(str, Enum):
    TASKS_ASSIGNED = "tasks.assigned"
    TASKS_COMPLETED = "tasks.completed"
    TASKS_REJECTED = "tasks.rejected"
    TASKS_APPROVED = "tasks.approved"
    GOALS_PLANNED = "goals.planned"
    GOALS_SUMMARIZED = "goals.summarized"


ALL_TOPICS = [t.value for t in Topic]
