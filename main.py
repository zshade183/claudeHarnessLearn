import sys
import threading
from copy import deepcopy
from datetime import datetime
from time import sleep

from anthropic import APIError

from agent.teams import TEAM


try:
    # 导入 readline 模块
    # 用于在命令行中使用箭头键导航历史记录
    # 仅在 Unix-like 系统上有效
    # 在 Windows 上，使用 cmd.exe 作为 shell，不支持箭头键导航历史记录
    import readline

    readline.parse_and_bind("set bind-tty-special-chars off")
    readline.parse_and_bind("set input-meta on")
    readline.parse_and_bind("set output-meta on")
    readline.parse_and_bind("set convert-meta off")
except ImportError:
    pass

from agent.background import BACKGROUND
from agent.client import client
from agent.config import GOAL_EVALUATOR_MODEL
from agent.goals import CLEAR_ALIASES, GoalController, GoalError, PromptGoalEvaluator
from agent.loop import agent_loop
from agent.hooks import trigger_hooks
from agent.scheduler import (
    acknowledge_cron_jobs,
    consume_cron_queue,
    load_durable_jobs,
    poll_due_jobs,
    restore_cron_jobs,
)


def print_assistant_reply(history: list) -> None:
    if not history or history[-1].get("role") != "assistant":
        return

    content = history[-1]["content"]
    if isinstance(content, list):
        for block in content:
            if getattr(block, "type", None) == "text":
                print(block.text)
    elif isinstance(content, str):
        print(content)
    print()


def run_scheduled_turn(history: list, *, collect_background: bool = True) -> bool:
    """交付一批到点任务。调用方须持有 agent_lock
    params:
        history: 上一回合的对话历史
    return:
        是否成功交付所有任务
    """
    jobs = consume_cron_queue()
    if not jobs:
        return True

    acknowledged = False  # 是否成功交付所有任务

    def confirm_delivery() -> None:
        """确认交付所有任务"""
        nonlocal acknowledged
        acknowledge_cron_jobs(jobs)
        acknowledged = True
        for job in jobs:
            print(f"  [cron] delivered {job.id}: {job.prompt[:60]}")

    try:
        # 上下文压缩会修改消息。用副本跑定时回合，便于在首次交付失败时回退。
        scheduled_history = deepcopy(history)
        request = "\n\n".join(f"[Scheduled] {job.prompt}" for job in jobs)
        scheduled_history.append({"role": "user", "content": request})
        agent_loop(
            scheduled_history,
            request,
            on_first_response=confirm_delivery,
            collect_background=collect_background,
        )
    except Exception as error:
        # 首次响应前失败可以重投。模型已接收的任务可能已经调用了工具，
        # 因此后续出错只报告，避免把整次工作再执行一遍。
        if not acknowledged:
            restore_cron_jobs(jobs)
        else:
            # 指令已经交付，保留这一轮收取的通知和执行过的工具结果。
            history[:] = scheduled_history
        print(f"  [cron] scheduled turn failed: {type(error).__name__}: {error}")
        return False

    history[:] = scheduled_history
    print_assistant_reply(history)
    return True


def poll_clock(stop_event: threading.Event) -> None:
    """按本机时间每秒检查一次。到点只入队，不调用模型。"""
    while not stop_event.wait(1.0):
        poll_due_jobs(datetime.now())

def run_team_turn(history: list, *, collect_background: bool = True) -> bool:
    """发现队友回信时，开启一轮主代理对话。调用方持有 agent_lock。"""
    # peek 只检查有没有信。实际取信由 agent_loop() 完成。
    if not TEAM.bus.peek("lead"):
        return True

    request = "Review the incoming teammate messages and report the result to the user."
    history.append({"role": "user", "content": request})

    print("  [team] lead has a new reply")

    try:
        agent_loop(history, request, collect_background=collect_background)
    except Exception as error:
        print(f"  [team] could not process reply: {error}")
        return False

    print_assistant_reply(history)
    return True


