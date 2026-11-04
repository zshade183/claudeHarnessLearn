import os
import subprocess



def run_bash(command: str) -> str:
    """Run a bash command and return its output.

    Args:
        command: The bash command to run.
    Returns:
        The output of the command.
    Raises:
        subprocess.TimeoutExpired: If the command times out.
        FileNotFoundError: If the command is not found.
        OSError: If the command fails for any other reason.
       """
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"

    try:
        r = subprocess.run(
            command,
            shell=True,  # 启用 shell 解析
            cwd=os.getcwd(),  # 设置工作目录为当前目录
            capture_output=True,  # 捕获标准输出和错误输出
            text=True,  # 返回文本而不是字节
            errors="replace",  # 替换错误字符
            timeout=120,  # 设置超时时间为 120 秒
        )
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"

    except subprocess.TimeoutExpired:  # 命令超时
        return "Error: Timeout (120s)"

    except (FileNotFoundError, OSError) as e:  # 命令失败
        return f"Error: {e}"