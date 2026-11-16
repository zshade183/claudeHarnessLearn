# 第 2 章 · Tool Use 学习笔记

模型说「我要用 `read_file`」，程序要解决两件事。先告诉模型这个工具接受什么参数，再在本机找到处理它的函数。工具清单负责第一件事，执行函数表负责第二件事。 [原项目第 2 章中文说明](https://github.com/shareAI-lab/learn-claude-code/blob/main/s02_tool_use/README.zh.md)

## 1. 一个工具为什么要有两份定义

打开 [agent/tools/base.py · BASE_TOOLS 和 BASE_HANDLERS](../agent/tools/base.py)。在文件上部搜索 `"name": "read_file"`，再看底部的 `"read_file": run_read`。

| 位置 | 谁用它 | `read_file` 的例子 |
|---|---|---|
| `BASE_TOOLS` | 模型用工具说明选择动作和参数 | 必填的 `path` 是字符串，可选的 `limit` 是整数 |
| `BASE_HANDLERS` | Python 执行器用名字找到函数 | `"read_file": run_read` |

Python 函数在本机运行。模型能看到函数对应的参数说明，也就是 `input_schema`，然后按说明给出调用。两边用同一个名字对上，所以同一套循环能处理多种工具。

## 2. 从工具名字找到读文件的代码

1. `assemble_tool_pool()` 返回工具清单和执行函数表。项目已经有后续章节的工具，每次请求模型前都会组合一遍当前可用工具。 [agent/tools/registry.py · assemble_tool_pool](../agent/tools/registry.py#L152)
2. 模型返回 `tool_use`，其中 `name` 是 `read_file`，`input` 是 `{"path": "playground/greeting.txt", "limit": 1}`。主循环把这个块交给执行器。 [agent/loop.py · 工具批次](../agent/loop.py#L221)
3. `execute_tool()` 通过前置检查后，运行 `handlers.get(block.name)`。取到 `run_read` 后，`handler(**block.input)` 把字典展开成具名参数，等价于 `run_read(path="playground/greeting.txt", limit=1)`。 [agent/tools/executor.py · execute_tool](../agent/tools/executor.py#L11)
4. `run_read()` 从当前工具工作目录解析路径，按 UTF-8 读文本，按行应用 `limit`，返回结果字符串。主循环把结果送回模型。 [agent/tools/files.py · run_read](../agent/tools/files.py#L6)

执行器查不到普通工具名时会返回 `Unknown: <工具名>`。检查工具清单和函数表中的名字是否一致，就能定位常见的注册错误。

## 3. 五个基础工具分别干什么

| 工具 | 本项目的处理函数 | 用法 |
|---|---|---|
| `read_file` | `run_read` | 读指定文本文件，可限制返回行数 |
| `write_file` | `run_write` | 写入完整内容，必要时创建父目录，同名文件会被覆盖 |
| `edit_file` | `run_edit` | 查找完全相同的 `old_text`，替换第一个匹配，找不到时返回错误 |
| `glob` | `run_glob` | 按路径模式找文件，`**` 可递归匹配 |
| `bash` | `run_bash` | 把命令交给本机 Shell，名称沿用原版，本项目在 Windows 也能走本机 Shell |

专用工具的参数把意图说清楚了。读文件只需要路径和行数，所以 Python 能直接完成，也能在执行前单独检查这个路径。 [文件操作](../agent/tools/files.py) · [Shell 执行](../agent/tools/shell.py)

## 4. 在终端观察一次分发

```text
请用 glob 查找 playground 下的 txt 文件，再用 read_file 读取 playground/greeting.txt 的第一行。
```

观察终端出现的两个工具名。如果模型在一次响应里给出多个工具调用，当前循环按返回顺序逐个执行，再把同一批结果交回模型。如果模型要先看查找结果再选文件，它也可以先发 `glob`，下一次模型调用再发 `read_file`。

执行器在查处理函数前还有一道检查。下一章从这个位置读起。 [第 3 章 · Permission](s03-permission.md)
