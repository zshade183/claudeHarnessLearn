"""
按收件人存放消息，让队友可以稍后取走
收件箱模块，用于处理消息的发送和接收。
"""

import json
import re
import threading
import time
from pathlib import Path


# 收件人的名字会用作文件名，因此只接受这一组字符。 
VALID_AGENT_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")


class MessageBus:
    """消息总线，用于发送和接收消息。"""
    def __init__(self, directory: Path):
        # 生产环境会传入 .mailboxes，测试可以传入临时目录。
        self.directory = directory.resolve()
        # 用于保护文件操作的锁。RLock 允许递归加锁，
        # 用于处理文件操作时的并发问题。
        # 例如，一个线程可能正在写入文件，而线程 B 正在读取文件。
        # 如果不使用 RLock，线程 B 会阻塞，直到线程 A 完成写入。
        # 但是，使用 RLock 后，线程 B 可以继续读取文件，
        # 而不会阻塞线程 A。
        self._lock = threading.RLock()  
        # 条件变量和收发消息共用一把锁，
        # 保证“检查是否有消息”和“开始等待”之间不会漏掉通知。Condition
        # 用于等待线程，直到条件满足。
        # 例如，当收件人收到新消息时，会唤醒等待中的线程。
        # 被唤醒的线程会各自检查自己的收件箱。
        # 如果有新消息，会继续执行；如果没有新消息，会继续等待。
        # 这样，就可以确保“检查是否有消息”和“开始等待”之间不会漏掉通知。
        self._changed = threading.Condition(self._lock)

    def _path(self, recipient: str) -> Path:
        """根据收件人名称返回收件箱文件路径。
        
        :param recipient: 收件人名称，只能包含字母、数字和下划线。
        :return: 收件箱文件路径。
        """
        if not VALID_AGENT_NAME.fullmatch(recipient):
            raise ValueError(f"Invalid mailbox recipient: {recipient!r}")

        path = (self.directory / f"{recipient}.jsonl").resolve()

        # 即使名称合法，也要确认解析后的文件仍位于收件箱目录内。
        if not path.is_relative_to(self.directory):
            raise ValueError(f"Mailbox path escapes directory: {recipient!r}")

        return path
    
    def send(
        self,
        from_agent: str,  # 发件人名称
        to_agent: str,  # 收件人名称
        content: str,  # 消息内容
        msg_type: str = "message",  # 消息类型
        metadata: dict | None = None,  # 消息元数据
    ) -> None:
        message = {
            "from": from_agent,
            "to": to_agent,
            "content": content,
            "type": msg_type,
            "ts": time.time(),
            "metadata": metadata or {},
        }
        
        with self._changed:  # 确保收发人共用一把锁，
            # 保证“检查是否有消息”和“开始等待”之间不会漏掉通知。
            inbox = self._path(to_agent)
            self.directory.mkdir(parents=True, exist_ok=True)

            with inbox.open("a", encoding="utf-8") as handle:
                # 写入消息到文件，每个消息占一行。
                # 每个消息是一个 JSON 字符串，用 ensure_ascii=False 确保中文能正常显示。
                # 每个消息后面都有一个换行符，方便后续读取。
                # 例如：
                # {"from": "Agent1", "to": "Agent2", "content": "你好", "type": "message", "ts": 1694502400.0, "metadata": {}}\n
                # {"from": "Agent2", "to": "Agent1", "content": "你好，Agent1", "type": "message", "ts": 1694502401.0, "metadata": {}}\n
                # ...
                handle.write(json.dumps(message, ensure_ascii=False) + "\n")

            # 文件已经写好并关闭，现在唤醒等待中的线程。
            # 被唤醒的线程会各自检查自己的收件箱。
            self._changed.notify_all()

    def read_inbox(self, agent: str) -> list[dict]:
        """读取收件人收件箱中的所有消息。
        :param agent: 收件人名称，只能包含字母、数字和下划线。
        :return: 收件人收件箱中的所有消息，每个消息是一个字典。
        """
        with self._lock:
            inbox = self._path(agent)
            if not inbox.exists():
                return []

            # 先把每行解析成字典。全部读成功后，才清掉这个收件箱。
            with inbox.open(encoding="utf-8") as handle:
                messages = [
                    json.loads(line)
                    for line in handle
                    if line.strip()
                ]

            inbox.unlink()
            return messages
    
    def peek(self, agent: str) -> bool:
        """查看这个收件箱是否有内容，保留文件供后续读取。
        :param agent: 收件人名称，只能包含字母、数字和下划线。
        :return: 如果收件箱有内容，返回 True；否则返回 False。
        """
        with self._lock:
            inbox = self._path(agent)
            return inbox.exists() and inbox.stat().st_size > 0

    def wait_for_messages(
        self,
        agent: str,
        timeout: float | None = None,
    ) -> list[dict]:
        """等到这个收件箱有消息，或等待超时，然后返回消息列表。
        :param agent: 收件人名称，只能包含字母、数字和下划线。
        :param timeout: 等待超时时间，单位为秒。默认 None，即无限等待。
        :return: 收件人收件箱中的所有消息，每个消息是一个字典。
        先检查。 如果收件箱已经有消息，直接往下读。
        没有就等待。 等待期间会释放锁，发送线程才能拿锁写入消息。
        收到通知后再次检查。 收件箱有消息才继续，否则继续等。
        超时返回。 超过 timeout 秒仍没有消息，就得到 False。
        """
        with self._changed:
            available = self._changed.wait_for(
                lambda: self.peek(agent),
                timeout=timeout,
            )

            if not available:
                return []

            # 仍持有同一把锁，检查成功后立即取走消息。
            # RLock 允许 read_inbox() 在当前线程里再次获取这把锁。
            return self.read_inbox(agent)