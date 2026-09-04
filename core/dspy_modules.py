from __future__ import annotations

import os

import dspy


def configure_dspy() -> None:
    lm = dspy.LM(
        model=f"anthropic/{os.getenv('DSPY_MODEL', 'claude-haiku-4-5')}",
        max_tokens=int(os.getenv("DSPY_MAX_TOKENS", "4096")),
    )
    dspy.configure(lm=lm)


# ---------------------------------------------------------------------------
# Planner signatures
# ---------------------------------------------------------------------------

class DecomposeGoal(dspy.Signature):
    """
    Break a high-level user goal into a list of concrete, independently
    executable subtasks. Return ONLY a numbered list, one task per line.
    Each task must be self-contained and actionable by a research executor.
    """
    goal: str = dspy.InputField(desc="High-level user goal")
    past_context: str = dspy.InputField(desc="Relevant past memory, may be empty")
    tasks: str = dspy.OutputField(desc="Numbered list of subtasks, one per line")


class PlannerModule(dspy.Module):
    def __init__(self) -> None:
        self.decompose = dspy.ChainOfThought(DecomposeGoal)

    def forward(self, goal: str, past_context: str = "") -> list[str]:
        pred = self.decompose(goal=goal, past_context=past_context)
        lines = [l.strip() for l in pred.tasks.strip().splitlines() if l.strip()]
        # strip leading "1. " "2. " etc
        tasks = []
        for line in lines:
            parts = line.split(".", 1)
            tasks.append(parts[1].strip() if len(parts) == 2 and parts[0].isdigit() else line)
        return tasks


# ---------------------------------------------------------------------------
# Executor signatures
# ---------------------------------------------------------------------------

class ExecuteTask(dspy.Signature):
    """
    Execute a research subtask thoroughly. Produce a detailed, factual result.
    If you cannot complete the task, explain why clearly.
    """
    task_description: str = dspy.InputField(desc="The specific subtask to execute")
    past_context: str = dspy.InputField(desc="Relevant past memory, may be empty")
    result: str = dspy.OutputField(desc="Detailed result of executing the task")


class ExecutorModule(dspy.Module):
    def __init__(self) -> None:
        self.execute = dspy.ChainOfThought(ExecuteTask)

    def forward(self, task_description: str, past_context: str = "") -> str:
        pred = self.execute(task_description=task_description, past_context=past_context)
        return pred.result


# ---------------------------------------------------------------------------
# Critic signatures
# ---------------------------------------------------------------------------

class EvaluateResult(dspy.Signature):
    """
    Evaluate the quality of a task result on a scale from 1 to 10.
    A score of 7 or above means the result is acceptable and should be approved.
    A score below 7 means it needs improvement and should be rejected with feedback.
    """
    task_description: str = dspy.InputField(desc="The original task that was executed")
    result: str = dspy.InputField(desc="The executor's output to evaluate")
    score: str = dspy.OutputField(desc="Integer quality score from 1 to 10")
    reasoning: str = dspy.OutputField(desc="Brief reasoning for the score")
    feedback: str = dspy.OutputField(desc="Specific, actionable feedback for improvement if score < 7, else empty string")


class CriticModule(dspy.Module):
    def __init__(self) -> None:
        self.evaluate = dspy.ChainOfThought(EvaluateResult)

    def forward(self, task_description: str, result: str) -> tuple[int, str, str]:
        pred = self.evaluate(task_description=task_description, result=result)
        try:
            score = int("".join(c for c in str(pred.score) if c.isdigit())[:2])
        except (ValueError, TypeError):
            score = 5
        score = max(1, min(10, score))
        return score, pred.reasoning, pred.feedback


# ---------------------------------------------------------------------------
# Summarizer signatures
# ---------------------------------------------------------------------------

class SynthesizeResults(dspy.Signature):
    """
    Synthesize multiple approved task results into a single cohesive, well-structured
    final answer that directly addresses the original goal. Be comprehensive and clear.
    """
    goal: str = dspy.InputField(desc="The original high-level goal")
    results: str = dspy.InputField(desc="Approved task results, separated by '---'")
    summary: str = dspy.OutputField(desc="Comprehensive final answer synthesizing all results")


class SummarizerModule(dspy.Module):
    def __init__(self) -> None:
        self.synthesize = dspy.ChainOfThought(SynthesizeResults)

    def forward(self, goal: str, results: list[str]) -> str:
        results_text = "\n\n---\n\n".join(f"Result {i + 1}:\n{r}" for i, r in enumerate(results))
        pred = self.synthesize(goal=goal, results=results_text)
        return pred.summary
