"""离线跑一次代码审查，再用保存的 runId 演示续跑。"""

import argparse
import json
from pathlib import Path

from agent.filelocks import file_lock
from agent.workflow_runtime import run_saved_workflow
from agent.workflows import RunnerOutput, WorkflowInputError


DEMO_CHANGES = """\
def average(scores):
    return sum(scores) / len(scores)

def total(scores):
    return sum([score for score in scores])
"""

DEMO_FINDINGS = {
    "correctness": [{"title": "Empty scores cause division by zero", "severity": "high"}],
    "security": [],
    "performance": [{"title": "total creates an unnecessary list", "severity": "low"}],
    "style": [{"title": "Type hints are required", "severity": "low"}],
}

DEMO_VERDICTS = {
    "correctness": {"isReal": True, "reason": "An empty list makes len(scores) zero"},
    "performance": {"isReal": True, "reason": "sum can consume scores directly"},
    "style": {"isReal": False, "reason": "The input shows no type hint requirement"},
}


def run_mock_step(prompt: str) -> RunnerOutput:
    """按审查步骤返回固定答案，方便反复观察 journal 和续跑。"""
    action, _, dimension = prompt.split("\n", 1)[0].partition(": ")
    if action == "Review dimension":
        answer = {"findings": DEMO_FINDINGS[dimension]}
    elif action == "Verify dimension":
        answer = DEMO_VERDICTS[dimension]
    else:
        raise WorkflowInputError(f"Unknown demo step: {action!r}")

    text = json.dumps(answer)
    return RunnerOutput(text=text, tokens=(len(prompt) + len(text)) // 4)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run or resume the offline workflow demo.")
    parser.add_argument("command", choices=("demo", "resume"))
    command = parser.parse_args().command

    # 演示用自己的目录，模拟回答和真实模型的 journal 各自保留。
    store = Path.cwd() / ".runtime" / "demo"
    last_run = store / "last_run.txt"
    last_run_lock = last_run.with_suffix(".lock")
    resume_id = None
    if command == "resume":
        if not last_run.is_file():
            print("No demo to resume. Run python -m agent.workflow_demo demo first.")
            return 1
        with file_lock(last_run_lock):
            resume_id = last_run.read_text(encoding="utf-8").strip()
        print(f"Resuming demo {resume_id}")

    try:
        result = run_saved_workflow(
            "review-changes",
            {"changes": DEMO_CHANGES} if command == "demo" else None,
            resume_from_run_id=resume_id,
            store=store,
            run_agent=run_mock_step,
        )
    except (WorkflowInputError, OSError) as error:
        print(f"Error: {error}")
        return 1

    task = result["task"]
    if command == "demo":
        with file_lock(last_run_lock):
            temporary = last_run.with_suffix(".tmp")
            temporary.write_text(task["runId"] + "\n", encoding="utf-8")
            temporary.replace(last_run)

    if task["error"] is not None:
        print(task["error"])
        return 1

    print("\nConfirmed findings:")
    for finding in result["result"]["confirmed"]:
        print(f"  [{finding['severity']}] {finding['dimension']}: {finding['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
