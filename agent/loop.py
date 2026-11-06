from agent.client import client
from agent.config import MODEL
from agent.prompts import SYSTEM
from agent.tools.registry import TOOLS, TOOL_HANDLERS
from agent.hooks import trigger_hooks
from agent.tools.executor import execute_tool


def agent_loop(messages: list):
    rounds_since_todo = 0  # 未更新任务列表的轮数
    while True:
        response = client.messages.create(
            model=MODEL,
            system=SYSTEM,
            messages=messages,
            tools=TOOLS,
            max_tokens=8000,
        )

        messages.append({
            "role": "assistant",
            "content": response.content,
        })

        tool_calls = [
            block
            for block in response.content
            if block.type == "tool_use"
        ]

        if not tool_calls:  # 没有工具调用，结束会话
            force = trigger_hooks("Stop", messages)  # 触发Stop钩子
            if force:
                messages.append({
                    "role": "user",
                    "content": force,
                })
                continue
            return

        results = []
        used_todo = False

        for block in tool_calls:
            output = execute_tool(block, TOOL_HANDLERS)

            if block.name == "todo_write":
                used_todo = True

            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": output,
            })

        rounds_since_todo = (
            0 if used_todo else rounds_since_todo + 1
        )

        if rounds_since_todo >= 3:
            results.append({
                "type": "text",
                "text": "<reminder>Update your todos.</reminder>",
            })
            rounds_since_todo = 0

        messages.append({
            "role": "user",
            "content": results,
        })