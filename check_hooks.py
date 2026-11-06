from types import SimpleNamespace

from agent.hooks import trigger_hooks


allowed = SimpleNamespace(
    name="read_file",
    input={"path": "main.py"},
)

blocked = SimpleNamespace(
    name="bash",
    input={"command": "sudo whoami"},
)

print("允许的调用：")
print("结果：", trigger_hooks("PreToolUse", allowed))

print("\n禁止的调用：")
print("结果：", trigger_hooks("PreToolUse", blocked))