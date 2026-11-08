import os

from pathlib import Path  
from dotenv import load_dotenv



load_dotenv(override=True)
WORKDIR = Path.cwd()
SKILLS_DIR = WORKDIR / "skills"  #技能目录
TRANSCRIPT_DIR = WORKDIR / ".transcripts"  #历史归档目录
TOOL_RESULTS_DIR = WORKDIR / ".task_outputs" / "tool-results"  #大工具调用结果目录
MEMORY_DIR = WORKDIR / ".memory"  #记忆目录
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"  #记忆索引文件
TASKS_DIR = WORKDIR / ".tasks"  #任务目录


MODEL = os.environ["MODEL_ID"]
BASE_URL = os.getenv("ANTHROPIC_BASE_URL")

ENVIRONMENT_PROMPT = (
    "Windows: the bash tool runs through cmd.exe; use cmd.exe syntax, not Unix "
    "Bash or PowerShell syntax, and prefer dedicated file tools for file operations"
    if os.name == "nt"
    else "Unix-like: the bash tool runs the system shell"
)

SYSTEM = (
    f"You are a coding agent at {WORKDIR}. Environment: {ENVIRONMENT_PROMPT}. "
    "Use tools to solve tasks. Do not ask for approval yourself. "
    "For destructive operations, still request the tool call; "
    "the harness will approve or deny before execution. "
    "Before starting any multi-step task, use todo_write to plan your steps. "
    "Update status as you go."
    "Use task for focused exploration or a self-contained subtask."
    "For work that needs persistent progress or dependencies, use the task tools. "
    "Create the tasks first, then use their returned IDs in update_task. "
    "Claim a task before working on it and complete it after the work is done."
)

# 子系统提示
SUB_SYSTEM = (
    f"You are a coding agent at {WORKDIR}. Environment: {ENVIRONMENT_PROMPT}. "
    "Complete the given task, then return a concise final answer."
)