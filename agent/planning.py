"""
planning.py 是一个任务管理模块，用于处理用户输入的任务列表。
"""

import ast
import json


class TodoManager:
    def __init__(self):
        self.items: list[dict] = []
    
    # 更新任务列表
    def update(self, todos: list | str) -> str:
        """
        更新任务列表。
        Args:
            todos: 任务列表，可以是字符串或列表。
        Returns:
            更新后的任务列表字符串。
        """
        if isinstance(todos, str):  # 如果是字符串，尝试解析为 JSON 数组或 Python 列表
            try:
                todos = json.loads(todos)
            except json.JSONDecodeError:
                try:
                    todos = ast.literal_eval(todos)
                except (SyntaxError, ValueError) as e:
                    raise ValueError(
                        "todos must be a list or JSON array string"
                    ) from e

        if not isinstance(todos, list):  # 如果不是列表，抛出异常
            raise ValueError("todos must be a list")

        if len(todos) > 20:  # 最多20个任务
            raise ValueError("Max 20 todos allowed")

        validated = []  # 验证后的任务列表
        in_progress_count = 0  # 正在进行中的任务数量计数

        for index, todo in enumerate(todos):
            if not isinstance(todo, dict):
                raise ValueError(f"todos[{index}] must be an object")

            content = str(todo.get("content", "")).strip()  # 任务内容，去空格
            status = str(todo.get("status", "pending")).lower()  # 任务状态，转换为小写

            if not content:
                raise ValueError(f"todos[{index}] requires content")

            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(
                    f"todos[{index}] has invalid status '{status}'"
                )

            if status == "in_progress":
                in_progress_count += 1

            validated.append({
                "content": content,
                "status": status,
            })

        if in_progress_count > 1:
            raise ValueError(
                "Only one todo can be in_progress at a time"
            )

        self.items = validated  # 更新任务列表
        return self.render()  # 返回渲染后的任务列表字符串


    def render(self) -> str:
        """
        渲染任务列表。
        Returns:
            �染后的任务列表字符串。
        """
        if not self.items:
            return "No todos."

        lines = []  # 渲染后的任务列表行列表

        for todo in self.items:  # 遍历任务列表
            marker = {
                "pending": "[ ]",
                "in_progress": "[>]",
                "completed": "[x]",
            }[todo["status"]]

            lines.append(f"{marker} {todo['content']}")

        done = sum(  # 统计已完成任务数量
            todo["status"] == "completed"
            for todo in self.items
        )

        lines.append(f"\n({done}/{len(self.items)} completed)")
        return "\n".join(lines)

TODO = TodoManager()  # 任务管理实例

def run_todo_write(todos: list | str) -> str:
    """
    运行任务写入工具。
    Args:
        todos: 任务列表，可以是字符串或列表。
    Returns:
        更新后的任务列表字符串。
    """
    try:
        output = TODO.update(todos)
    except ValueError as e:
        return f"Error: {e}"

    print(f"\n\033[33m## Current Tasks\033[0m\n{output}")
    return output