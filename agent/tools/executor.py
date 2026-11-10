"""
工具执行器
包含工具调用的处理逻辑，包括触发钩子和调用工具处理函数。
"""
from agent.hooks import trigger_hooks
from agent.background import BACKGROUND

"""
工具执行器
"""
def execute_tool(
    block,
    handlers: dict,
    *,
    allow_background: bool = False,
) -> str:
    # 先检查权限。前台和后台共用这个入口，
    # 被拒绝的命令会在创建线程之前返回。
    blocked = trigger_hooks("PreToolUse", block)
    if blocked:
        return str(blocked)

    handler = handlers.get(block.name)

    try:
        if handler is None:
            output = f"Unknown: {block.name}"

        elif block.name == "bash":
            # 复制参数再取出开关。原始工具调用还在对话历史里，
            # 而前台的 run_bash() 只接收 command。
            args = dict(block.input)
            run_in_background = args.pop("run_in_background", False)

            if not isinstance(run_in_background, bool):
                raise ValueError("run_in_background must be a boolean")

            if run_in_background:
                if not allow_background:
                    raise ValueError("Background execution is unavailable here")

                # start() 启动线程后返回 ID。等待命令结束的工作由线程负责。
                task_id = BACKGROUND.start(block)
                output = (
                    f"[Background task {task_id} started] "
                    "The result will be collected on a later turn."
                )
            else:
                # 开关没传或为 False 时，沿用原来的前台执行方式。
                output = handler(**args)

        else:
            output = handler(**block.input)

    except Exception as error:
        output = f"Error: {error}"

    trigger_hooks("PostToolUse", block, output)
    return str(output)