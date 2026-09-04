"""
CLI — submit a goal and tail results in real time.

Usage:
    python cli.py "Find the top 5 ML papers from last month and summarize them"
"""
from __future__ import annotations

import json
import sys
import threading
import time

from dotenv import load_dotenv
from loguru import logger

from config.topics import Topic
from core.kafka import KafkaConsumerLoop, KafkaPublisher, ensure_topics
from core.models import FinalSummary, Goal, TaskResult

load_dotenv()

GOAL_TOPIC = "goals.submitted"


def submit_and_watch(goal_text: str) -> None:
    ensure_topics([GOAL_TOPIC, Topic.TASKS_COMPLETED, Topic.GOALS_SUMMARIZED])

    publisher = KafkaPublisher()
    goal = Goal(description=goal_text)

    results: list[TaskResult] = []
    done = threading.Event()

    def handle_result(payload: dict) -> None:
        result = TaskResult(**payload)
        if result.goal_id == goal.goal_id:
            results.append(result)
            print(f"\n{'─'*60}")
            print(f"Task completed: {result.task_id[:8]}...")
            print(result.output)
            print(f"{'─'*60}")

    def handle_summary(payload: dict) -> None:
        summary = FinalSummary(**payload)
        if summary.goal_id == goal.goal_id:
            print(f"\n{'═'*60}")
            print(f"FINAL ANSWER ({summary.task_count} tasks synthesized)")
            print(f"{'─'*60}")
            print(summary.summary)
            print(f"{'═'*60}\n")
            done.set()

    result_loop = KafkaConsumerLoop(
        group_id=f"cli-{goal.goal_id[:8]}",
        topics=[Topic.TASKS_COMPLETED],
        handler=handle_result,
    )
    summary_loop = KafkaConsumerLoop(
        group_id=f"cli-summary-{goal.goal_id[:8]}",
        topics=[Topic.GOALS_SUMMARIZED],
        handler=handle_summary,
    )

    threading.Thread(target=result_loop.run, daemon=True).start()
    threading.Thread(target=summary_loop.run, daemon=True).start()

    publisher.publish(GOAL_TOPIC, goal, key=goal.goal_id)
    print(f"\nGoal submitted: {goal.goal_id}")
    print(f"Description: {goal_text}\n")
    print("Waiting for results (Ctrl+C to stop)...\n")

    try:
        while not done.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        result_loop.stop()
        summary_loop.stop()
        publisher.flush()
        print(f"\nTotal tasks completed: {len(results)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python cli.py \"<goal description>\"")
        sys.exit(1)
    submit_and_watch(" ".join(sys.argv[1:]))
