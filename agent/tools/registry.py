import re
from collections.abc import Callable
from copy import deepcopy

from agent import mcp
from agent.planning import run_todo_write
from agent.subagents import run_subagent
from agent.tools.base import BASE_TOOLS, BASE_HANDLERS
from agent.skills import SKILL_LOADER
from agent.tools.cron_tools import CRON_HANDLERS, CRON_TOOLS
from agent.tools.task_tools import TASK_HANDLERS, TASK_TOOLS
from agent.tools.team_tools import TEAM_HANDLERS, TEAM_TOOLS


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
# 连接 MCP 服务工具
CONNECT_MCP_TOOL = {
    "name": "connect_mcp",
    "description": "Connect to an MCP server and discover its tools.",
    "input_schema": {
        "type": "object",
        "properties": {
            # 工具参数和可连接服务共用一份名单。
            "name": {"type": "string", "enum": list(mcp.MOCK_SERVERS)},
        },
        "required": ["name"],
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
    CONNECT_MCP_TOOL,
    TODO_TOOL,
    TASK_TOOL,
    LOAD_SKILL_TOOL,
    COMPACT_TOOL,
    *TASK_TOOLS,
    *CRON_TOOLS,
    *TEAM_TOOLS,
]

TOOL_HANDLERS = {
    **BASE_HANDLERS,
    "todo_write": run_todo_write,
    "task": run_subagent,
    "load_skill": SKILL_LOADER.load,
    "connect_mcp": mcp.connect_mcp,
    **TASK_HANDLERS,
    **CRON_HANDLERS,
    **TEAM_HANDLERS,
}


def _make_mcp_handler(server: mcp.MCPClient, tool_name: str) -> Callable[..., str]:
    # 每个执行函数都记住自己的服务对象和服务端工具名。
    def run(**args) -> str:
        return server.call_tool(tool_name, args)

    return run


def assemble_tool_pool() -> tuple[list[dict], dict[str, Callable]]:
    """把当前已连接的 MCP 工具加入本轮工具清单和执行函数表。"""
    tools = list(TOOLS)
    handlers = dict(TOOL_HANDLERS)
    policies: dict[str, str] = {}
    used_names = {tool["name"] for tool in tools}

    for server_name, server in mcp.mcp_clients.items():
        for tool_def in server.tools:
            raw_name = tool_def["name"]
            full_name = re.sub(r"[^A-Za-z0-9_-]", "_", f"mcp__{server_name}__{raw_name}")
            if len(full_name) > 64 or full_name in used_names:
                raise ValueError(f"Invalid or duplicate MCP tool name: {full_name}")

            schema = tool_def.get("inputSchema", {"type": "object"})
            if not isinstance(schema, dict) or schema.get("type") != "object":
                raise ValueError(f"Invalid input schema for MCP tool: {full_name}")

            # 给模型发带服务名的工具说明，执行时仍用服务端的原名。
            tools.append({
                "name": full_name,
                "description": tool_def.get("description", ""),
                "input_schema": schema,
            })
            handlers[full_name] = _make_mcp_handler(server, raw_name)
            used_names.add(full_name)
            policies[full_name] = mcp.MCP_HOST_POLICY.get(
                (server_name, raw_name), "confirm"
            )

    # 组装完成后整张表一起替换，权限钩子始终读取完整的表。
    mcp.mcp_tool_policies = policies
    return tools, handlers
