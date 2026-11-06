"""
子代理（subagent）模块。
主代理通过 task 工具调用 run_subagent：派一个上下文独立的 agent 去完成
自包含的子任务，只把最终文本总结返回，避免大量中间工具输出污染主对话历史。
"""

from agent.client import client
from agent.config import MODEL, SUB_SYSTEM
from agent.hooks import trigger_hooks
from agent.tools.base import BASE_TOOLS, BASE_HANDLERS
from agent.tools.executor import execute_tool


# 子代理只继承基础工具：不含 task（防止无限递归地再开子代理），
# 也不含 todo_write（子代理是一次性干活，无需自己的待办列表）。
# 用 list()/dict() 各复制一份，避免与主代理共享同一对象后被意外修改。
SUB_TOOLS = list(BASE_TOOLS)
SUB_HANDLERS = dict(BASE_HANDLERS)


def extract_text(content) -> str:
    """
    从子系统响应中提取文本内容。
    只保留 text 块，丢弃 tool_use/tool_result 等中间过程块，
    因此回传给主代理的是一段干净的总结，而不是完整对话。
    :param content: 子系统响应
    :return: 文本内容
    """
    if not isinstance(content, list):
        return str(content)

    return "\n".join(
        getattr(block, "text", "")
        for block in content
        if getattr(block, "type", None) == "text"
    )


def run_subagent(prompt: str) -> str:
    """
    运行子系统。
    :param prompt: 子系统提示
    :return: 子系统响应
    """
    print("\n\033[35m[Subagent started]\033[0m")

    # 全新的独立上下文：不含任何主代理历史，实现上下文隔离
    messages = [{"role": "user", "content": prompt}]  # 子系统提示

    # 最多 30 轮，作为兜底防止子代理陷入死循环（主代理循环没有此上限）
    for _ in range(30):
        response = client.messages.create(
            model=MODEL,
            system=SUB_SYSTEM,  # 独立的 system prompt，仅要求"完成并返回简洁答案"
            messages=messages,
            tools=SUB_TOOLS,  # 精简工具集，见上方说明
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

        # 没有工具调用，说明子代理已给出最终答案，可以收工返回
        if not tool_calls:
            force = trigger_hooks("Stop", messages)
            if force:
                messages.append({
                    "role": "user",
                    "content": force,
                })
                continue

            print("\033[35m[Subagent done]\033[0m")
            # 只回传文本总结，中间工具过程留在子代理内部
            return extract_text(response.content) or "(no summary)"

        results = []

        for block in tool_calls:
            output = execute_tool(block, SUB_HANDLERS)

            # 仅用于终端观察子代理在做什么，不进入主代理上下文
            print(
                f"  \033[90m[sub] {block.name}: "
                f"{output[:100]}\033[0m"
            )

            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": output,
            })

        messages.append({
            "role": "user",
            "content": results,
        })

    # 达到 30 轮仍未给出最终答案，强制中止
    print("\033[35m[Subagent stopped]\033[0m")
    return "Subagent stopped after 30 turns without a final answer."
