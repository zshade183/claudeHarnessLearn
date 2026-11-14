import threading
from copy import deepcopy
from datetime import datetime

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
    content = history[-1]["content"]
    if isinstance(content, list):
        for block in content:
            if getattr(block, "type", None) == "text":
                print(block.text)
    elif isinstance(content, str):
        print(content)
    print()


def run_scheduled_turn(history: list) -> bool:
    """交付一批到点任务。调用方须持有 agent_lock。"""
    jobs = consume_cron_queue()
    if not jobs:
        return True

    acknowledged = False

    def confirm_delivery() -> None:
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
        agent_loop(scheduled_history, request, on_first_response=confirm_delivery)
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

def run_team_turn(history: list) -> bool:
    """发现队友回信时，开启一轮主代理对话。调用方持有 agent_lock。"""
    # peek 只检查有没有信。实际取信由 agent_loop() 完成。
    if not TEAM.bus.peek("lead"):
        return True

    request = "Review the incoming teammate messages and report the result to the user."
    history.append({"role": "user", "content": request})

    print("  [team] lead has a new reply")

    try:
        agent_loop(history, request)
    except Exception as error:
        print(f"  [team] could not process reply: {error}")
        return False

    print_assistant_reply(history)
    return True


def run_background_turn(history: list) -> bool:
    """交付已结束的后台命令。调用方须持有 agent_lock。"""
    if not BACKGROUND.has_ready():
        return True

    request = "Review the completed background commands and report their results."
    history.append({"role": "user", "content": request})

    try:
        # 完成通知由主循环取走并加入本轮消息。
        agent_loop(history, request)
    except Exception as error:
        print(f"  [background] could not process result: {error}")
        return False

    print_assistant_reply(history)
    return True


def process_pending_turns(
    stop_event: threading.Event,
    agent_lock: threading.Lock,
    history: list,
) -> None:
    """终端空闲时处理定时任务、队友回信和后台命令结果。"""
    while not stop_event.wait(0.2):
        # 手动回合持有锁时，等下一次检查再处理待交付事件。
        if not agent_lock.acquire(blocking=False):
            continue
        try:
            if stop_event.is_set():  # 退出线程
                return
            delivered = run_scheduled_turn(history)  # 先交付定时任务
            if delivered:  # 如果定时任务交付成功，再检查队友回信
                delivered = run_team_turn(history)
            if delivered:
                delivered = run_background_turn(history)
        finally:
            agent_lock.release()  # 释放锁，允许其他线程获取锁

        if not delivered:
            # 接口或文件暂时不可用时，等一会再重试。
            stop_event.wait(1.0)


def main() -> None:
    print("Agent Loop")
    print("Enter a question, press Enter to send. Type q to quit.\n")

    load_durable_jobs()
    history = []
    agent_lock = threading.Lock()
    stop_event = threading.Event()
    workers = [
        threading.Thread(target=poll_clock, args=(stop_event,), daemon=True),
        threading.Thread(
            target=process_pending_turns,
            args=(stop_event, agent_lock, history),
            daemon=True,
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
                trigger_hooks("UserPromptSubmit", query)
                history.append({"role": "user", "content": query})
                try:
                    agent_loop(history, query)
                except APIError as error:
                    # 保留已经写入对话的工具结果，并记录本轮失败。
                    history.append({
                        "role": "assistant",
                        "content": f"[Error] {type(error).__name__}: {error}",
                    })
                print_assistant_reply(history)
    finally:
        stop_event.set()
        for worker in started:
            worker.join(timeout=1)


if __name__ == "__main__":
    main()
