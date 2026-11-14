"""
子代理（subagent）模块。
主代理通过 task 工具调用 run_subagent：派一个上下文独立的 agent 去完成
自包含的子任务，只把最终文本总结返回，避免大量中间工具输出污染主对话历史。
"""

from collections.abc import Callable
from itertools import count

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

    # 每次调用 task 都从一份新历史开始。
    messages = [{"role": "user", "content": prompt}]
    return run_subagent_turn(messages)


def run_subagent_turn(
    messages: list,
    *,
    allowed_tools: set[str] | None = None,
    extra_tools: list[dict] | None = None,
    extra_handlers: dict | None = None,
    dispatch: Callable | None = None,
    before_model_call: Callable[[list], None] | None = None,
    max_model_calls: int | None = 30,
    system: str = SUB_SYSTEM,
) -> str:
    """用指定的工具范围完成一轮对话。
    :param messages: 子系统当前对话历史
    :param allowed_tools: 允许的工具名称集合
    :param extra_tools: 这轮额外提供给模型的工具描述
    :param extra_handlers: 这轮额外的工具执行函数
    :param dispatch: 队友可在基础执行器之前检查任务和审批状态
    :param before_model_call: 每次请求模型前，可向当前对话补充消息
    :param max_model_calls: 本轮请求模型的次数上限，None 表示由工作结束或关机控制
    :return: 子系统响应
    """
    # 额外工具只加入当前回合。最后筛选工具描述和执行函数，保证两边一致。
    tools = [*SUB_TOOLS, *(extra_tools or [])]
    handlers = {**SUB_HANDLERS, **(extra_handlers or {})}
    if allowed_tools is not None:
        tools = [tool for tool in tools if tool["name"] in allowed_tools]
        handlers = {
            name: handler
            for name, handler in handlers.items()
            if name in allowed_tools
        }

    # 一次性子代理默认限制 30 次，长期队友可以持续处理工具结果。
    attempts = count() if max_model_calls is None else range(max_model_calls)
    for _ in attempts:
        if before_model_call is not None:
            before_model_call(messages)

        response = client.messages.create(
            model=MODEL,
            system=system,
            messages=messages,
            tools=tools,
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
            output = (dispatch or execute_tool)(block, handlers)

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

    # 达到请求次数上限时，给调用方返回停止原因。
    print("\033[35m[Subagent stopped]\033[0m")
    return f"Subagent stopped after {max_model_calls} turns without a final answer."
