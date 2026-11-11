from agent.teams import TEAM
from agent.tools.task_tools import TASK_ID_SCHEMA
from agent.worktrees import create_worktree


def run_spawn_teammate(
    name: str, task_id: str | None = None, require_plan: bool = False,
) -> str:
    """启动一个名为 name 的队友，等待接收消息。
    :param name: 团队成员名称。
    :return: None
    """
    TEAM.spawn_teammate(name, task_id=task_id, require_plan=require_plan)
    if task_id is not None:
        return f"Teammate {name.lower()} started with task {task_id}."
    return f"Teammate {name.lower()} is ready to receive messages."


def run_list_teammates() -> str:
    """列出所有活跃的队友。
    :return: 所有活跃的队友名称，每个名称占一行。
    """
    names = sorted(TEAM.list_teammates())
    return "\n".join(names) or "No active teammates."


def run_send_message(to: str, content: str) -> str:
    """发送一条消息给指定的队友。
    :param to: 团队成员名称。
    :param content: 消息内容。
    :return: None
    """
    TEAM.send_message(to, content)
    return f"Sent to {to.lower()}."

def run_request_shutdown(teammate: str) -> str:
    """请求一个队友关闭当前工作。
    :param teammate: 团队成员名称。
    :return: None
    """
    request_id = TEAM.request_shutdown(teammate)
    return f"Shutdown requested from {teammate.lower()} ({request_id})."

def run_request_plan(teammate: str, task: str) -> str:
    """请求一个队友计划一个任务。
    :param teammate: 团队成员名称。
    :param task: 任务描述。
    :return: None
    """
    TEAM.send_message(teammate, task, msg_type="plan_request")
    return f"Plan requested from {teammate.lower()}."

def run_review_plan(
    teammate: str,
    request_id: str,
    approve: bool,
    feedback: str = "",
) -> str:
    """审批一个队友计划。
    :param teammate: 团队成员名称。
    :param request_id: 计划请求 ID。
    :param approve: 是否审批计划。
    :param feedback: 审批反馈。
    :return: None
    """
    TEAM.send_message(
        teammate,
        feedback,
        msg_type="plan_approval_response",
        metadata={"request_id": request_id, "approve": approve},
    )
    return f"Review sent to {teammate.lower()} for {request_id}."

TEAM_TOOLS = [
    {
        "name": "spawn_teammate",
        "description": "After user confirmation, start a teammate and optionally assign its first task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "task_id": TASK_ID_SCHEMA,
                "require_plan": {
                    "type": "boolean",
                    "description": "With an initial task, require Lead approval before workspace edits.",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_worktree",
        "description": "Create an isolated Git working directory for a pending task before it is claimed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "task_id": TASK_ID_SCHEMA,
            },
            "required": ["name", "task_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_teammates",
        "description": "List the names of active teammates.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "send_message",
        "description": "Send work or a follow-up to a named teammate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["to", "content"],
        },
    },
    {
        "name": "request_shutdown",
        "description": "Ask a teammate to stop after its current work.",
        "input_schema": {
            "type": "object",
            "properties": {"teammate": {"type": "string"}},
            "required": ["teammate"],
        },
    },
    {
        "name": "request_plan",
        "description": "Ask a teammate to propose a plan for a task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "teammate": {"type": "string"},
                "task": {"type": "string"},
            },
            "required": ["teammate", "task"],
        },
    },
    {
        "name": "review_plan",
        "description": "Approve or return a teammate's submitted plan by ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "teammate": {"type": "string"},
                "request_id": {"type": "string"},
                "approve": {"type": "boolean"},
                "feedback": {"type": "string"},
            },
            "required": ["teammate", "request_id", "approve"],
        },
    },
]

TEAM_HANDLERS = {
    "spawn_teammate": run_spawn_teammate,
    "create_worktree": create_worktree,
    "list_teammates": run_list_teammates,
    "send_message": run_send_message,
    "request_shutdown": run_request_shutdown,
    "request_plan": run_request_plan,
    "review_plan": run_review_plan,
}
