from agent.planning import run_todo_write
from agent.subagents import run_subagent
from agent.tools.base import BASE_TOOLS, BASE_HANDLERS
from agent.skills import SKILL_LOADER
from agent.tools.task_tools import TASK_HANDLERS, TASK_TOOLS
from copy import deepcopy

# 注册工具 
TODO_TOOL = {
    "name": "todo_write",
    "description": (
        "Create and manage a task list for your current coding session."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "maxItems": 20,
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "minLength": 1,
                        },
                        "status": {
                            "type": "string",
                            "enum": [
                                "pending",
                                "in_progress",
                                "completed",
                            ],
                        },
                    },
                    "required": ["content", "status"],
                },
            },
        },
        "required": ["todos"],
    },
}
TASK_TOOL = {
    "name": "task",
    "description": (
        "Run a subagent with fresh conversation context "
        "and return its final text."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "minLength": 1,
            },
        },
        "required": ["prompt"],
    },
}

LOAD_SKILL_TOOL = {
    "name": "load_skill",
    "description": "Load the full SKILL.md content by skill name.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
            },
        },
        "required": ["name"],
    },
}

COMPACT_TOOL = {
    "name": "compact",
    "description": "Summarize earlier conversation to free context space.",
    "input_schema": {
        "type": "object",
        "properties": {},
    },
}

# 主代理和子代理共用基础工具定义。复制内部字典后，
# 只给"主代理"的 bash 工具增加一个可选参数`run_in_background` ，
# 而"子代理"的 bash 保持原样。
main_base_tools = deepcopy(BASE_TOOLS)
bash_tool = next(tool for tool in main_base_tools if tool["name"] == "bash")

bash_tool["input_schema"]["properties"]["run_in_background"] = {
    "type": "boolean",
    "description": "Set to true for an independent command that can finish later.",
}

TOOLS = [
    *main_base_tools,
    TODO_TOOL,
    TASK_TOOL,
    LOAD_SKILL_TOOL,
    COMPACT_TOOL,
    *TASK_TOOLS,
]

TOOL_HANDLERS = {
    **BASE_HANDLERS,
    "todo_write": run_todo_write,
    "task": run_subagent,
    "load_skill": SKILL_LOADER.load,
    **TASK_HANDLERS,
}