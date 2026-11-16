# 第 7 章 · Skill Loading 学习笔记

Skill 可以先理解成一张操作说明。比如「阅读项目代码」这个技能写着先找文件，再顺着函数调用阅读，最后附上代码位置。程序先给模型一份简短的技能目录，模型看到合适的条目时，再用 `load_skill` 取整份说明。 [原项目第 7 章中文说明](https://github.com/shareAI-lab/learn-claude-code/blob/main/s07_skill_loading/README.zh.md)

## 1. 打开工作区里已有的技能

打开 [skills/code-exploration/SKILL.md](../skills/code-exploration/SKILL.md)，先找文件开头的两条 `---`。它们中间是 YAML 文件头，用来写结构化的名称和简介。第二条 `---` 后的 Markdown 才是详细操作步骤。

```yaml
---
name: code-exploration
description: 阅读项目代码，追踪调用关系，并根据源码解释模块职责。
---
```

模型从简介判断「这是代码阅读任务，可以用这个技能」。读到完整文档后，它才知道要用 `glob` 找文件、用 `read_file` 查调用方，并在答案里指出文件和函数。技能里提到的工具本身来自工具注册表，技能提供的是怎么用已有工具完成这类工作。 [工具注册表](../agent/tools/registry.py)

## 2. 一份文件怎样在两个时刻进入模型

| 时刻 | 代码做的事 | 模型收到什么 |
|---|---|---|
| 加载 Python 模块 | `SkillLoader.scan()` 扫描 `skills/*/SKILL.md`，读取文件并把内容存在 `self.skills` | 此时只在本机整理文件 |
| 组装系统提示 | `build_system_prompt()` 调用 `SKILL_LOADER.catalog()` | `Skills available` 下的技能名和一句简介 |
| 模型发出工具申请 | `load_skill` 映射到 `SKILL_LOADER.load`，按名称查找 | 主循环把对应的完整 `SKILL.md` 放进 `tool_result` |
| 主循环再问模型 | 工具结果已进入这段对话 | 模型可以照着刚加载的步骤继续读代码 |

按需加载发生在**把正文发给模型**这一步。本项目的扫描在启动时就读完了文件，只先向模型发送目录。这样即使以后有许多技能，每次请求也只常驻简短简介。想查看解析先看 `parse_frontmatter()`，再顺着 `scan()` → `catalog()` → `load()` 读。 [agent/skills.py · SkillLoader](../agent/skills.py#L11) · [agent/prompts.py · build_system_prompt](../agent/prompts.py#L7) · [agent/tools/registry.py · 工具映射](../agent/tools/registry.py#L131)

## 3. 一次查找失败会发生什么

`load()` 按名称查字典。比如输入了 `code_exploration`，而技能登记名是带短横线的 `code-exploration`，它会返回 `Unknown skill` 和可用的名字列表，模型可以据此重新调用。扫描时，如果 YAML 里未填写 `name`，加载器会用所在目录的名字。修改说明文件后，重新启动程序会再次扫描。 [agent/skills.py · scan 与 load](../agent/skills.py#L57)

## 4. 在终端体验

```text
请先加载 code-exploration 技能，然后按它的步骤解释 read_file 如何从模型的一次工具调用走到实际读文件。列出实际读过的文件、涉及的函数和调用顺序。
```

先看有没有 `load_skill` 工具日志，再看后面有哪些 `glob` 或 `read_file`。前者取的是怎么查代码的说明，后者才读取解释时需要引用的源码。

下一章处理正文和工具结果越积越多的问题。 [第 8 章 · Context Compact](s08-context-compact.md)
