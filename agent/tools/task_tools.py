from agent.tasks import (
    TASK_ID_PATTERN,
    claim_task,
    complete_task,
    create_task,
    get_task,
    list_tasks,
    update_task,
)


def run_create_task(subject: str, description: str = "") -> str:
    """
    创建任务。
    :param subject: 任务主题
    :param description: 任务描述
    :return: 创建任务结果
    """
    task = create_task(subject, description)
    print(f"  [create] {task.subject}")
    return f"Created {task.id}: {task.subject}"


def run_update_task(task_id: str, addBlockedBy: list[str]) -> str:
    """
    更新任务的依赖关系。
    :param task_id: 任务 ID
    :param addBlockedBy: 要添加的依赖任务 ID 列表
    :return: 更新任务结果
    """
    task = update_task(task_id, addBlockedBy)
    dependencies = ", ".join(task.blockedBy) or "(none)"
    print(f"  [update] {task.subject} blockedBy: {dependencies}")
    return f"Updated {task.id} blockedBy: {dependencies}"


def run_list_tasks() -> str:
    """
    列出所有任务。
    :return: 任务列表
    """
    tasks = list_tasks()
    if not tasks:
        return "No tasks. Use create_task to add some."

    markers = {
        "pending": "[ ]",
        "in_progress": "[>]",
        "completed": "[x]",
    }

    lines = []
    for task in tasks:
        marker = markers.get(task.status, "[?]")
        owner = f" [{task.owner}]" if task.owner else ""
        dependencies = (
            f" (blockedBy: {', '.join(task.blockedBy)})"
            if task.blockedBy
            else ""
        )
        worktree = f" (worktree: {task.worktree})" if task.worktree is not None else ""
        lines.append(
            f"{marker} {task.id}: {task.subject} "
            f"[{task.status}]{owner}{dependencies}{worktree}"
        )

    return "\n".join(lines)


def run_claim_task(task_id: str) -> str:
    """
    领取任务。
    :param task_id: 任务 ID
    :return: 领取任务结果
    """
    return claim_task(task_id, owner="agent")


def run_complete_task(task_id: str) -> str:
    """
    完成任务。
    :param task_id: 任务 ID
    :return: 完成任务结果
    """
    return complete_task(task_id, owner="agent")


TASK_ID_SCHEMA = {
    "type": "string",
    "pattern": TASK_ID_PATTERN.pattern,
}

TASK_TOOLS = [
    {
        "name": "create_task",
        "description": "Create a persistent task and return its generated ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["subject"],
            "additionalProperties": False,
        },
    },
    {
        "name": "update_task",
        "description": "Add prerequisite tasks using their generated IDs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": TASK_ID_SCHEMA,
                "addBlockedBy": {
                    "type": "array",
                    "items": TASK_ID_SCHEMA,
                    "minItems": 1,
                },
            },
            "required": ["task_id", "addBlockedBy"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_tasks",
        "description": "List persistent tasks and their current state.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_task",
        "description": "Read the full record for a task ID.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "claim_task",
        "description": "Start a pending task after its prerequisites are complete.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "complete_task",
        "description": "Mark a task claimed by this agent as completed.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
]

TASK_HANDLERS = {
    "create_task": run_create_task,
    "update_task": run_update_task,
    "list_tasks": run_list_tasks,
    "get_task": get_task,
    "claim_task": run_claim_task,
    "complete_task": run_complete_task,
}
