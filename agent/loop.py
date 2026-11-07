from agent.client import client
from agent.config import MODEL
from agent.prompts import SYSTEM
from agent.tools.registry import TOOLS, TOOL_HANDLERS
from agent.hooks import trigger_hooks
from agent.tools.executor import execute_tool
from agent.context import COMPACTOR, MAX_REACTIVE_RETRIES


def agent_loop(messages: list, active_request: str):
    """
    主循环，处理用户输入、模型调用、工具执行、上下文压缩等。
    params:
        messages: 当前会话的消息列表
        active_request: 当前会话的请求ID
    """
    rounds_since_todo = 0  # 未更新任务列表的轮数
    reactive_retries = 0  # 已做过的补救压缩次数

    while True:
        # 每次请求模型前，先把上下文压回安全范围
        messages[:] = COMPACTOR.prepare(messages, active_request)

        try:
            response = client.messages.create(
                model=MODEL,
                system=SYSTEM,
                messages=messages,
                tools=TOOLS,
                max_tokens=8000,
            )
            reactive_retries = 0  # 成功一次就重置重试额度
        except Exception as error:
            # 只有"上下文超长"才值得压缩重试，别的错误直接抛
            too_long = any(
                text in str(error).lower()
                for text in (
                    "prompt_too_long",
                    "prompt is too long",
                    "too many tokens",
                )
            )
            if too_long and reactive_retries < MAX_REACTIVE_RETRIES:
                print("[reactive compact]")
                messages[:] = COMPACTOR.reactive_compact(
                    messages,
                    active_request,
                )
                reactive_retries += 1
                continue
            raise

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
        compact_requested = False

        for block in tool_calls:
            if block.name == "compact":
                # 循环级工具：不进 dispatch map，由这里直接应答
                output = "Compaction requested after this tool batch."
                compact_requested = True
            else:
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

        if compact_requested:
            # 本批结果已经追加，再整体摘要归档
            messages[:] = COMPACTOR.compact_history(
                messages,
                active_request,
            )