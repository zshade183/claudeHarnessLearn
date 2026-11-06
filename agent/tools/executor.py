"""
工具执行器
包含工具调用的处理逻辑，包括触发钩子和调用工具处理函数。
"""
from agent.hooks import trigger_hooks

"""
工具执行器
"""
def execute_tool(block, handlers: dict) -> str:
    blocked = trigger_hooks("PreToolUse", block)
    if blocked:
        return str(blocked)

    handler = handlers.get(block.name)

    try:
        output = (
            handler(**block.input)
            if handler
            else f"Unknown: {block.name}"
        )
    except Exception as e:
        output = f"Error: {e}"

    trigger_hooks("PostToolUse", block, output)
    return str(output)