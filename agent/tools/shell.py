import atexit
import os
import signal
import subprocess
import threading
import time

from agent.config import TOOL_CWD, WORKDIR


_shell_processes: set[subprocess.Popen] = set()
_shell_process_lock = threading.RLock()


def _stop_process_group(process: subprocess.Popen) -> None:
    """
    停止指定进程组中的所有进程。
    :param process: 要停止的进程对象。
    """
    if os.name == "nt":
        for stop in (process.terminate, process.kill):
            if process.poll() is not None:
                return

            try:
                stop()
                process.wait(timeout=0.05)
            except subprocess.TimeoutExpired:
                continue
            except OSError:
                return

        return

    for sig in (
        signal.SIGTERM,
        getattr(signal, "SIGKILL", signal.SIGTERM),
    ):
        try:
            os.killpg(process.pid, sig)
        except OSError:
            return

        time.sleep(0.05)


def _stop_all_shell_processes() -> None:
    """
    停止所有 shell 进程组中的所有进程。
    :return: None
    """
    with _shell_process_lock:
        processes = list(_shell_processes)

    for process in processes:
        # 停止进程组中的所有进程
        _stop_process_group(process)


def _handle_termination_signal(signum, _frame):
    """
    处理终止信号。
    :param signum: 信号编号。
    :param _frame: 当前帧对象。
    :return: None
    """
    _stop_all_shell_processes()
    raise SystemExit(128 + signum)


atexit.register(_stop_all_shell_processes)
signal.signal(signal.SIGTERM, _handle_termination_signal)

def _run_bash_process(command: str) -> tuple[str, int | None]:
    """
    运行 bash 命令并返回输出和退出码
    负责"执行"，返回`(output, exit_code)` 原始二元组；
    :param command: 要运行的 bash 命令。
    :return: 包含输出和退出码的元组。
    """
    process = None

    try:
        options = {} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=TOOL_CWD.get() or WORKDIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            **options,
        )

        with _shell_process_lock:
            _shell_processes.add(process)

        stdout, stderr = process.communicate(timeout=120)
        output = (stdout + stderr).strip()
        return output[:50000] or "(no output)", process.returncode

    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)", None

    except OSError as error:
        return f"Error: {type(error).__name__}: {error}", None

    finally:
        if process is not None:
            _stop_process_group(process)

            try:
                process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass

            with _shell_process_lock:
                _shell_processes.discard(process)


def _format_bash_result(output: str, exit_code: int | None) -> str:
    """
    格式化 bash 命令的输出, 并添加错误提示
    :param output: 命令的输出。
    :param exit_code: 命令的退出码。
    :return: 格式化后的输出。
    """
    if exit_code in (0, None):
        return output

    return f"Error: command exited with status {exit_code}\n{output}"


def run_bash(command: str) -> str:
    """
    运行 bash 命令并返回格式化后的输出。
    :param command: 要运行的 bash 命令。
    :return: 格式化后的输出。
    """
    output, exit_code = _run_bash_process(command)
    return _format_bash_result(output, exit_code)
