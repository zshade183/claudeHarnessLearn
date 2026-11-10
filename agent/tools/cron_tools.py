"""把定时计划的管理操作暴露给主代理。"""

from agent.scheduler import cancel_job, cron_lock, schedule_job, scheduled_jobs


def run_schedule_cron(
    cron: str,
    prompt: str,
    recurring: bool = True,
    durable: bool = True,
) -> str:
    job = schedule_job(cron, prompt, recurring, durable)
    if isinstance(job, str):
        return f"Error: {job}"
    return f"Scheduled {job.id}: {job.cron} -> {job.prompt}"


def run_list_crons() -> str:
    with cron_lock:
        if not scheduled_jobs:
            return "No cron jobs."

        lines = []
        for job in scheduled_jobs.values():
            frequency = "recurring" if job.recurring else "one-shot"
            storage = "durable" if job.durable else "session"
            lines.append(
                f"{job.id}: {job.cron} -> {job.prompt[:60]} "
                f"[{frequency}, {storage}]"
            )
        return "\n".join(lines)


CRON_TOOLS = [
    {
        "name": "schedule_cron",
        "description": "Schedule an agent prompt using a five-field cron rule in local time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cron": {"type": "string"},
                "prompt": {"type": "string"},
                "recurring": {"type": "boolean"},
                "durable": {"type": "boolean"},
            },
            "required": ["cron", "prompt"],
        },
    },
    {
        "name": "list_crons",
        "description": "Show the registered cron schedules and their IDs.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "cancel_cron",
        "description": "Remove a schedule by its generated job ID.",
        "input_schema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
]

CRON_HANDLERS = {
    "schedule_cron": run_schedule_cron,
    "list_crons": run_list_crons,
    "cancel_cron": cancel_job,
}