def run_background_turn(history: list, *, goal: GoalController) -> bool:
    """交付已结束的后台命令。交互模式下调用方须持有 agent_lock。"""
    if not BACKGROUND.has_ready():
        return True

    active = goal.active if goal.waiting_for_background else None
    request = "Review the completed background commands and report their results."
    if active is not None:
        request = (
            "Continue the current goal using the background results.\n"
            f"Condition: {active.condition}"
        )

    history.append({"role": "user", "content": request})

    try:
        # 完成通知由主循环取走并加入本轮消息。
        agent_loop(history, request, goal=goal if active is not None else None)
    except Exception as error:
        print(f"  [background] could not process result: {error}")
        return False

    print_assistant_reply(history)
    return True


def process_pending_turns(
    stop_event: threading.Event,
    agent_lock: threading.Lock,
    history: list,
    goal: GoalController,
) -> None:
    """终端空闲时处理定时任务、队友回信和后台命令结果。"""
    while not stop_event.wait(0.2):
        # 手动回合持有锁时，等下一次检查再处理待交付事件。
        if not agent_lock.acquire(blocking=False):
            continue
        try:
            if stop_event.is_set():  # 退出线程
                return
            # Goal 已在等结果时，完成通知留给能接续 Goal 的后台回合。
            collect_background = not goal.waiting_for_background
            cron_ok = run_scheduled_turn(history, collect_background=collect_background)
            team_ok = run_team_turn(history, collect_background=collect_background)
            background_ok = run_background_turn(history, goal=goal)
        finally:
            agent_lock.release()  # 释放锁，允许其他线程获取锁

        if not (cron_ok and team_ok and background_ok):
            # 有一处失败，就稍等一下再进入下一轮检查。
            stop_event.wait(1.0)


def submit_query(query: str, history: list, goal: GoalController) -> None:
    """处理一条终端输入，供交互模式和单次执行共用。"""
    parts = query.split(maxsplit=1)
    if not parts:
        return

    if parts[0] == "/goal":
        condition = parts[1].strip() if len(parts) == 2 else ""
        if not condition:
            print(goal.status())
            return
        if condition.lower() in CLEAR_ALIASES:
            print(goal.clear())
            return

        try:
            goal.set_goal(condition)
        except GoalError as error:
            print(f"Goal error: {error}")
            return

        # 后续照常处理用户请求，只把完成条件交给主模型。
        query = condition

    trigger_hooks("UserPromptSubmit", query)
    history.append({"role": "user", "content": query})
    try:
        agent_loop(history, query, goal=goal)
    except APIError as error:
        # 保留已经写入对话的工具结果，并记录本轮失败。
        history.append({
            "role": "assistant",
            "content": f"[Error] {type(error).__name__}: {error}",
        })
    print_assistant_reply(history)


def main(argv: list[str] | None = None) -> None:
    history = []
    goal = GoalController(PromptGoalEvaluator(client, GOAL_EVALUATOR_MODEL))

    if argv:
        submit_query(" ".join(argv), history, goal)

        # 单次模式自己交付后台通知，沿用刚才的对话和目标。
        while goal.waiting_for_background:
            if not run_background_turn(history, goal=goal):
                break
            if goal.waiting_for_background:
                sleep(0.2)
        return

    print("Agent Loop")
    print("Enter a question, press Enter to send. Type q to quit.\n")

    load_durable_jobs()
    agent_lock = threading.Lock()
    stop_event = threading.Event()
    workers = [
        threading.Thread(target=poll_clock, args=(stop_event,), daemon=True),
        threading.Thread(
            target=process_pending_turns,
            args=(stop_event, agent_lock, history, goal),
            daemon=True,  # 守时线程，不阻塞主进程退出
        ),
    ]
    started = []
    try:
        for worker in workers:
            worker.start()
            started.append(worker)

        while True:
            try:
                # 等用户输入时不持锁，定时回合才能在终端空闲时运行。
                query = input("\001\033[36m\002agent >> \001\033[0m\002")
            except (EOFError, KeyboardInterrupt):
                break
            if not query.strip():
                continue
            if query.strip().lower() in ("q", "exit"):
                break

            with agent_lock:
                submit_query(query, history, goal)
    finally:
        stop_event.set()
        for worker in started:
            worker.join(timeout=1)


if __name__ == "__main__":
    main(sys.argv[1:])
