import os

from dotenv import load_dotenv


load_dotenv(override=True)

MODEL = os.environ["MODEL_ID"]
BASE_URL = os.getenv("ANTHROPIC_BASE_URL")

ENVIRONMENT_PROMPT = (
    "Windows: the bash tool runs through cmd.exe; use cmd.exe syntax, not Unix "
    "Bash or PowerShell syntax"
    if os.name == "nt"
    else "Unix-like: the bash tool runs the system shell"
)

SYSTEM = (
    f"You are a coding agent at {os.getcwd()}. Environment: {ENVIRONMENT_PROMPT}. "
    "Use bash to solve tasks. Act, don't explain."
)