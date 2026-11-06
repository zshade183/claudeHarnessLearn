import os

from pathlib import Path  
from dotenv import load_dotenv



load_dotenv(override=True)
WORKDIR = Path.cwd()
SKILLS_DIR = WORKDIR / "skills"

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
)

# 子系统提示
SUB_SYSTEM = (
    f"You are a coding agent at {WORKDIR}. Environment: {ENVIRONMENT_PROMPT}. "
    "Complete the given task, then return a concise final answer."
)