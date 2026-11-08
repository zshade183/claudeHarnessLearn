"""
任务系统（task store）
一个任务 = .tasks/{id}.json 一个文件。
主要解决两个问题：
1.当前问题是什么？
2.当前问题的依赖任务有哪些？
"""
from dataclasses import asdict, dataclass
import json
import re
import secrets
from pathlib import Path

from agent.config import TASKS_DIR, WORKDIR

@dataclass
class Task:
    id: str     
    subject: str  # 任务主题
    description: str  # 任务描述
    status: str          # pending | in_progress | completed
    owner: str | None    # 认领它的 agent 名
    blockedBy: list[str] # 前置任务 ID 列表

# 任务 ID 格式：task_8位随机数
TASK_ID_PATTERN = re.compile(r"^task_[0-9a-f]{8}$")


class TaskStore:
    """任务存储系统"""
    def __init__(self, directory: Path):
        self.directory = directory

    def _root(self, create: bool = False) -> Path:
        """任务目录；可创建，但必须位于工作目录内。"""
        if create:
            self.directory.mkdir(parents=True, exist_ok=True)

        # 解析目录路径，确保它是一个绝对路径
        root = self.directory.resolve()

        # 检查目录是否位于工作目录内
        if not root.is_relative_to(WORKDIR.resolve()):
            raise ValueError("Task store escapes the workspace")

        return root

    def _path(self, task_id: str, create_root: bool = False) -> Path:
        """把任务 ID 解析成文件路径（两层校验）。"""
        if (
            not isinstance(task_id, str)
            or not TASK_ID_PATTERN.fullmatch(task_id)
        ):
            raise ValueError(f"Invalid task ID: {task_id!r}")

        root = self._root(create=create_root)
        path = (root / f"{task_id}.json").resolve()

        if not path.is_relative_to(root):
            raise ValueError(f"Invalid task ID: {task_id!r}")

        return path

    def exists(self, task_id: str) -> bool:
        """检查任务是否存在"""
        return self._path(task_id).is_file()


    def create(self, subject: str, description: str = "") -> Task:
        """创建一个新任务
        造一个新任务，随机分配 ID，写进硬盘，把完整任务返回给调用方。
        :param subject: 任务主题
        :param description: 任务描述，默认为空字符串
        :return: 新创建的任务
        
        :return: Task
        """
        # 移除首尾空格，数据清洗
        subject = subject.strip()

        # 检查主题是否为空
        if not subject:
            raise ValueError("Task subject cannot be empty")

        self._root(create=True)

        for _ in range(100):
            task = Task(
                id=f"task_{secrets.token_hex(4)}",  # 任务 ID 格式：task_8位随机数
                subject=subject,
                description=description,
                status="pending",
                owner=None,
                blockedBy=[],
            )

            try:
                with self._path(task.id, create_root=True).open(
                    "x", encoding="utf-8" # 写入模式，创建新文件 排他锁
                ) as handle:
                    json.dump(asdict(task), handle, indent=2)
                return task
            except FileExistsError:
                # 任务 ID 已存在，重试
                continue

        raise RuntimeError("Could not allocate a unique task ID")

    def save(self, task: Task) -> None:
        """保存任务到硬盘
        把一个内存里的 Task 对象，覆盖写回它对应的 JSON 文件
        :param task: 要保存的任务
        """
        self._path(task.id, create_root=True).write_text(
            json.dumps(asdict(task), indent=2),
            encoding="utf-8",
        )

    def load(self, task_id: str) -> Task:
        """从硬盘加载任务
        从 JSON 文件读取任务数据，返回一个 Task 对象
        :param task_id: 任务 ID
        :return: 任务对象
        """
        data = json.loads(
            self._path(task_id).read_text(encoding="utf-8")
        )
        task = Task(**data)

        if task.id != task_id:
            raise ValueError(f"Task file ID does not match {task_id}")

        if task.status not in ("pending", "in_progress", "completed"):
            raise ValueError(f"Invalid task status: {task.status}")

        return task

    def list_all(self) -> list[Task]:
        """列出所有任务
        :return: 所有任务的列表
        """
        if not self.directory.exists():
            return []

        root = self._root()

        return [
            self.load(path.stem)
            for path in sorted(root.glob("task_*.json"))
        ]

    def _depends_on(self, task_id: str, target_id: str) -> bool:
        """task_id 是否（传递地）依赖 target_id —— 环检测用。
        :param task_id: 任务 ID
        :param target_id: 目标任务 ID
        :return: 是否依赖 target_id
        :return: bool
        """
        pending = [task_id]
        visited = set()

        while pending:
            current = pending.pop()

            if current == target_id:
                return True

            if current in visited:
                continue

            visited.add(current)
            pending.extend(self.load(current).blockedBy)

        return False

    def update_dependencies(
        self,
        task_id: str,
        add_blocked_by: list[str],
    ) -> Task:
        """更新任务依赖
        :param task_id: 任务 ID
        :param add_blocked_by: 要添加的依赖任务 ID 列表
        :return: 更新后的任务对象
        """
        # 参数校验 + 读出任务 + 冻结检查
        if not isinstance(add_blocked_by, list):
            raise ValueError("addBlockedBy must be a list of task IDs")

        task = self.load(task_id)
        #只有在任务" 还没开始 + 还没被认领 "时，才允许改它的`blockedBy` （前置依赖）
        if task.status != "pending" or task.owner is not None:
            raise ValueError(
                f"Task {task_id} dependencies can only be updated while "
                "pending and unowned"
            )
        
        # 合并依赖任务 ID 列表，去重
        #dict.fromkeys(["a", "b", "a"]) → {"a": None, "b": None} dict 的键天然不能重复，所以这一步顺便去重了。
        dependencies = list(dict.fromkeys(add_blocked_by))

        # 检查依赖任务是否存在
        for dependency in dependencies:
            if dependency == task_id:  # 任务不能依赖自己
                raise ValueError("Task cannot depend on itself")

            if not self.exists(dependency):  # 依赖任务必须存在
                raise ValueError(f"Dependency not found: {dependency}")

            if (  # 检查依赖任务是否存在循环依赖
                dependency not in task.blockedBy
                and self._depends_on(dependency, task_id)
            ):
                raise ValueError(
                    f"Dependency cycle detected: {task_id} -> {dependency}"
                )
        
        task.blockedBy.extend(
            dependency
            for dependency in dependencies
            if dependency not in task.blockedBy
        )
        self.save(task)
        return task
    
    
    
