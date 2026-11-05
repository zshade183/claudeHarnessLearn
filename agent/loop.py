from agent.client import client
from agent.config import MODEL, SYSTEM
from agent.tools.registry import TOOLS, TOOL_HANDLERS
from agent.permissions import check_permission


def agent_loop(messages: list):
    """Main loop for the agent.
    Args:
        messages: List of messages to pass to the model.
    """
    while True:
        # 调用模型
        response = client.messages.create(  
            model=MODEL,
            system=SYSTEM,
            messages=messages,
            tools=TOOLS,
            max_tokens=8000,
        )
        # 处理模型响应
        messages.append({
            "role": "assistant",
            "content": response.content,
        })
        # 处理工具调用
        tool_calls = [
            block
            for block in response.content
            if block.type == "tool_use"
        ]

        if not tool_calls:
            return

        results = []
        # 处理工具调用
        for block in tool_calls:
            print(f"\033[36m> {block.name}\033[0m")

            if not check_permission(block):
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": "Permission denied.",
                })
                continue

            handler = TOOL_HANDLERS.get(block.name)
            output = (
                handler(**block.input)
                if handler
                else f"Unknown: {block.name}"
            )

            print(str(output)[:200])

            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": output,
            })

        messages.append({
            "role": "user",
            "content": results,
        })