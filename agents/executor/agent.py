"""
Executor Agent
--------------
1. Consumes Task events from topics.TASKS_ASSIGNED
2. Searches episodic memory for relevant past results (avoids repeat work)
3. Uses DSPy ExecutorModule to complete the task
4. Publishes TaskResult to topics.TASKS_COMPLETED
5. Stores result in episodic memory
"""
from __future__ import annotations

import os
import signal
import sys
import threading
import time

from dotenv import load_dotenv
from loguru import logger

from config.topics import ALL_TOPICS, Topic
from core.dspy_modules import ExecutorModule, configure_dspy
from core.kafka import KafkaConsumerLoop, KafkaPublisher, ensure_topics
from core.memory import MemoryClient
from core.models import CriticFeedback, Task, TaskResult

load_dotenv()


AGENT_ID = "executor"
MAX_RETRIES = 3


class ExecutorAgent:
    def __init__(self) -> None:
        self._retry_counts: dict[str, int] = {}  # task_id → rejection count
        configure_dspy()
        self._publisher = KafkaPublisher()
        self._memory = MemoryClient()
        self._module = ExecutorModule()
        self._loop = KafkaConsumerLoop(
            group_id=os.getenv("EXECUTOR_CONSUMER_GROUP", "executor-group"),
            topics=[Topic.TASKS_ASSIGNED],
            handler=self._handle_task,
        )
        self._retry_loop = KafkaConsumerLoop(
            group_id=os.getenv("EXECUTOR_CONSUMER_GROUP", "executor-group") + "-retry",
            topics=[Topic.TASKS_REJECTED],
            handler=self._handle_rejection,
        )

    def _execute_and_publish(self, task_id: str, goal_id: str, description: str, past_context: str) -> None:
        output = self._module(task_description=description, past_context=past_context)
        logger.info(f"Task {task_id} executed ({len(output)} chars)")
        result = TaskResult(
            task_id=task_id,
            goal_id=goal_id,
            task_description=description,
            output=output,
            metadata={"agent_id": AGENT_ID},
        )
        self._publisher.publish(Topic.TASKS_COMPLETED, result, key=goal_id)
        self._memory.store(
            agent_id=AGENT_ID,
            goal_id=goal_id,
            content=f"Task: {description}\nResult: {output}",
        )

    def _handle_task(self, payload: dict) -> None:
        task = Task(**payload)
        logger.info(f"Executor received task {task.task_id}: {task.description[:80]}")
        memories = self._memory.search(query=task.description, agent_id=AGENT_ID, limit=3)
        past_context = "\n".join(m["content"] for m in memories) if memories else ""
        if past_context:
            logger.debug(f"Found {len(memories)} relevant memories for task {task.task_id}")
        self._execute_and_publish(task.task_id, task.goal_id, task.description, past_context)

    def _handle_rejection(self, payload: dict) -> None:
        time.sleep(5)
        fb = CriticFeedback(**payload)
        count = self._retry_counts.get(fb.task_id, 0) + 1
        self._retry_counts[fb.task_id] = count
        if count > MAX_RETRIES:
            logger.warning(f"Task {fb.task_id} rejected {count} times — giving up (max={MAX_RETRIES})")
            return
        logger.info(f"Executor retrying rejected task {fb.task_id} (attempt {count}/{MAX_RETRIES}, score={fb.score}): {fb.feedback[:80]}")
        memories = self._memory.search(query=fb.task_description, agent_id=AGENT_ID, limit=3)
        past_context = "\n".join(m["content"] for m in memories) if memories else ""
        past_context = f"Previous attempt was rejected (score {fb.score}/10).\nCritic feedback: {fb.feedback}\nPrevious output: {fb.output}\n\n{past_context}".strip()
        self._execute_and_publish(fb.task_id, fb.goal_id, fb.task_description, past_context)

    def run(self) -> None:
        logger.info("Executor agent started")
        retry_thread = threading.Thread(target=self._retry_loop.run, daemon=True)
        retry_thread.start()
        self._loop.run()

    def stop(self) -> None:
        self._loop.stop()
        self._retry_loop.stop()
        self._publisher.flush()
        self._memory.close()


def main() -> None:
    ensure_topics(ALL_TOPICS)
    agent = ExecutorAgent()

    def _shutdown(sig, frame):
        logger.info("Shutting down executor...")
        agent.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    agent.run()


if __name__ == "__main__":
    main()
