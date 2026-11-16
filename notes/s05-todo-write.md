# 第 5 章 · TodoWrite 学习笔记

让模型「先查文件、再读取、最后汇报」，它可能读完文件就答复，忘了汇报某一项。TodoWrite 给这次工作留一张待办清单，每做完一步就更新状态。模型下一次拿到工具结果时，能看见完成了什么、还有什么要做。 [原项目第 5 章中文说明](https://github.com/shareAI-lab/learn-claude-code/blob/main/s05_todo_write/README.zh.md)

## 1. 看一张清单怎么更新

假设这次要做三件事。找出 `playground` 下的文本文件，读取 `greeting.txt` 第一行，最后汇报。第一步做完、第二步刚开始时，模型可用下面的参数调用 `todo_write`。

```json
{
  "todos": [
    {"content": "找出 playground 下的文本文件", "status": "completed"},
    {"content": "读取 greeting.txt 第一行", "status": "in_progress"},
    {"content": "汇报文件清单和第一行", "status": "pending"}
  ]
}
```

终端会显示 `Current Tasks`，排好版的清单也会作为工具结果交回模型。

```text
[x] 找出 playground 下的文本文件
[>] 读取 greeting.txt 第一行
[ ] 汇报文件清单和第一行

(1/3 completed)
```

下一次仍要发送整张清单。这是因为 `update()` 会用新列表替换旧列表。假如只发第二条，第一条和第三条就会从清单里消失。 [agent/planning.py · TodoManager.update](../agent/planning.py#L14)

## 2. 程序如何保存和显示清单

| 找到这段代码 | 看它做什么 |
|---|---|
| [agent/tools/registry.py · TODO_TOOL](../agent/tools/registry.py#L17) | 告诉模型工具名和参数格式，包括每条待办的 `content` 与 `status` |
| [agent/planning.py · run_todo_write](../agent/planning.py#L104) | 调用 `TODO.update(todos)`，成功时打印清单，校验失败时返回 `Error: ...` |
| [agent/planning.py · TodoManager](../agent/planning.py#L9) | 先校验整张列表，再赋给 `self.items`。因此中途发现第二条有问题时，旧清单仍可用 |
| [agent/loop.py · rounds_since_todo](../agent/loop.py#L233) | 记录连续几批工具调用里没用过 `todo_write`，必要时给模型加一句提醒 |

清单最多 20 条。状态有 `pending`、`in_progress`、`completed`，同时最多允许一项 `in_progress`。这让阅读清单的人能一眼看到模型当前在做哪一步。Python 进程用模块里的 `TODO` 对象保存清单，重启后会重新创建这个对象。需要落盘、依赖关系和领取任务时，会用到第 10 章的任务系统。 [第 10 章 · Task System](s10-task-system.md)

## 3. 「连续三轮」到底在数什么

它数的是**主模型返回了工具调用的批次**。同一条模型回复里调用了三个工具，仍然只算一批。某批出现 `todo_write` 就把计数归零。连续三批都没出现时，程序把 `<reminder>Update your todos.</reminder>` 作为普通文本块附在第三批工具结果后面，再把计数归零。提醒只会进入模型看到的对话，具体要不要改、改哪几项，仍由模型通过下一次工具调用表达。 [agent/loop.py · 计数与提醒](../agent/loop.py#L242)

## 4. 在终端体验

```text
请先用 todo_write 建一张三项清单。列出 playground 下的 txt 文件，读 greeting.txt 的第一行，汇报文件清单和这一行。每完成一步就更新整张清单。
```

看 `Current Tasks` 有没有从 `[ ]` 变成 `[>]` 和 `[x]`。再对照每次出现的 `todo_write`，确认清单是工具收到更新请求后才改变。

下一章把一个能单独完成的小任务交给子代理。 [第 6 章 · Subagent](s06-subagent.md)
