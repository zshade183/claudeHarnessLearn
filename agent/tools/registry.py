from agent.tools.shell import run_bash

# 注册工具 
TOOLS = [
    {  
        "name": "bash",
        "description": "Run a shell command.",
        "input_schema": {
            "type": "object",  # 输入参数为对象
            "properties": {  # 定义输入参数
                "command": {
                    "type": "string",
                },
            },
            "required": ["command"],  # 必须提供 command 参数
        },
    },
]

# 注册工具处理函数
TOOL_HANDLERS = {
    "bash": run_bash,
}