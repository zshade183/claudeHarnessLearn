from collections.abc import Callable

from agent.client import RecoveryState, request_model, response_token_count
from agent.config import MAX_GOAL_BLOCKS, MAX_TURNS, MODEL
from agent.goals import GoalController
from agent.prompts import append_mcp_status, build_system_prompt
from agent.tools.registry import assemble_tool_pool
from agent.hooks import trigger_hooks
from agent.tools.executor import execute_tool
from agent.context import COMPACTOR, MAX_REACTIVE_RETRIES
from agent.background import BACKGROUND, inject_background_results
from agent.teams import inject_team_messages
# 顶部 import 合并成一行
from agent.memory import (
    consolidate_memories,
    extract_memories,
    load_memories,
)


DEFAULT_MAX_TOKENS = 8000
ESCALATED_MAX_TOKENS = 16000
MAX_CONTINUATIONS = 2
CONTINUATION_PROMPT = (
    "Continue the unfinished answer from where it stopped. "
    "Avoid repeating earlier text."
)


def agent_loop(
    messages: list,
    active_request: str,
    *,
    on_first_response: Callable[[], None] | None = None,
    goal: GoalController | None = None,
    collect_background: bool = True,
):
    """
    主循环，处理用户输入、模型调用、工具执行、上下文压缩等。
    params:
        messages: 当前会话的消息列表
        active_request: 当前会话的请求文本
        goal: 当前会话的目标控制器
        collect_background: 本轮是否取走后台完成通知
    """
    if goal is not None:
        # 本轮开始处理目标。若仍需等待，由 defer 重新标记。
        goal.waiting_for_background = False

    goal_blocks = 0  # 当前请求因目标未达成而续轮的次数
    rounds_since_todo = 0  # 未更新任务列表的轮数
    max_tokens = DEFAULT_MAX_TOKENS
    continuation_count = 0
    recovery = RecoveryState(current_model=MODEL)
    reactive_retries = 0  # 已做过的补救压缩次数

    # 本回合开场召回一次，拼进 system prompt
    system = build_system_prompt(load_memories(messages))
    if goal is not None and goal.active is not None:
        #这句提醒主模型把重要的工具结果明确报告出来。
        #比如直接报告读到的 Hello World，判断器就能核对回答和工具结果是否一致。
        system += "\nReport concrete tool results that show which goal conditions were met."

    turns = 0
    while True:
        if MAX_TURNS and turns >= MAX_TURNS:
            # 最后一轮的工具结果已保存，在发出新请求前交回控制权。
            trigger_hooks("Stop", messages)
            print(f"  [loop] max_turns: reached {MAX_TURNS} model turns")
            return

        turns += 1

        # 定时回合首次响应前使用临时对话，通知和回信先留在原处。
        if on_first_response is None:
            # 本轮首次响应，先取走后台完成通知。
            if collect_background:
                inject_background_results(messages)
            inject_team_messages(messages)

        # 每次请求模型前，先把上下文压回安全范围
        messages[:] = COMPACTOR.prepare(messages, active_request)

        # 连接工具可能刚登记了一个服务，下一次请求前重新组装。
        tools, handlers = assemble_tool_pool()

        try:
            response = request_model(
                model=MODEL,
                recovery=recovery,
                system=append_mcp_status(system),
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
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

        # 在处理工具调用和截断续写之前，先记下主模型已报告的用量。
        if goal is not None and goal.active is not None:
            goal.active.main_tokens += response_token_count(response)

        # 首次请求成功说明模型收到了定时指令。先确认交付，再执行工具。
        # 放在请求的异常处理之外，保存失败时应重试交付，而不是压缩上下文。
        if on_first_response is not None:
            on_first_response()
            on_first_response = None

        if response.stop_reason == "max_tokens":
            if max_tokens == DEFAULT_MAX_TOKENS:
                max_tokens = ESCALATED_MAX_TOKENS
                print(f"  [max_tokens] retry with {max_tokens}")
                continue

            # 截断回复可能含有不完整的工具参数，只保留已有的正文。
            text_blocks = [
                block for block in response.content
                if block.type == "text" and block.text.strip()
            ]
            messages.append({
                "role": "assistant",
                "content": text_blocks or "The response was cut off before it produced text.",
            })

            if text_blocks and continuation_count < MAX_CONTINUATIONS:
                continuation_count += 1
                # 回合结束时终端只打印最后一条回复，先显示这一段。
                for block in text_blocks:
                    print(block.text)

                messages.append({
                    "role": "user",
                    "content": CONTINUATION_PROMPT,
                })
                print(f"  [max_tokens] continue {continuation_count}/{MAX_CONTINUATIONS}")
                continue

            print("  [max_tokens] Output remains incomplete.")
            return

        max_tokens = DEFAULT_MAX_TOKENS

        messages.append({
            "role": "assistant",
            "content": response.content,
        })

        tool_calls = [
            block
            for block in response.content
            if block.type == "tool_use"
        ]

        if not tool_calls:  # 主模型准备结束，先检查是否还需要继续。
            force = trigger_hooks("Stop", messages)  # 触发Stop钩子
            if force:
                messages.append({
                    "role": "user",
                    "content": force,
                })
                continue

            if goal is not None:
                decision = goal.evaluate_after_turn(
                    messages, background_pending=BACKGROUND.has_pending()
                )

                if decision.action == "block":
                    if goal_blocks >= MAX_GOAL_BLOCKS:
                        print(
                            f"  [goal] limit: reached {MAX_GOAL_BLOCKS} "
                            f"automatic continuations. {decision.reason}"
                        )
                        return

                    goal_blocks += 1
                    condition = goal.active.condition if goal.active is not None else ""
                    messages.append({
                        "role": "user",
                        "content": (
                            "[Goal still active]\n"
                            f"Condition: {condition}\n"
                            f"Evaluator: {decision.reason}\n"
                            "Continue the task and report the missing evidence."
                        ),
                    })
                    continue

                if decision.action != "allow":
                    print(f"  [goal] {decision.action}: {decision.reason}")
                if decision.action in {"defer", "error", "failed"}:
                    return

            # 回合真正结束：提取可复用知识，达标就整理
            if extract_memories(messages):
                consolidate_memories()
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
                output = execute_tool(
                    block,
                    handlers,
                    allow_background=True,
                )

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
