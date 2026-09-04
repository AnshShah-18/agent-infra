"""
Critic Agent
------------
1. Consumes TaskResult events from tasks.completed
2. Uses DSPy CriticModule to evaluate result quality (score 1-10)
3. If score >= 7: publishes to tasks.approved
4. If score <  7: publishes CriticFeedback to tasks.rejected (executor retries)
5. Stores evaluation in Weaviate episodic memory
"""
from __future__ import annotations

import os
import signal
import sys

from dotenv import load_dotenv
from loguru import logger

from config.topics import ALL_TOPICS, Topic
from core.dspy_modules import CriticModule, configure_dspy
from core.kafka import KafkaConsumerLoop, KafkaPublisher, ensure_topics
from core.memory import MemoryClient
from core.models import CriticFeedback, TaskResult

load_dotenv()

AGENT_ID = "critic"
APPROVE_THRESHOLD = 7


class CriticAgent:
    def __init__(self) -> None:
        configure_dspy()
        self._publisher = KafkaPublisher()
        self._memory = MemoryClient()
        self._module = CriticModule()
        self._loop = KafkaConsumerLoop(
            group_id=os.getenv("CRITIC_CONSUMER_GROUP", "critic-group"),
            topics=[Topic.TASKS_COMPLETED],
            handler=self._handle_result,
        )

    def _handle_result(self, payload: dict) -> None:
        result = TaskResult(**payload)
        logger.info(f"Critic evaluating task {result.task_id}: {result.task_description[:60]}")

        score, reasoning, feedback = self._module(
            task_description=result.task_description,
            result=result.output,
        )
        logger.info(f"Task {result.task_id} scored {score}/10 — {reasoning[:80]}")

        if score >= APPROVE_THRESHOLD:
            self._publisher.publish(Topic.TASKS_APPROVED, result, key=result.goal_id)
            logger.info(f"  → Approved task {result.task_id}")
        else:
            fb = CriticFeedback(
                task_id=result.task_id,
                goal_id=result.goal_id,
                task_description=result.task_description,
                output=result.output,
                score=score,
                feedback=feedback,
            )
            self._publisher.publish(Topic.TASKS_REJECTED, fb, key=result.goal_id)
            logger.info(f"  → Rejected task {result.task_id} (score={score}): {feedback[:80]}")

        self._memory.store(
            agent_id=AGENT_ID,
            goal_id=result.goal_id,
            content=f"Task: {result.task_description}\nScore: {score}/10\nReasoning: {reasoning}\nFeedback: {feedback}",
        )

    def run(self) -> None:
        logger.info("Critic agent started")
        self._loop.run()

    def stop(self) -> None:
        self._loop.stop()
        self._publisher.flush()
        self._memory.close()


def main() -> None:
    ensure_topics(ALL_TOPICS)
    agent = CriticAgent()

    def _shutdown(sig, frame):
        logger.info("Shutting down critic...")
        agent.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    agent.run()


if __name__ == "__main__":
    main()
