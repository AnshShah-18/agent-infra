"""
Planner Agent
-------------
1. Accepts a Goal (via submit_goal() or the CLI)
2. Searches episodic memory for relevant past context
3. Uses DSPy PlannerModule to decompose the goal into subtasks
4. Publishes each subtask as a Task event to topics.TASKS_ASSIGNED
5. Stores the decomposition in episodic memory
"""
from __future__ import annotations

import os
import signal
import sys
import threading

from dotenv import load_dotenv
from loguru import logger

from config.topics import ALL_TOPICS, Topic
from core.dspy_modules import PlannerModule, configure_dspy
from core.kafka import KafkaConsumerLoop, KafkaPublisher, ensure_topics
from core.memory import MemoryClient
from core.models import Goal, GoalPlan, Task

load_dotenv()


AGENT_ID = "planner"
GOAL_TOPIC = "goals.submitted"  # planner listens here for new goals


class PlannerAgent:
    def __init__(self) -> None:
        configure_dspy()
        self._publisher = KafkaPublisher()
        self._memory = MemoryClient()
        self._module = PlannerModule()
        self._loop = KafkaConsumerLoop(
            group_id=os.getenv("PLANNER_CONSUMER_GROUP", "planner-group"),
            topics=[GOAL_TOPIC],
            handler=self._handle_goal,
        )

    def _handle_goal(self, payload: dict) -> None:
        goal = Goal(**payload)
        logger.info(f"Planner received goal: {goal.goal_id} — {goal.description[:80]}")

        # retrieve relevant past memory
        memories = self._memory.search(query=goal.description, agent_id=AGENT_ID, limit=3)
        past_context = "\n".join(m["content"] for m in memories) if memories else ""

        # decompose into subtasks
        subtask_descriptions = self._module(goal=goal.description, past_context=past_context)
        logger.info(f"Decomposed into {len(subtask_descriptions)} subtasks")

        # publish each subtask
        for desc in subtask_descriptions:
            task = Task(goal_id=goal.goal_id, description=desc)
            self._publisher.publish(Topic.TASKS_ASSIGNED, task, key=goal.goal_id)
            logger.info(f"  → Published task {task.task_id}: {desc[:60]}")

        # publish goal plan so the summarizer knows the expected task count
        plan = GoalPlan(goal_id=goal.goal_id, description=goal.description, task_count=len(subtask_descriptions))
        self._publisher.publish(Topic.GOALS_PLANNED, plan, key=goal.goal_id)
        logger.info(f"  → Published GoalPlan: {len(subtask_descriptions)} tasks for goal {goal.goal_id[:8]}")

        # store decomposition in memory
        self._memory.store(
            agent_id=AGENT_ID,
            goal_id=goal.goal_id,
            content=f"Goal: {goal.description}\nTasks:\n" + "\n".join(f"- {d}" for d in subtask_descriptions),
        )

    def submit_goal(self, description: str) -> Goal:
        """Helper to inject a goal directly (useful for testing/CLI)."""
        goal = Goal(description=description)
        self._publisher.publish(GOAL_TOPIC, goal, key=goal.goal_id)
        logger.info(f"Goal submitted: {goal.goal_id}")
        return goal

    def run(self) -> None:
        logger.info("Planner agent started")
        self._loop.run()

    def stop(self) -> None:
        self._loop.stop()
        self._publisher.flush()
        self._memory.close()


def main() -> None:
    ensure_topics(ALL_TOPICS + [GOAL_TOPIC])
    agent = PlannerAgent()

    def _shutdown(sig, frame):
        logger.info("Shutting down planner...")
        agent.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # if a goal was passed as CLI arg, submit it then start listening
    if len(sys.argv) > 1:
        goal_text = " ".join(sys.argv[1:])
        t = threading.Thread(target=lambda: agent.submit_goal(goal_text), daemon=True)
        t.start()

    agent.run()


if __name__ == "__main__":
    main()
