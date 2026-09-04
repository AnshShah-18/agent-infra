"""
Summarizer Agent
----------------
1. Consumes GoalPlan events from goals.planned to learn each goal's expected task count
2. Consumes approved TaskResult events from tasks.approved
3. Once all tasks for a goal are approved, uses DSPy SummarizerModule to synthesize
   a final answer and publishes it to goals.summarized
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
from core.dspy_modules import SummarizerModule, configure_dspy
from core.kafka import KafkaConsumerLoop, KafkaPublisher, ensure_topics
from core.memory import MemoryClient
from core.models import FinalSummary, GoalPlan, TaskResult

load_dotenv()

AGENT_ID = "summarizer"
STALE_TIMEOUT = 120  # seconds with no new results before force-synthesizing


class SummarizerAgent:
    def __init__(self) -> None:
        configure_dspy()
        self._publisher = KafkaPublisher()
        self._memory = MemoryClient()
        self._module = SummarizerModule()

        # shared state — both consumer threads write here
        self._lock = threading.Lock()
        self._expected: dict[str, int] = {}         # goal_id → expected task count
        self._descriptions: dict[str, str] = {}     # goal_id → goal description
        self._results: dict[str, list[str]] = {}    # goal_id → list of approved outputs
        self._last_update: dict[str, float] = {}    # goal_id → timestamp of last approved result

        self._plan_loop = KafkaConsumerLoop(
            group_id=os.getenv("SUMMARIZER_CONSUMER_GROUP", "summarizer-group") + "-plans",
            topics=[Topic.GOALS_PLANNED],
            handler=self._handle_plan,
        )
        self._result_loop = KafkaConsumerLoop(
            group_id=os.getenv("SUMMARIZER_CONSUMER_GROUP", "summarizer-group") + "-results",
            topics=[Topic.TASKS_APPROVED],
            handler=self._handle_approved,
        )

    def _handle_plan(self, payload: dict) -> None:
        plan = GoalPlan(**payload)
        logger.info(f"Summarizer learned goal {plan.goal_id[:8]} expects {plan.task_count} tasks")
        with self._lock:
            self._expected[plan.goal_id] = plan.task_count
            self._descriptions[plan.goal_id] = plan.description
            self._results.setdefault(plan.goal_id, [])
        self._maybe_summarize(plan.goal_id)

    def _handle_approved(self, payload: dict) -> None:
        result = TaskResult(**payload)
        logger.info(f"Summarizer received approved result for goal {result.goal_id[:8]}: task {result.task_id[:8]}")
        with self._lock:
            self._results.setdefault(result.goal_id, [])
            self._results[result.goal_id].append(result.output)
            self._last_update[result.goal_id] = time.monotonic()
        self._maybe_summarize(result.goal_id)

    def _maybe_summarize(self, goal_id: str, force: bool = False) -> None:
        with self._lock:
            expected = self._expected.get(goal_id)
            collected = list(self._results.get(goal_id, []))
            description = self._descriptions.get(goal_id, "")
            ready = (expected is not None and len(collected) >= expected) or (force and len(collected) > 0)

        if not ready:
            return

        if force:
            logger.warning(f"Goal {goal_id[:8]} stale — synthesizing with {len(collected)}/{expected} approved results (skipped task assumed permanent)")
        else:
            logger.info(f"All {len(collected)} tasks approved for goal {goal_id[:8]} — synthesizing final answer")
        summary_text = self._module(goal=description, results=collected)

        summary = FinalSummary(
            goal_id=goal_id,
            description=description,
            summary=summary_text,
            task_count=len(collected),
        )
        self._publisher.publish(Topic.GOALS_SUMMARIZED, summary, key=goal_id)

        self._memory.store(
            agent_id=AGENT_ID,
            goal_id=goal_id,
            content=f"Goal: {description}\nFinal Summary:\n{summary_text}",
        )

        print(f"\n{'═' * 70}")
        print(f"FINAL SUMMARY — goal {goal_id[:8]}")
        print(f"Goal: {description}")
        print(f"{'─' * 70}")
        print(summary_text)
        print(f"{'═' * 70}\n")

        # clear state so a resubmitted goal gets a fresh run
        with self._lock:
            self._expected.pop(goal_id, None)
            self._descriptions.pop(goal_id, None)
            self._results.pop(goal_id, None)

    def _stale_checker(self) -> None:
        while True:
            time.sleep(30)
            with self._lock:
                stale = [
                    gid for gid, results in self._results.items()
                    if results
                    and self._expected.get(gid) is not None
                    and len(results) < self._expected[gid]
                    and time.monotonic() - self._last_update.get(gid, 0) > STALE_TIMEOUT
                ]
            for goal_id in stale:
                self._maybe_summarize(goal_id, force=True)

    def run(self) -> None:
        logger.info("Summarizer agent started")
        plan_thread = threading.Thread(target=self._plan_loop.run, daemon=True)
        plan_thread.start()
        stale_thread = threading.Thread(target=self._stale_checker, daemon=True)
        stale_thread.start()
        self._result_loop.run()

    def stop(self) -> None:
        self._plan_loop.stop()
        self._result_loop.stop()
        self._publisher.flush()
        self._memory.close()


def main() -> None:
    ensure_topics(ALL_TOPICS)
    agent = SummarizerAgent()

    def _shutdown(sig, frame):
        logger.info("Shutting down summarizer...")
        agent.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    agent.run()


if __name__ == "__main__":
    main()
