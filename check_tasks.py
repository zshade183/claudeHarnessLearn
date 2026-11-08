"""任务系统的手工验收脚本（不需要 API key）。"""

from agent.tasks import create_task, get_task, list_tasks, update_task


schema = create_task("setup database schema")
endpoints = create_task("create API endpoints")
tests = create_task("write tests")

update_task(endpoints.id, addBlockedBy=[schema.id])
update_task(tests.id, addBlockedBy=[endpoints.id])

print("\n=== 全部任务 ===")
for task in list_tasks():
    print(task.id, "|", task.subject, "| blockedBy:", task.blockedBy)

print("\n=== get_task(endpoints) ===")
print(get_task(endpoints.id))

print("\n=== 拒绝成环：A 等 C，而 C→B→A ===")
try:
    update_task(schema.id, addBlockedBy=[tests.id])
except ValueError as error:
    print("OK:", error)

print("\n=== 拒绝自依赖 ===")
try:
    update_task(tests.id, addBlockedBy=[tests.id])
except ValueError as error:
    print("OK:", error)

print("\n=== 拒绝不存在的依赖 ===")
try:
    update_task(tests.id, addBlockedBy=["task_deadbeef"])
except ValueError as error:
    print("OK:", error)