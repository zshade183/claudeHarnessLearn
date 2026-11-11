"""第 14 章的 MCP 工具发现和调用。
"""

from collections.abc import Callable


class MCPClient:
    """保存一个模拟服务提供的工具定义和执行函数。"""

    def __init__(self, name: str):
        self.name = name
        self.tools: list[dict] = []  # 工具定义列表
        self._handlers: dict[str, Callable[..., object]] = {}  # 工具执行函数映射
    
    def register(
        self,
        tool_defs: list[dict],
        handlers: dict[str, Callable[..., object]],
    ) -> None:
        """注册工具定义和执行函数。
        
        每个工具定义必须有一个非空的字符串名称。
        每个工具执行函数必须有一个与工具名称对应的键。
        每个工具名称必须是唯一的。
        
        :param tool_defs: 工具定义列表，每个元素是一个字典，包含 "name" 键。
        :param handlers: 工具执行函数映射，键是工具名称，值是可调用对象。
        :raises ValueError: 如果工具名称或执行函数缺失或重复。
        """
        names = [tool.get("name") for tool in tool_defs]

        # 先检查清单，确保每个工具都有唯一的名字和对应的函数。
        if any(not isinstance(name, str) or not name for name in names):
            raise ValueError("Every MCP tool needs a non-empty name")
        if len(set(names)) != len(names):
            raise ValueError(f"Duplicate MCP tool name on server {self.name!r}")

        missing = [name for name in names if name not in handlers]
        if missing:
            raise ValueError(f"Missing MCP handlers: {', '.join(missing)}")

        self.tools = list(tool_defs)
        self._handlers = dict(handlers)

    def call_tool(self, tool_name: str, args: dict) -> str:
        """调用指定工具。
        :param tool_name: 工具名称。
        :param args: 工具参数，键是参数名，值是参数值。
        :return: 工具执行结果，作为字符串。
        """

        handler = self._handlers.get(tool_name)
        if handler is None:
            return f"MCP error: unknown tool '{tool_name}'"

        try:
            # 例如 {"query": "agent hooks"} 会变成 search(query="agent hooks")。
            return str(handler(**args))
        except Exception as error:
            # 把调用失败变成工具结果，模型可以据此修正参数。
            return f"MCP error: {type(error).__name__}: {error}"