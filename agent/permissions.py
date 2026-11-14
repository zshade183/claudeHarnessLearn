"""
权限检查模块。
检查文件路径、Shell 命令和 MCP 工具的执行权限。
"""

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


def path_outside_workspace(args: dict) -> bool:
    cwd = (TOOL_CWD.get() or WORKDIR).resolve()
    try:
        path = (cwd / args["path"]).resolve()
    except (OSError, ValueError, RuntimeError):
        # 无法确认实际位置时，同样拒绝文件操作。
        return True
    return not path.is_relative_to(cwd)

def check_rules(tool_name: str, args: dict) -> str | None:
    """
    Check the permissions of the tool call.
    """
    if tool_name.startswith("mcp__"):
        if mcp.mcp_tool_policies.get(tool_name) == "allow":
            return None
        # 宿主没有明确授予 allow 的 MCP 工具都需要确认。
        return "MCP tool requires confirmation"

    return None


def ask_user(tool_name: str, args: dict, reason: str) -> str:
    """
    Ask the user for permission to run the tool call.
    """
    # 终端输入由主线程负责。后台回合无法询问用户，直接拒绝。
    if threading.current_thread() is not threading.main_thread():
        print(f"[permission] Background turn cannot request approval: {reason}")
        return "deny"

    print(f"\n\033[33m[permission] {reason}\033[0m")
    print(f"   Tool: {tool_name}({args})")

    choice = input("   Allow? [y/N] ").strip().lower()
    return "allow" if choice in ("y", "yes") else "deny"


def check_permission(block) -> bool:
    """先处理直接拒绝的条件，再按工具规则决定是否询问用户。"""
    if block.name in {"read_file", "write_file", "edit_file"}:
        path = block.input.get("path")
        # 先确认是文字路径，再检查它最终指到哪里。
        if not isinstance(path, str) or path_outside_workspace(block.input):
            print("[blocked] File path is invalid or outside the working directory.")
            return False

    if block.name == "bash":
        command = block.input.get("command")
        if not isinstance(command, str) or not command.strip():
            print("[blocked] Shell command must be a non-empty string.")
            return False

        reason = check_deny_list(command)
        if reason:
            print(f"[blocked] {reason}")
            return False

        # 先确认，再由执行器决定本次命令在前台还是后台运行。
        return ask_user(block.name, block.input, "Shell command") == "allow"

    reason = check_rules(block.name, block.input)

    if reason:
        decision = ask_user(block.name, block.input, reason)
        if decision == "deny":
            return False

    return True


def permission_hook(block):
    """
    Hook to check the permissions of the tool call.
    如果没有权限，返回"Permission denied."。
    如果有权限，返回None。
    """

    if not check_permission(block):
        return "Permission denied."
    return None
