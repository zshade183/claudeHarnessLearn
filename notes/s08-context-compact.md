# 第 8 章 · Context Compact 学习笔记

每次请求模型，主循环都要带上这段对话。聊天会越聊越长，读文件也可能一次带回几万字。本章在发送前检查消息大小，把能保留在磁盘上的大段文字换成路径和预览。空间仍然不够时，再请模型整理较早的工作。 [原项目第 8 章中文说明](https://github.com/shareAI-lab/learn-claude-code/blob/main/s08_context_compact/README.zh.md)

## 1. 先分清对话里的内容和磁盘里的存档

假设现在要做的是「报告 `greeting.txt` 的第一行」。你刚才还让模型读过一份很长的日志。如果日志全文仍在 `messages`，下一次请求模型时，还会带上那一大段文字。把全文保存到文件，在原位置留下一段预览和路径，就能把多数空间留给当前问题。如果后面需要查证，模型也可以根据路径再调用读文件工具。

| 放在哪里 | 放进去的东西 | 什么时候用 |
|---|---|---|
| `messages` 列表 | 当前请求、近期对话、工具结果或它的预览 | 下一次请求模型时 |
| `.task_outputs/tool-results/` | 单次工具结果的原文 | 想查看路径标记指向的完整输出时 |
| `.transcripts/` | 当次压缩前消息列表的 JSONL 快照，每行一条消息 | 想回看被归档的对话时。之前缩短过的工具结果在快照里仍可能是路径标记 |

目录来自 [agent/config.py](../agent/config.py#L13)。工具输出由 [save_output()](../agent/context.py#L139) 保存，消息快照由 [write_transcript()](../agent/context.py#L165) 保存。

## 2. 按实际执行顺序读 `prepare()`

主循环先执行 `messages[:] = COMPACTOR.prepare(messages, active_request)`，再发模型请求。切片赋值把压缩后的条目装回原列表，因此终端里持有同一个 `history` 的代码也能看到更新。 [agent/loop.py · 调用位置](../agent/loop.py#L81) · [agent/context.py · prepare](../agent/context.py#L662)

| 顺序 | 怎么处理 | 为什么这一步放在这里 |
|---|---|---|
| 1. [tool_result_budget](../agent/context.py#L289) | 检查末条消息里这一批工具结果。合计超过 20 万字符时，优先把最大的结果落盘，单条需超过 3 万字符 | 模型可能还没看到这批结果，给它留预览，也避免大结果把整段请求撑满 |
| 2. [snip_compact](../agent/context.py#L382) | 消息达到 50 条时，归档中间段，目标保留约 30 条，包含头尾和一条归档标记 | 长对话里有一段较久远的来回可以留在磁盘上。约 30 条的目标也给后续消息留了空间 |
| 3. [micro_compact](../agent/context.py#L449) | 消息估算超过 5 万字符时，先缩短模型已经看过的旧工具结果，保留最近 3 条已读结果和未读结果 | 优先腾出较早的工具输出占用的空间 |
| 4. [fit_tool_results](../agent/context.py#L508) | 仍超标时，再尝试把任何较大的工具结果换成路径和 1000 字符的预览，包括近期结果 | 有时最新的一大段结果就是主要的体积来源，此时连它也需要缩短 |
| 5. [compact_history](../agent/context.py#L636) | 仍超标就归档、额外请求一次模型生成事实摘要，再用一条包含当前请求、摘要和归档路径的消息替换列表 | 当剩下的内容仍很长时，摘要可以浓缩已经完成的工作和待办 |

这里的 5 万是把消息序列化后得到的字符数，用来粗估体积，模型服务会按自己的 token 计算窗口。`micro_compact()` 和 `fit_tool_results()` 尝试压到约 4 万字符，为下一批消息留空间。 [agent/context.py · estimate_chars](../agent/context.py#L39)

## 3. 为什么还要保护当前请求和工具配对

压缩时要留住两样东西。第一样是**现在究竟要完成什么**。原始请求可能夹在很长一串工具往来中间，`snip_compact()` 裁剪后如果找不到它，`prepare()` 会把 `active_request` 加回消息。第二样是**这条工具结果回答哪一次调用**。裁剪碰到相邻的 `tool_use` 和 `tool_result` 时会调整边界，避免只剩半对。单独缩短结果时，程序只替换结果里的 `content`，关联的 ID 还在。 [裁剪边界](../agent/context.py#L382) · [补回当前请求](../agent/context.py#L662)

还有两个入口会用到同一套压缩器。模型主动请求 `compact` 时，主循环先把同一批工具结果写入对话，然后执行整段摘要。模型接口报请求上下文太长时，主循环执行 `reactive_compact()`，把早期历史摘要化并尽量保留最近 5 条消息，然后重试。连续报超长时只补救一次，请求成功后恢复重试额度。 [手动压缩分支](../agent/loop.py#L221) · [超长重试分支](../agent/loop.py#L96) · [reactive_compact](../agent/context.py#L725)

## 4. 在 PowerShell 里亲眼看一次「长结果变成路径」

在项目根目录运行这段小实验。它把本次批次的测试上限临时传成 5000 字符，构造一条 4 万字符的工具结果。只运行第一步，不请求模型，原文写入临时目录。

```powershell
@'
from pathlib import Path
from tempfile import TemporaryDirectory

from agent.context import ContextCompactor

full_output = "0123456789" * 4_000
result = {
    "type": "tool_result",
    "tool_use_id": "toolu_demo",
    "content": full_output,
}
messages = [{"role": "user", "content": [result]}]

with TemporaryDirectory() as folder:
    root = Path(folder)
    compactor = ContextCompactor(None, "demo", root / "history", root / "outputs")
    print("Before:", len(result["content"]))
    compactor.tool_result_budget(messages, max_chars=5_000)
    print("After:", len(result["content"]))
    print(result["content"].splitlines()[1])
    saved = root / "outputs" / "toolu_demo.txt"
    print("Full output preserved:", saved.read_text(encoding="utf-8") == full_output)
'@ | python -B -
```

预期先看到 `Before: 40000`，再看到 `After` 约两千字符和一条 `Full output` 路径，最后是 `Full output preserved: True`。这一步改变的是发给模型的结果文本，原文在它指向的文件里。实验结束时 Python 会清理临时目录。

压缩在管理当前对话的长度，下一章再看哪些经验值得记下来供以后的对话使用。 [第 9 章 · Memory](s09-memory.md)
