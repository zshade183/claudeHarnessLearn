"""后台任务管理器的手工验收脚本（不需要 API key）。"""

import time
from types import SimpleNamespace

from agent.background import BACKGROUND, collect_background_results


block = SimpleNamespace(
    id="toolu_fake",
    name="bash",
    input={"command": "ping -n 4 127.0.0.1 > nul"},
)

task_id = BACKGROUND.start(block)
print("started:", task_id)

print("立刻收集（应为空）:", collect_background_results())

time.sleep(5)

print("5 秒后再收集：")
for notification in collect_background_results():
    print(notification)

print("再收集一次（应为空）:", collect_background_results())