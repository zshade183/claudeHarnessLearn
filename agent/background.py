"""
后台任务管理器（background tasks）
把慢命令丢到后台线程执行，主循环拿到一个占位结果继续干活，
等命令跑完，在后续轮次把结果以"通知"形式送进对话。
① 显式请求，不猜关键词
② 占位结果 + 后续通知（不复用 tool_use_id）
③ 拉取，而不是推送
不复用 tool_use_id的原因
    #`tool_use_id` 是"一问一答"的配对凭据，启动时已经答过了。
    # 完成后补发只能算"新事件"，所以必须走普通文本块，并靠`task_id` 让模型自行关联。
"""

# 主线程（agent_loop）                        后台线程
# ──────────────────────────────────────      ──────────────────────
# 第 N 轮
#   LLM → bash("npm install",
#              run_in_background=true)
#         │
#         ▼
#   executor 拦截 → BACKGROUND.start()
#         │                                    ┌─ _run() 开始
#         │                                    │   subprocess 跑命令
#         ├── 立刻返回占位结果 ────────────────┘
#         │   "[Background task bg_0001 started]"
#         ▼
#   追加 tool_result，继续循环
#         │
#   LLM → read_file("package.json")   ← 主线程没等！
#         │
#         ▼
#   工具结果追加                            命令跑完 → 锁内写
#         │                                  status/results/_ready
#         ▼                                       │
# 第 N+1 轮                                        │
#   inject_background_results(messages) ◀─────────┘
#         │
#         ▼
#   messages[-1] 里多了 <task_notification>
#         │
#         ▼
#   LLM 看到：package.json 内容 + 安装完成通知

import threading

from agent.tools.shell import _format_bash_result, _run_bash_process

class BackgroundManager:
    def __init__(self):
        self.tasks: dict[str, dict] = {}        # 任务映射表
        self.results: dict[str, str] = {}  # 任务结果映射表
        self._ready: list[str] = []  # 已完成的任务 ID 列表
        self._counter = 0  # 任务 ID 计数器
        self._lock = threading.Lock()  # 互斥锁
    

    def start(self, block) -> str:
        """
        启动一个后台任务，返回任务 ID。
        :param block: 包含命令的代码块。
        :return: 任务 ID。
        """

        if block.name != "bash":
            raise ValueError("Only Bash commands can run in the background")

        command = block.input.get("command")  # 获取命令

        if not isinstance(command, str) or not command.strip():
            raise ValueError("Bash command cannot be empty")

        with self._lock:  # 从这里开始进入临界区
            self._counter += 1
            task_id = f"bg_{self._counter:04d}"  # 生成任务 ID
            self.tasks[task_id] = {  # 初始化任务状态
                "tool_use_id": block.id,
                "command": command,
                "status": "running",
            }
        
        thread = threading.Thread(
            target=self._run,#线程启动后执行哪个函数。
            args=(task_id, command),
            daemon=True, # 守护线程。主线程退出时它不会阻止进程结束。
        )

        try:
            thread.start()
        except Exception:
            with self._lock:
                self.tasks.pop(task_id, None)
            raise

        print(f"  [background] started {task_id}: {command[:60]}")
        return task_id
    
    def _run(self, task_id: str, command: str):
        """
        后台线程执行函数，用于运行 Bash 命令。
        :param task_id: 任务 ID。
        :param command: 要运行的 Bash 命令。
        """
        try:
            #注意它会阻塞——但现在阻塞的是后台线程，不是主线程
            output, exit_code = _run_bash_process(command)
            result = _format_bash_result(output, exit_code)
            status = "completed" if exit_code == 0 else "failed"
        except Exception as error:
            result = f"Error: {type(error).__name__}: {error}"
            status = "failed"

        with self._lock:
            # 从这里开始进入临界区
            task = self.tasks.get(task_id) # 获取任务状态
            # 任务不存在，直接返回
            if task is None:
                return
            # 更新任务状态
            task["status"] = status
            # 更新任务结果
            self.results[task_id] = result
            # 任务完成，添加到已完成列表
            self._ready.append(task_id)
    
    def collect(self) -> list[str]:
        """
        收集已完成的任务结果。
        :return: 已完成任务的列表，每个元素是一个元组，包含任务 ID、任务状态和结果。
        """
        with self._lock:
            ready = []

            for task_id in self._ready:
                task = self.tasks.pop(task_id, None)  # 从任务映射表中移除任务
                result = self.results.pop(task_id, "")  # 从任务结果映射表中移除结果

                if task is not None:
                    ready.append((task_id, task, result))

            self._ready.clear()

        # 生成通知
        notifications = []  
        #因为模型在训练中见过大量 XML/HTML，
        #这种结构它解析得最准，也容易区分"系统注入的事件"和用户说的话。
        for task_id, task, result in ready:
            notifications.append(
                f"<task_notification>\n"
                f"  <task_id>{task_id}</task_id>\n"
                f"  <status>{task['status']}</status>\n"
                f"  <command>{task['command']}</command>\n"
                f"  <summary>{result[:500]}</summary>\n"
                f"</task_notification>"
            )
            print(f"  [background] collected {task_id}: {task['status']}")

        return notifications

BACKGROUND = BackgroundManager()

def collect_background_results() -> list[str]:
    """取走已经执行完的后台任务通知，每条通知只取一次。
    :return: 已完成任务的列表，每个元素是一个 XML 字符串，包含任务 ID、状态、命令和摘要。
    """
    return BACKGROUND.collect()

def inject_background_results(messages: list) -> int:
    """把已结束的后台任务通知加入对话，返回加入了几条。
    :param messages: 对话消息列表，每个元素是一个字典，包含角色和内容。
    :return: 加入的后台任务通知数量。
    """
    notifications = collect_background_results()

    # 可能还没有任务完成。这时保留消息原样，主循环可以继续。
    if not notifications:
        return 0

    # 后台命令启动时已经回过一次 tool_result，其中包含后台任务 ID。
    # 这里的完成通知是后来发生的事件，用普通文本块随下一次请求送出。
    blocks = [
        {"type": "text", "text": notification}
        for notification in notifications
    ]

    if messages and messages[-1].get("role") == "user":
        last_message = messages[-1]
        content = last_message.get("content", "")

        if isinstance(content, list):
            # 上一批工具结果已经在列表里。把通知接在后面，
            # 保留原来的 tool_result 和它们的调用 ID。
            content.extend(blocks)
        else:
            # 用户刚输入的问题通常是字符串。先保留为第一个文本块，
            # 再把完成通知放在它后面。
            last_message["content"] = [
                {"type": "text", "text": str(content)},
                *blocks,
            ]
    else:
        # 单独使用这个函数时，末尾可能没有可追加的 user 消息。
        messages.append({"role": "user", "content": blocks})

    return len(notifications)
        
    

    


