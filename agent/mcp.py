"""第 14 章的 MCP 工具发现和调用。
"""

from collections.abc import Callable


class MCPClient:
    """保存一个模拟服务提供的工具定义和执行函数。
    模拟一个 MCP 服务，封装工具定义和执行函数。
    
    """

    def __init__(self, name: str):
        self.name = name  # 服务名称
        self._handlers: dict[str, Callable[..., object]] = {}  # 工具执行函数映射
        self.tools: list[dict] = []  # 工具定义列表
    
    
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

def _mock_server_docs() -> MCPClient:
    """创建模拟文档服务，登记搜索和查询版本工具。
    :return: 模拟文档服务的 MCPClient 实例。
    """
    docs = MCPClient("docs")

    def search(query: str) -> str:
        # 在模拟结果里带上关键词，方便确认参数传到了执行函数。
        return f"[docs] Found 3 results for '{query}'"

    def get_version() -> str:
        return "[docs] API v2.1.0"

    docs.register(
        tool_defs=[
            {
                "name": "search",
                "description": "Search the documentation.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "get_version",
                "description": "Get the documentation API version.",
                "inputSchema": {"type": "object", "properties": {}},
                "annotations": {"readOnlyHint": True},
            },
        ],
        handlers={"search": search, "get_version": get_version},
    )
    return docs


def _mock_server_deploy() -> MCPClient:
    """用固定文本模拟部署平台的两个工具。"""
    deploy = MCPClient("deploy")

    def status(service: str) -> str:
        return f"[deploy] {service}: running (v1.4.2)"

    def trigger(service: str) -> str:
        return f"[deploy] Triggered: {service}"

    # 两个工具都通过 service 指定要操作的服务。
    service_schema = {
        "type": "object",
        "properties": {"service": {"type": "string"}},
        "required": ["service"],
    }
    deploy.register(
        tool_defs=[
            {
                "name": "status",
                "description": "Check deployment status.",
                "inputSchema": service_schema,
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "trigger",
                "description": "Trigger a deployment.",
                "inputSchema": service_schema,
                "annotations": {"destructiveHint": True},
            },
        ],
        handlers={"status": status, "trigger": trigger},
    )
    return deploy


# 可以连接的服务：服务名 → 创建它的函数。
MOCK_SERVERS = {
    "docs": _mock_server_docs,
    "deploy": _mock_server_deploy,
}

# 已经连接的服务：服务名 → 创建好的对象。
# 用于存储已经连接的服务，避免重复创建。
# 服务名 → MCPClient 实例。
mcp_clients: dict[str, MCPClient] = {}


# 宿主按服务名和原工具名授权。服务提供的 annotations 只用于描述工具。
MCP_HOST_POLICY = {
    ("docs", "search"): "allow",
    ("docs", "get_version"): "allow",
    ("deploy", "status"): "allow",
    ("deploy", "trigger"): "confirm",
}

# 组装工具清单时生成，供权限钩子按模型看到的工具名查询。
mcp_tool_policies: dict[str, str] = {}


def connect_mcp(name: str) -> str:
    if name in mcp_clients:
        return f"MCP server '{name}' already connected"

    factory = MOCK_SERVERS.get(name)
    if factory is None:
        return f"Unknown server '{name}'. Available: {', '.join(MOCK_SERVERS)}"

    server = factory()  # 这时才创建对象并登记工具
    mcp_clients[name] = server  # 后面的工具调用继续用这个对象

    names = ", ".join(tool["name"] for tool in server.tools)
    return f"Connected to MCP server '{name}'. Discovered {len(server.tools)} tools: {names}"
