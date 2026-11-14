import re
import threading

from agent import mcp
from agent.config import TOOL_CWD, WORKDIR


DENY_LIST = [
    "rm -rf /",
    "sudo",
    "shutdown",
    "reboot",
    "mkfs",
    "dd if=",
    "> /dev/sda",
]


def check_deny_list(command: str) -> str | None:
    """
    检查命令是否包含拒绝列表中的模式。
    如果包含，返回拒绝原因；否则返回None。
    """
    for pattern in DENY_LIST:
        if pattern in command:
            return f"Blocked: '{pattern}' is on the deny list"
    return None

# 定义destructive命令的正则表达式
DESTRUCTIVE_COMMAND_WORD = re.compile(
    r"(?i)(?:^|[;&|()\n])\s*(?:rm|del)(?=\s|$|[;&|()])"
)


def contains_destructive_command(command: str) -> bool:
    """
    Check if the command contains any destructive commands.
    """
    return bool(DESTRUCTIVE_COMMAND_WORD.search(command))  

# 定义权限规则
def path_outside_workspace(args: dict) -> bool:
    cwd = (TOOL_CWD.get() or WORKDIR).resolve()
    return not (cwd / args.get("path", "")).resolve().is_relative_to(cwd)


PERMISSION_RULES = [
    {  # 读取文件、写入文件、编辑文件工具
        "tools": ["read_file", "write_file", "edit_file"],
        "check": path_outside_workspace,
        "message": "Writing outside workspace",
    },
    {  # bash工具
        "tools": ["bash"],
        "check": lambda args: (
            contains_destructive_command(args.get("command", ""))
            or any(
                kw in args.get("command", "")
                for kw in ["rm ", "> /etc/", "chmod 777"]
            )
        ),
        "message": "Potentially destructive command",
    },
]


def check_rules(tool_name: str, args: dict) -> str | None:
    """
    Check the permissions of the tool call.
    """
    if tool_name.startswith("mcp__"):
        if mcp.mcp_tool_policies.get(tool_name) == "allow":
            return None
        # 宿主没有明确授予 allow 的 MCP 工具都需要确认。
        return "MCP tool requires confirmation"

    for rule in PERMISSION_RULES:
        if tool_name in rule["tools"] and rule["check"](args):
            return rule["message"]
    return None

def ask_user(tool_name: str, args: dict, reason: str) -> str:
    """
    Ask the user for permission to run the tool call.
    """
    # 定时任务在后台线程中执行。终端输入由主线程负责，
    # 需要人工批准的操作在定时回合中直接拒绝。
    if threading.current_thread() is not threading.main_thread():
        print(f"[permission] Background turn cannot request approval: {reason}")
        return "deny"

    print(f"\n\033[33m[permission] {reason}\033[0m")
    print(f"   Tool: {tool_name}({args})")

    choice = input("   Allow? [y/N] ").strip().lower()
    return "allow" if choice in ("y", "yes") else "deny"

def check_permission(block) -> bool:
    """
    检查工具调用是否有用权限。
    """
    if block.name == "bash":
        reason = check_deny_list(block.input.get("command", ""))
        if reason:
            print(f"\n\033[31m[blocked] {reason}\033[0m")
            return False

    reason = check_rules(block.name, block.input)

    if reason:
        decision = ask_user(block.name, block.input, reason)
        if decision == "deny":
            return False

    return True

def permission_hook(block):
    """
    Hook to check the permissions of the tool call.
    """

    if not check_permission(block):
        return "Permission denied."
    return None
