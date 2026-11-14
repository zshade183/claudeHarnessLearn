import secrets
import threading
from collections import deque
from collections.abc import Callable

from agent import tasks
from agent.config import ENVIRONMENT_PROMPT, TOOL_CWD, WORKDIR
from agent.mailboxes import MessageBus, VALID_AGENT_NAME
from agent.subagents import run_subagent_turn
from agent.tools.base import BASE_HANDLERS
from agent.tools.executor import execute_tool
from agent.tools.task_tools import TASK_HANDLERS, TASK_TOOLS


TEAMMATE_TASK_TOOLS = [
    tool for tool in TASK_TOOLS
    if tool["name"] in {"list_tasks", "get_task", "claim_task", "complete_task"}
]
PLAN_BLOCKED_TOOLS = {"bash", "write_file", "edit_file", "claim_task", "complete_task"}


class TeammateShutdown(Exception):
    """关机已确认，用来结束队友当前的循环。"""


class TeammatePlanRequired(Exception):
    """Lead 要求先交计划，暂停当前工作回合。"""


class TeammateRuntime:
    """队友保留自己的对话，处理来信，并在空闲时认领任务。"""

    def __init__(
        self,
        name: str,
        bus: MessageBus,
        run_turn: Callable[[list], str] | None = None,
        *,
        shutdown_matches: Callable[[str, str], bool] | None = None,
    ):
        self.name = name
        self.bus = bus
        self.run_turn = self.run_work_turn if run_turn is None else run_turn
        self.shutdown_matches = shutdown_matches
        self.messages: list[dict] = []
        self._inbox: deque[dict] = deque()
        self._plan_lock = threading.Lock()
        self._plan_request_id: str | None = None
        self.pending_plan_id: str | None = None
        self._plan_task_id: str | None = None
        self.system = (
            f"You are teammate {name}. {ENVIRONMENT_PROMPT} "
            "Use the claimed task's working directory and relative paths. "
            "If you need to inspect or edit files, claim a task first. "
            "Follow Lead's plan decisions. Complete your task by its exact ID "
            "after checking your work, then report the result to Lead."
        )

    def claim_task(self, task_id: str) -> str:
        result = tasks.claim_task(task_id, owner=self.name, hold_assignment=True)
        if result.startswith("Claimed "):
            return f"{result}\n{self.assignment_text(tasks.load_task(task_id))}"
        return result

    def finish_task(self, task_id: str) -> str:
        # 模型提供任务 ID，负责人由当前队友的名字确定。
        return tasks.complete_task(task_id, owner=self.name)

    def assignment_text(self, task: tasks.Task) -> str:
        assignment = tasks.get_assignment(self.name)
        cwd = assignment.cwd if assignment is not None else WORKDIR
        return (
            f"[Assigned task {task.id}] {task.subject}\n"
            f"{task.description}\nWorking directory: {cwd}\n"
            "After verifying the work is done, call complete_task with this task ID."
        )

    def dispatch_tool(self, block, handlers: dict) -> str:
        with self._plan_lock:
            if self._plan_request_id is not None and block.name in PLAN_BLOCKED_TOOLS:
                return "Blocked: Lead must approve the plan before you can do this."
            if block.name in {"claim_task", "complete_task"}:
                # 修改任务状态和发起计划互斥，计划才能记住正确的任务 ID。
                return execute_tool(block, handlers)

        if block.name not in BASE_HANDLERS or block.name not in handlers:
            return execute_tool(block, handlers)

        try:
            assignment = tasks.get_assignment(self.name)
        except (OSError, ValueError) as error:
            return f"Cannot use workspace tools: {error}"
        if assignment is None:
            return "Claim a task before using workspace tools."

        # 只给当前工具调用传目录。其他队友和主代理仍使用各自的目录。
        token = TOOL_CWD.set(assignment.cwd)
        try:
            return execute_tool(block, handlers)
        finally:
            TOOL_CWD.reset(token)

    def check_shutdown(self, _messages: list) -> None:
        """在请求模型前检查 Lead 发来的有效关机请求。"""
        self._inbox.extend(self.bus.read_inbox(self.name))
        for message in self._inbox:
            if (
                message["type"] == "shutdown_request"
                and message["from"] == "lead"
                and self._acknowledge_shutdown(message)
            ):
                raise TeammateShutdown

    def collect_work_messages(self, messages: list) -> None:
        """把普通来信追加到即将发给模型的对话。"""
        self.check_shutdown(messages)

        with self._plan_lock:
            if self._plan_request_id is not None:
                raise TeammatePlanRequired

        blocks = []

        # 控制消息留在队首，由外层循环按顺序处理。
        while self._inbox and self._inbox[0]["type"] == "message":
            message = self._inbox.popleft()
            blocks.append({
                "type": "text",
                "text": f"[Message from {message['from']}] {message['content']}",
            })

        if not blocks:
            return

        if messages and messages[-1]["role"] == "user":
            last = messages[-1]
            if isinstance(last["content"], str):
                last["content"] = [{"type": "text", "text": last["content"]}]
            # 工具结果保留在前面，来信作为文本块接在后面。
            last["content"].extend(blocks)
        else:
            messages.append({"role": "user", "content": blocks})

    def run_work_turn(self, messages: list) -> str:
        return run_subagent_turn(
            messages,
            extra_tools=TEAMMATE_TASK_TOOLS,
            extra_handlers={
                "list_tasks": TASK_HANDLERS["list_tasks"],
                "get_task": TASK_HANDLERS["get_task"],
                "claim_task": self.claim_task,
                "complete_task": self.finish_task,
            },
            dispatch=self.dispatch_tool,
            before_model_call=self.collect_work_messages,
            max_model_calls=None,
            system=self.system,
        )

    def request_plan(self, task: str) -> str:
        """发出请求时立刻关上执行入口，即使队友还没来得及读信。"""
        request_id = f"req_{secrets.token_hex(4)}"
        with self._plan_lock:
            assignment = tasks.get_assignment(self.name)
            self.bus.send(
                "lead", self.name, task,
                msg_type="plan_request", metadata={"request_id": request_id},
            )
            self._plan_request_id = request_id
            self.pending_plan_id = None
            self._plan_task_id = assignment.task_id if assignment else None
        return request_id

    def draft_plan(self, task: str, request_id: str) -> None:
        with self._plan_lock:
            if request_id != self._plan_request_id or self.pending_plan_id is not None:
                return

        self.messages.append({
            "role": "user",
            "content": (
                f"[Plan requested by lead] {task}\n\n"
                "Describe the changes and how to verify them. "
                "Return the plan and wait for approval."
            ),
        })
        plan = run_subagent_turn(
            self.messages,
            allowed_tools={"read_file", "glob"},
            dispatch=self.dispatch_tool,
            before_model_call=self.check_shutdown,
            system=self.system,
        )

        with self._plan_lock:
            # 写计划时 Lead 可能发了新请求。只提交最新请求的计划。
            if request_id != self._plan_request_id:
                return
            self.pending_plan_id = request_id
            self.bus.send(
                self.name, "lead", f"Plan {request_id}:\n{plan}",
                msg_type="plan_approval_request",
                metadata={"request_id": request_id, "task_id": self._plan_task_id},
            )

    def apply_plan_review(self, message: dict) -> bool:
        metadata = message["metadata"]
        approve = metadata.get("approve")
        with self._plan_lock:
            if (
                message["from"] != "lead"
                or message["to"] != self.name
                or self.pending_plan_id is None
                or metadata.get("request_id") != self.pending_plan_id
                or not isinstance(approve, bool)
            ):
                return False

            assignment = tasks.get_assignment(self.name)
            if (assignment.task_id if assignment else None) != self._plan_task_id:
                return False

            if approve:
                self._plan_request_id = None
                self.pending_plan_id = None
                self._plan_task_id = None
                return True

            # 驳回仍然锁住写入。修订稿用新编号，旧回执不会批准修订稿。
            request_id = f"req_{secrets.token_hex(4)}"
            self.bus.send(
                "lead", self.name,
                f"Revise the previous plan using this feedback:\n{message['content']}",
                msg_type="plan_request", metadata={"request_id": request_id},
            )
            self._plan_request_id = request_id
            self.pending_plan_id = None
            return False

    def assigned_message(self, task: tasks.Task) -> dict:
        return {
            "from": "task_store",
            "to": self.name,
            "type": "message",
            "content": self.assignment_text(task),
            "metadata": {"task_id": task.id},
        }

    def _acknowledge_shutdown(self, message: dict) -> bool:
        request_id = message["metadata"].get("request_id")
        if not isinstance(request_id, str):
            return False
        if self.shutdown_matches is not None and not self.shutdown_matches(self.name, request_id):
            return False

        self.bus.send(
            self.name, "lead", f"Shutdown acknowledged ({request_id}).",
            msg_type="shutdown_response", metadata=message["metadata"],
        )
        return True

    def run(self, initial_task: tasks.Task | None = None) -> None:
        try:
            self._run(initial_task)
        except TeammateShutdown:
            pass  # 关机确认已发送，由 finally 释放任务分配。
        except Exception as error:
            self.bus.send(self.name, "lead", str(error), msg_type="error")
        finally:
            tasks.release_teammate_assignment(self.name)

    def _run(self, initial_task: tasks.Task | None) -> None:
        if initial_task is not None:
            self._inbox.append(self.assigned_message(initial_task))

        while True:
            # 处理邮箱中的消息。
            if not self._inbox:
                # 有信先处理。邮箱等待超时以后，空闲队友才尝试找新任务。
                self._inbox.extend(
                    self.bus.wait_for_messages(self.name, timeout=2.0)
                )
                # 如果邮箱为空，尝试找新任务。
                if not self._inbox:
                    with self._plan_lock:
                        task = (
                            tasks.claim_next_task(self.name)
                            if self._plan_request_id is None else None
                        )
                    if task is not None:
                        self._inbox.append(self.assigned_message(task))
            # 处理邮箱中的消息。
            while self._inbox:
                message = self._inbox.popleft()
                kind = message["type"]
                from_lead = message["from"] == "lead"
                if kind == "shutdown_request" and from_lead:
                    if self._acknowledge_shutdown(message):
                        return
                    continue
                if kind == "plan_request" and from_lead:
                    request_id = message["metadata"].get("request_id")
                    if isinstance(request_id, str):
                        self.draft_plan(message["content"], request_id)
                    continue

                content = message["content"]
                if kind == "plan_approval_response":
                    if not self.apply_plan_review(message):
                        continue
                    content = f"Plan approved. Carry out your submitted plan.\n{content}"
                elif kind != "message":
                    continue

                with self._plan_lock:
                    waiting = self._plan_request_id is not None
                if waiting:
                    self.bus.send(
                        self.name, "lead", "Waiting for plan approval.",
                        msg_type="waiting_approval",
                    )
                    continue

                self.messages.append({
                    "role": "user",
                    "content": f"[Message from {message['from']}] {content}",
                })
                try:
                    result = self.run_turn(self.messages)
                except TeammatePlanRequired:
                    result = None

                # complete_task 先记完成。等模型回合返回后，才释放工作目录。
                with self._plan_lock:
                    released = tasks.release_completed_assignment(self.name)
                    if released and self._plan_task_id is not None:
                        self._plan_request_id = None
                        self.pending_plan_id = None
                        self._plan_task_id = None
                    waiting = self._plan_request_id is not None

                if result is None:
                    # 工具结果已保留，回到收件箱处理计划请求。
                    continue

                self.bus.send(self.name, "lead", result, msg_type="result")
                if not waiting:
                    self.bus.send(
                        self.name, "lead", "Waiting for more work.",
                        msg_type="idle_notification",
                    )


