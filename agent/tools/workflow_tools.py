import json

from agent.client import client, response_token_count
from agent.config import MODEL, WORKFLOW_RUNTIME_DIR
from agent.subagents import extract_text
from agent.workflow_runtime import run_saved_workflow
from agent.workflows import WORKFLOWS, RunnerOutput


WORKFLOW_TOOL = {
    "name": "Workflow",
    "description": (
        "Run a saved workflow: hello (args.topic), topic-guide (args.topics), "
        "or review-changes (args.changes, the code changes as text). "
        "Set args.budget to a non-negative integer to limit token use per execution. "
        "Provide resume_from_run_id to reuse saved answers and the original args."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "enum": list(WORKFLOWS)},
            "args": {"type": "object"},
            "resume_from_run_id": {"type": "string"},
        },
        "required": ["name"],
        "additionalProperties": False,
    },
}


def run_agent_step(prompt: str) -> RunnerOutput:
    """为工作流执行一次独立的模型请求，取出文字和 token 用量。"""
    response = client.messages.create(
        model=MODEL,
        system="Answer this workflow step using the supplied input. Return concise text.",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=500,
    )
    text = extract_text(response.content).strip()
    tokens = response_token_count(response)
    return RunnerOutput(text=text, tokens=tokens)


def run_workflow(
    name: str, args: dict | None = None, *, resume_from_run_id: str | None = None
) -> str:
    """把真实模型适配器接入运行时，再将结果转成工具返回文本。"""
    output = run_saved_workflow(
        name,
        args,
        resume_from_run_id=resume_from_run_id,
        store=WORKFLOW_RUNTIME_DIR,
        run_agent=run_agent_step,
    )
    return json.dumps(output, ensure_ascii=False)
