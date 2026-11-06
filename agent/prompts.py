from agent.config import SYSTEM as BASE_SYSTEM
from agent.skills import SKILL_LOADER


def build_system_prompt() -> str:
    return (
        f"{BASE_SYSTEM}\n\n"
        f"Skills available:\n{SKILL_LOADER.catalog()}\n\n"
        "Use load_skill to read the full instructions when a skill applies."
    )


SYSTEM = build_system_prompt()