class TeamManager:
    """团队管理器，用于管理团队成员。
    :param bus: 消息总线，用于发送和接收消息。
    """
    def __init__(self, bus: MessageBus):
        self.bus = bus

        # 用队友名字找到线程。线程是否还活着，由 is_alive() 判断。
        self._threads: dict[str, threading.Thread] = {}
        self._runtimes: dict[str, TeammateRuntime] = {}
        self._lock = threading.Lock()
        # 队友名对应尚未确认的关闭请求编号。
        self._pending_shutdowns: dict[str, str] = {}

    def spawn_teammate(
        self,
        name: str,
        *,
        task_id: str | None = None,
        require_plan: bool = False,
        run_turn: Callable[[list], str] | None = None,
    ) -> threading.Thread:
        """创建一个新团队成员线程。
        :param name: 团队成员名称，只能包含字母、数字和下划线。
        :param run_turn: 可替换的工作回合函数，默认使用队友自己的工具。
        :return: 新创建的线程对象。
        """
        # 名字会用于收件箱文件名，沿用上一节的校验规则。
        if not VALID_AGENT_NAME.fullmatch(name):
            raise ValueError(f"Invalid teammate name: {name!r}")

        # 创建和发信都使用小写，Reader 和 reader 指向同一个队友。
        name = name.lower()
        if name in {"lead", "agent"}:
            raise ValueError(f"Reserved teammate name: {name!r}")
        if require_plan and task_id is None:
            raise ValueError("An initial task_id is required when require_plan is true")

        with self._lock:
            current = self._threads.get(name)
            if current is not None and current.is_alive():
                raise ValueError(f"Teammate {name!r} is already running")

            runtime = TeammateRuntime(
                name,
                self.bus,
                run_turn=run_turn,
                shutdown_matches=self.match_shutdown,
            )

            task = None
            if task_id is not None:
                result = tasks.claim_task(task_id, owner=name, hold_assignment=True)
                if not result.startswith("Claimed "):
                    raise ValueError(result)

            try:
                if task_id is not None:
                    task = tasks.load_task(task_id)
                else:
                    assignment = tasks.get_assignment(name)
                    if assignment is not None:
                        task = tasks.load_task(assignment.task_id)
                if require_plan and task is not None:
                    # 在线程开始前挂上计划要求，第一轮也不能先改后报。
                    runtime.request_plan(runtime.assignment_text(task))

                worker = threading.Thread(
                    target=runtime.run,
                    args=(None if require_plan else task,),
                    name=f"teammate-{name}",
                    daemon=True,
                )
                self._pending_shutdowns.pop(name, None)
                worker.start()
            except Exception:
                if task_id is not None or task is not None:
                    tasks.release_teammate_assignment(name)
                raise
            self._threads[name] = worker
            self._runtimes[name] = runtime
            return worker

    def list_teammates(self) -> list[str]:
        with self._lock:
            return [
                name
                for name, worker in self._threads.items()
                if worker.is_alive()
            ]

    def send_message(
        self,
        to: str,
        content: str,
        *,
        msg_type: str = "message",
        metadata: dict | None = None,
    ) -> None:
        """发送一条消息给指定的队友。
        :param to: 团队成员名称。
        :param content: 消息内容。
        :param msg_type: 消息类型，默认 message。
        :return: None
        """
        to = to.lower()

        with self._lock:
            worker = self._threads.get(to)
            if worker is None or not worker.is_alive():
                raise ValueError(f"Teammate {to!r} is not active")

            # 管理器以 lead 的身份发信。收件人醒来后自行处理。
            if msg_type == "plan_request":
                self._runtimes[to].request_plan(content)
            else:
                self.bus.send("lead", to, content, msg_type=msg_type, metadata=metadata)
    
    def request_shutdown(self, teammate: str) -> str:
        """请求一个队友关闭当前工作。
        :param teammate: 团队成员名称。
        :return: 请求编号，用于后续确认。
        """
        name = teammate.lower()

        with self._lock:
            # 如果已经发过关闭请求，沿用原来的编号。
            existing = self._pending_shutdowns.get(name)
            if existing is not None:
                return existing
            worker = self._threads.get(name)
            if worker is None or not worker.is_alive():
                raise ValueError(f"Teammate {name!r} is not active")

            # 把编号放进消息，队友回信时可以原样带回来。
            request_id = f"req_{secrets.token_hex(4)}"
            self.bus.send(
                "lead",
                name,
                "Finish the current work and shut down.",
                msg_type="shutdown_request",
                metadata={"request_id": request_id},
            )

            # 记录下请求，等队友回执后删除。
            self._pending_shutdowns[name] = request_id

        return request_id
    
    def match_shutdown(
        self,
        name: str,
        request_id: str | None,
        *,
        confirm: bool = False,
    ) -> bool:
        """确认一个队友关闭当前工作。
        :param name: 团队成员名称。
        :param request_id: 请求编号。
        :param confirm: 是否确认关闭。
        :return: 是否成功确认关闭。
        """
        with self._lock:
            if not request_id:
                return False
            if self._pending_shutdowns.get(name) != request_id:
                return False

            # 收到正确回执后取走记录。同一封回执只能确认一次。
            if confirm:
                del self._pending_shutdowns[name]

            return True

# 创建一次。发信的工具和收信的主循环都使用这个对象。
TEAM = TeamManager(MessageBus(WORKDIR / ".mailboxes"))


def inject_team_messages(messages: list) -> None:
    """将团队成员的回复注入到对话中。
    :param messages: 对话列表，每个元素是一个字典，包含 role 和 content 键。
    :return: None
    """
    # 取走已经送到 lead 的回信。没有回信时，马上继续。
    inbox = TEAM.bus.read_inbox("lead")
    inbox = [
        item for item in inbox
        if item["type"] != "shutdown_response"
        or (
            item["to"] == "lead"
            and TEAM.match_shutdown(
                item["from"],
                item["metadata"].get("request_id"),
                confirm=True,
            )
        )
    ]
    if not inbox:
        return

    blocks = [
        {
            "type": "text",
            "text": f"[Team {item['type']} from {item['from']}] {item['content']}",
        }
        for item in inbox
    ]

    if messages and messages[-1].get("role") == "user":
        last = messages[-1]
        content = last["content"]

        # 用户刚输入一句话时，先把字符串转成文本块。
        # 如果已经是工具结果列表，就直接往后追加回信。
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
            last["content"] = content

        content.extend(blocks)
    else:
        messages.append({"role": "user", "content": blocks})
