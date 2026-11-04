try:
    # 导入 readline 模块
    # 用于在命令行中使用箭头键导航历史记录
    # 仅在 Unix-like 系统上有效
    # 在 Windows 上，使用 cmd.exe 作为 shell，不支持箭头键导航历史记录
    import readline

    readline.parse_and_bind("set bind-tty-special-chars off")
    readline.parse_and_bind("set input-meta on")
    readline.parse_and_bind("set output-meta on")
    readline.parse_and_bind("set convert-meta off")
except ImportError:
    pass

from agent.loop import agent_loop


if __name__ == "__main__":
    print("Agent Loop")
    print("Enter a question, press Enter to send. Type q to quit.\n")

    history = []  # 存储用户输入和模型响应

    while True:
        try:
            # 从 readline 模块获取用户输入
            # 用于在 Unix-like 系统上使用箭头键导航历史记录
            query = input("\001\033[36m\002agent >> \001\033[0m\002")
        except (EOFError, KeyboardInterrupt):
            break
        # 处理空输入
        # 忽略空输入，继续下一次循环
        if not query.strip():  
            continue
        # 处理退出命令
        # 当用户输入 q 或 exit 时，退出循环
        if query.strip().lower() in ("q", "exit"):
            break

        history.append({
            "role": "user",
            "content": query,
        })

        agent_loop(history)

        response_content = history[-1]["content"]

        if isinstance(response_content, list):
            for block in response_content:
                # 处理文本块
                # 打印文本块的内容
                if getattr(block, "type", None) == "text":
                    print(block.text)

        print()