from pathlib import Path
from tempfile import TemporaryDirectory

import agent.tasks as tasks
from agent.config import WORKDIR


original_store = tasks.TASKS

try:
    with TemporaryDirectory(
        dir=WORKDIR,
        prefix="task-claim-check-",
    ) as directory:
        tasks.TASKS = tasks.TaskStore(Path(directory))

        schema = tasks.create_task("Design database")
        api = tasks.create_task("Implement API")

        tasks.update_task(
            api.id,
            addBlockedBy=[schema.id],
        )

        # 没有依赖的任务已经具备前置条件。
        assert tasks.can_start(schema.id) is True

        # API 的前置任务尚未完成。
        assert tasks.can_start(api.id) is False

        blocked = tasks.claim_task(api.id, owner="worker-a")
        assert blocked.startswith("Blocked by:")

        unchanged = tasks.load_task(api.id)
        assert unchanged.status == "pending"
        assert unchanged.owner is None

        # 认领数据库任务，并重新读取磁盘验证。
        claimed = tasks.claim_task(schema.id, owner="worker-a")
        assert claimed.startswith("Claimed ")

        saved = tasks.load_task(schema.id)
        assert saved.status == "in_progress"
        assert saved.owner == "worker-a"

        # 进行中的任务不能被第二次认领。
        rejected = tasks.claim_task(schema.id, owner="worker-b")
        assert "cannot claim" in rejected

        saved_again = tasks.load_task(schema.id)
        assert saved_again.owner == "worker-a"

        # 前置任务只是开始执行，仍然没有完成。
        assert tasks.can_start(api.id) is False

        print("依赖检查、认领保存和重复认领拒绝验证通过")

finally:
    tasks.TASKS = original_store