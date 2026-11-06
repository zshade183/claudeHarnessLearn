"""
基础工具 
包含文件操作、shell命令执行、glob匹配等
"""

from agent.tools.files import run_read, run_write, run_edit, run_glob
from agent.tools.shell import run_bash

BASE_TOOLS = [
    # 这里放剪切过来的五个完整工具字典
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
    {
        "name": "read_file",
        "description": "Read file contents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["path"],
        },
    },
        {
        "name": "write_file",
        "description": "Write content to a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace exact text in a file once.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
        {
        "name": "glob",
        "description": (
            "Find files matching a glob pattern; ** matches recursively."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
            },
            "required": ["pattern"],
        },
    },
]

BASE_HANDLERS = {
    "bash": run_bash,
    "read_file": run_read,
    "write_file": run_write,
    "edit_file": run_edit,
    "glob": run_glob,
}