TASKS = TaskStore(TASKS_DIR)

def create_task(subject: str, description: str = "") -> Task:
    """创建一个新任务
    :param subject: 任务主题
    :param description: 任务描述
    :return: 创建的任务对象
    """
    return TASKS.create(subject, description)

def update_task(task_id: str, addBlockedBy: list[str]) -> Task:
    """更新任务依赖
    :param task_id: 任务 ID
    :param addBlockedBy: 要添加的依赖任务 ID 列表
    :return: 更新后的任务对象
    """
    return TASKS.update_dependencies(task_id, addBlockedBy)


def load_task(task_id: str) -> Task:
    """从硬盘加载任务
    :param task_id: 任务 ID
    :return: 任务对象
    """
    return TASKS.load(task_id)


def list_tasks() -> list[Task]:
    """列出所有任务
    :return: 所有任务的列表
    """
    return TASKS.list_all()


def get_task(task_id: str) -> str:
    """获取任务详情
    :param task_id: 任务 ID
    :return: 任务详情的 JSON 字符串
    """
    return json.dumps(asdict(load_task(task_id)), indent=2)

def incomplete_dependencies(task: Task) -> list[str]:
    """检查任务是否有未完成的依赖
    :param task: 任务对象
    :return: 未完成依赖任务 ID 列表
    """
    incomplete = []

    for dependency in task.blockedBy:
        try:
            if load_task(dependency).status != "completed":
                incomplete.append(dependency)
        except (FileNotFoundError, ValueError):
            incomplete.append(dependency)

    return incomplete

def can_start(task_id: str) -> bool:
    """检查任务是否可以开始
    :param task_id: 任务 ID
    :return: 是否可以开始
    """
    return not incomplete_dependencies(load_task(task_id))

def claim_task(task_id: str, owner: str = "agent") -> str:
    """认领任务
    :param task_id: 任务 ID
    :param owner: 任务领者
    :return: 认领结果
    """
    task = load_task(task_id)

    if task.status != "pending":
        return f"Task {task_id} is {task.status}, cannot claim"

    dependencies = incomplete_dependencies(task)

    if dependencies:
        return f"Blocked by: {dependencies}"

    task.owner = owner
    task.status = "in_progress"

    TASKS.save(task)

    print(
        f"  [claim] {task.subject} -> in_progress "
        f"(owner: {owner})"
    )

    return f"Claimed {task.id} ({task.subject})"

def complete_task(task_id: str, owner: str = "agent") -> str:
    """完成任务
    仅当任务状态为 in_progress 且领者为 owner 时才能完成。
    完成后，会检查是否有其他任务依赖于该任务，
    :param task_id: 任务 ID
    :param owner: 任务领者
    :return: 完成结果
    """
    task = load_task(task_id)  

    if task.status != "in_progress":  
        return f"Task {task_id} is {task.status}, cannot complete"

    if task.owner != owner:
        return f"Task {task_id} is owned by {task.owner}, not {owner}"
    
    # 检查是否有其他任务依赖于该任务
    ready_before = {
        candidate.id
        for candidate in list_tasks()
        if candidate.status == "pending"  # 只检查未完成任务
        and candidate.blockedBy  # 只检查有依赖的任务
        and can_start(candidate.id)  # 只检查可以开始的任务
    }

    task.status = "completed"
    TASKS.save(task)

    unblocked = []  # 未被阻塞的任务列表
    for candidate in list_tasks():
        if (
            candidate.status == "pending"
            and candidate.blockedBy
            and candidate.id not in ready_before
            and can_start(candidate.id)
        ):
            unblocked.append(candidate.subject)  # 记录未被阻塞的任务

    print(f"  [complete] {task.subject}")
    message = f"Completed {task.id} ({task.subject})"

    if unblocked:
        names = ", ".join(unblocked)
        print(f"  [unblocked] {names}")
        message += f"\nUnblocked: {names}"

    return message
