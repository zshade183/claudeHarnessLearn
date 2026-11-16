"""串起工作流登记、运行文件和任务生命周期。"""

import asyncio
import re
from collections.abc import Callable
from pathlib import Path

from agent.filelocks import file_lock
from agent.workflows import (
    Budget,
    ExecutionState,
    LocalWorkflowTask,
    RunnerOutput,
    WorkflowInputError,
    WorkflowJournal,
    get_workflow,
    read_resume_args,
    write_workflow_json,
)


def run_saved_workflow(
    name: str,
    args: dict | None = None,
    *,
    resume_from_run_id: str | None = None,
    store: Path,
    run_agent: Callable[[str], RunnerOutput],
) -> dict:
    """让真实模型和离线演示通过同一个入口执行保存好的工作流。"""
    _, script = get_workflow(name)
    if args is not None and not isinstance(args, dict):
        raise WorkflowInputError("Workflow args must be an object")

    resume = resume_from_run_id is not None
    if resume and (
        not isinstance(resume_from_run_id, str)
        or re.fullmatch(r"[0-9a-f]{32}", resume_from_run_id) is None
    ):
        raise WorkflowInputError("Invalid resume runId")

    task = LocalWorkflowTask(name, run_id=resume_from_run_id)
    snapshot_path = store / f"{task.run_id}.json"
    if resume and not snapshot_path.is_file():
        raise WorkflowInputError("Resume snapshot not found")

    store.mkdir(parents=True, exist_ok=True)
    # 同一个 runId 在整个执行及保存期间只由一个进程持有。
    with file_lock(snapshot_path.with_suffix(".lock"), blocking=False):
        if resume:
            run_args = read_resume_args(snapshot_path, name, args)
        else:
            if snapshot_path.exists():
                raise WorkflowInputError(f"Workflow runId already exists: {task.run_id}")
            run_args = {} if args is None else args
        return _execute(task, script, run_args, snapshot_path, run_agent, resume=resume)


def _execute(
    task: LocalWorkflowTask,
    script: Callable,
    run_args: dict,
    snapshot_path: Path,
    run_agent: Callable[[str], RunnerOutput],
    *,
    resume: bool,
) -> dict:
    budget = Budget(run_args.get("budget"))
    journal = WorkflowJournal(snapshot_path.with_suffix(".journal.jsonl"), resume=resume)
    ctx = ExecutionState(run_agent, journal=journal, task=task, budget=budget)
    output_path = snapshot_path.with_suffix(".output.json")
    snapshot = {
        "runId": task.run_id,
        "workflowName": task.workflow_name,
        "args": run_args,
        "task": task.to_dict(),
        "progress": task.progress,
    }

    # 旧快照和 journal 校验通过后，才写入本次开始状态。
    write_workflow_json(snapshot_path, snapshot)
    launched = task.launch_info()
    task.event("async_launched", workflowName=task.workflow_name)
    task.event("task_started", workflowName=task.workflow_name, resume=resume)

    result = None
    try:
        # 脚本内部可并行，工具入口会等到脚本整个执行完毕。
        result = asyncio.run(script(ctx, run_args))
    except Exception as error:
        task.status = "failed"
        task.error = f"{type(error).__name__}: {error}"
    else:
        task.status = "completed"

    # 输出先写成功，再把快照更新为终态。
    write_workflow_json(output_path, result if task.error is None else {"error": task.error})
    snapshot["task"] = task.to_dict()
    write_workflow_json(snapshot_path, snapshot)
    task.event(
        "task_notification",
        status=task.status,
        **task.usage,
        outputFile=output_path,
    )
    return {
        "launched": launched,
        "result": result,
        "progress": task.progress,
        "task": snapshot["task"],
    }
