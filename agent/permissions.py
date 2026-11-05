import re

from agent.config import WORKDIR


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
    Check the command against if any deny-listed patterns are found.
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
PERMISSION_RULES = [
    {  # 读取文件、写入文件、编辑文件工具
        "tools": ["read_file", "write_file", "edit_file"],
        "check": lambda args: not (
            WORKDIR / args.get("path", "")
        ).resolve().is_relative_to(WORKDIR),
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
    for rule in PERMISSION_RULES:
        if tool_name in rule["tools"] and rule["check"](args):
            return rule["message"]
    return None

def ask_user(tool_name: str, args: dict, reason: str) -> str:
    """
    Ask the user for permission to run the tool call.
    """
    print(f"\n\033[33m[permission] {reason}\033[0m")
    print(f"   Tool: {tool_name}({args})")

    choice = input("   Allow? [y/N] ").strip().lower()
    return "allow" if choice in ("y", "yes") else "deny"

def check_permission(block) -> bool:
    """
    Check the permissions of the tool call.
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