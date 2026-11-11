"""用 Git 创建和校验任务工作目录。任务状态仍由 tasks 模块保存。"""

import re
import subprocess
from contextlib import ExitStack
from pathlib import Path

from agent import tasks
from agent.config import WORKDIR


VALID_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def _worktree_path(name: str) -> Path:
    if not isinstance(name, str) or not VALID_NAME.fullmatch(name) or ".." in name:
        raise ValueError("Invalid worktree name")

    root = WORKDIR.resolve() / ".worktrees"
    path = root / name
    # 已存在的链接或联接点不能把任务目录指向别处。
    if root.resolve() != root or path.resolve() != path:
        raise ValueError(f"Worktree path redirects to another location: {path}")
    return path


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """把参数逐项传给 Git，目录名里的空格不会被拆开。"""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd or WORKDIR,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"Git timed out: {error.cmd}") from error


def _git_error(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or f"Git exited with {result.returncode}").strip()


def _registered_worktrees() -> dict[Path, str]:
    # -z 以空字符分隔字段，带空格、中文的路径可以原样读取。
    result = _git("worktree", "list", "--porcelain", "-z")
    if result.returncode != 0:
        raise ValueError(_git_error(result))

    worktrees: dict[Path, str] = {}
    for record in result.stdout.split("\0\0"):
        fields = dict(field.split(" ", 1) for field in record.split("\0") if " " in field)
        if "worktree" in fields:
            worktrees[Path(fields["worktree"]).resolve()] = fields.get("branch", "")
    return worktrees


def _registered_path(name: str) -> Path:
    path = _worktree_path(name)
    branch = f"refs/heads/wt/{name}"
    if not path.is_dir() or _registered_worktrees().get(path) != branch:
        raise ValueError(f"Worktree '{name}' is missing or is not registered on {branch}")
    checkout = _git("rev-parse", "--show-toplevel", "--symbolic-full-name", "HEAD", cwd=path)
    lines = checkout.stdout.splitlines()
    if (
        checkout.returncode != 0 or len(lines) != 2
        or Path(lines[0]).resolve() != path or lines[1] != branch
    ):
        raise ValueError(f"Worktree '{name}' is not a usable checkout on {branch}")
    return path


def task_worktree_cwd(task: tasks.Task) -> Path:
    """任务有绑定时必须通过校验。只有从未绑定的任务才使用主目录。"""
    if task.worktree is None:
        return WORKDIR.resolve()
    return _registered_path(task.worktree)


def create_worktree(name: str, task_id: str) -> str:
    """为一张待认领的任务创建并绑定 Git 工作目录。"""
    try:
        path = _worktree_path(name)
        branch = f"wt/{name}"
        ref = f"refs/heads/{branch}"

        # 认领也用这把锁，队友会等到目录创建和任务绑定都完成。
        with tasks.task_store_lock():
            task = tasks.TASKS.load(task_id)
            if task.status != "pending" or task.owner is not None:
                return f"Error: Task {task_id} must be pending and unowned"
            if task.worktree is not None:
                return f"Error: Task {task_id} already uses '{task.worktree}'"
            if any(
                other.worktree is not None and path.parent / other.worktree == path
                for other in tasks.TASKS.list_all()
            ):
                return f"Error: Worktree '{name}' is already bound to a task"

            git_root = _git("rev-parse", "--show-toplevel")
            if git_root.returncode != 0 or Path(git_root.stdout.strip()).resolve() != WORKDIR.resolve():
                return "Error: Working directory must be the root of a Git repository"
            branch_check = _git("check-ref-format", "--branch", branch)
            if branch_check.returncode != 0:
                return f"Error: Invalid Git branch '{branch}': {_git_error(branch_check)}"
            if _git("show-ref", "--verify", "--quiet", ref).returncode == 0:
                return f"Error: Branch '{branch}' already exists"
            if path.exists() or path in _registered_worktrees():
                return f"Error: Worktree path already exists or is registered: {path}"

            path.parent.mkdir(exist_ok=True)
            try:
                created = _git("worktree", "add", "-b", branch, str(path), "HEAD")
                if created.returncode != 0:
                    raise ValueError(_git_error(created))
            except (OSError, ValueError) as error:
                # Git 失败也可能留下分支或目录。保留它们，避免误删数据。
                return (
                    f"Git error: {error}. Task {task_id} remains unbound. "
                    f"Inspect {path}, branch '{branch}', and git worktree list before retrying."
                )

            task.worktree = name
            try:
                tasks.TASKS.save(task)
            except OSError as error:
                return (
                    f"Partial success: worktree '{name}' was created at {path}, "
                    f"but saving task {task_id} failed: {error}"
                )
    except (OSError, ValueError) as error:
        return f"Error: {error}"

    return f"Worktree '{name}' created at {path} for task {task_id}"


def remove_worktree(name: str, *, discard_changes: bool = False) -> str:
    """宿主清理函数。只有明确同意丢弃改动时，调用方才能传 True。"""
    try:
        with tasks.task_store_lock(), ExitStack() as guards:
            path = _registered_path(name)
            bound = [task for task in tasks.TASKS.list_all() if task.worktree == name]
            if not bound:
                return f"Error: Worktree '{name}' is not bound to a task"
            if any(task.status != "completed" for task in bound):
                return f"Error: Complete the task bound to '{name}' before removing it"
            if any(item.cwd == path for item in tasks.teammate_assignments.values()):
                return f"Error: Worktree '{name}' is still in use by a teammate turn"
            try:
                for task in bound:
                    guards.enter_context(tasks.assignment_lock(task.id))
            except BlockingIOError:
                return f"Error: Worktree '{name}' is still in use by another process"

            status = _git("status", "--porcelain", "--untracked-files=all", "--ignored", cwd=path)
            if status.returncode != 0:
                return f"Git error: {_git_error(status)}"
            if status.stdout and not discard_changes:
                return f"Error: Worktree '{name}' contains tracked, untracked or ignored changes"

            args = ["worktree", "remove"]
            if discard_changes:
                args.append("--force")
            removed = _git(*args, str(path))
            if removed.returncode != 0:
                return f"Git error: {_git_error(removed)}"

            try:
                for task in bound:
                    task.worktree = None
                    tasks.TASKS.save(task)
            except OSError as error:
                return (
                    f"Partial success: checkout removed and branch 'wt/{name}' retained, "
                    f"but saving the task binding failed: {error}"
                )
    except (OSError, ValueError) as error:
        return f"Error: {error}"

    return f"Worktree '{name}' removed; branch 'wt/{name}' retained"
