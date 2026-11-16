# 第 3 章 · Permission 学习笔记

模型可以给工具提出申请，执行器要在动手之前决定它能不能做。比如 `read_file` 收到了一个路径，Python 要先算出它实际指向哪里，再决定能否读取。 [原项目第 3 章中文说明](https://github.com/shareAI-lab/learn-claude-code/blob/main/s03_permission/README.zh.md)

## 1. 哪些情况直接放行、拒绝或询问

| 工具请求 | 本项目的处理 | 对应代码 |
|---|---|---|
| 读 `playground/greeting.txt` | 解析后在当前工作目录里，允许 | `path_outside_workspace()` |
| 读工作目录外的文件 | 拒绝并返回 `Permission denied.` | `check_permission()` |
| 用 `bash` 跑 `echo hello` | 显示命令并等用户确认，输入 `y` 或 `yes` 才继续 | `ask_user()` |
| Shell 命令为空或命中拒绝列表 | 直接拒绝，跳过终端询问 | `check_permission()`、`check_deny_list()` |
| 调用 MCP 工具 | 已设为 `allow` 的工具可直接调用，其余需要确认 | `check_rules()` |

文件检查覆盖 `read_file`、`write_file`、`edit_file`。它用 `TOOL_CWD.get() or WORKDIR` 确定这次操作的目录，用 `.resolve()` 得到实际路径，再用 `.is_relative_to(cwd)` 判断它是否在该目录内。这样可以判断路径实际指向的地方，包括带 `..` 或符号链接的路径。 [agent/permissions.py · 路径检查](../agent/permissions.py#L34) · [agent/permissions.py · check_permission](../agent/permissions.py#L72)

## 2. 拒绝是怎样传回模型的

```text
execute_tool(block, handlers)
  -> trigger_hooks("PreToolUse", block)
     -> permission_hook(block)
        -> check_permission(block)
  -> 收到 Permission denied.，直接返回
  -> 主循环把拒绝信息写入这次调用的 tool_result
```

把判断放在执行入口，是因为模型只负责提出请求。执行器在读写文件或启动 Shell 之前就拿到了判断结果。返回拒绝原因，模型还可以向你解释为什么这次动作没发生。 [权限钩子](../agent/permissions.py#L105) · [执行入口](../agent/tools/executor.py#L11) · [主循环](../agent/loop.py#L221)

原版用硬拒绝、匹配规则、用户审批讲流程。本项目把目录外的文件读写直接拒绝，把通过基础检查的 Shell 调用交给用户逐次确认。拒绝列表用简单字符串匹配来演示判断的位置，实际隔离仍依赖运行环境的权限控制。需要询问时，只有主线程能读取终端输入，后台回合会得到拒绝结果。 [agent/permissions.py · ask_user](../agent/permissions.py#L56)

## 3. 用一个无害命令观察审批

在 `python main.py` 的交互提示下输入下面这句话。

```text
请只调用一次 bash 执行 echo hello。如果我拒绝，请告诉我工具返回了什么，直接结束。
```

看到 `Allow? [y/N]` 后直接按回车，观察这次工具返回的拒绝信息。重新提交同一个请求，再输入 `y`，观察模型这次是否收到 `hello`。两次走的是同一个工具，审批决定它能不能走进实际的执行函数。

下一章看 `PreToolUse` 怎样把权限检查接到工具执行前。 [第 4 章 · Hooks](s04-hooks.md)
