

from agent.config import TOOL_CWD, WORKDIR


def run_read(path: str, limit: int | None = None) -> str:
    """Read the content of a file.
    Args:
        path: The path to the file.
        limit: The number of lines to read. If None, read all lines.
    Returns:
        The content of the file.
    Raises:
        FileNotFoundError: If the file does not exist.
        PermissionError: If the user does not have permission to read the file.
        OSError: If the file fails for any other reason.
    """
    try:
        cwd = TOOL_CWD.get() or WORKDIR
        lines = (cwd / path).resolve().read_text(encoding="utf-8").splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [
                f"... ({len(lines) - limit} more lines)"
            ]
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"

def run_write(path: str, content: str) -> str:
    """Write content to a file.
    Args:
        path: The path to the file.
        content: The content to write.
    Returns:
        A message indicating the number of bytes written.
    Raises:
        FileNotFoundError: If the file does not exist.
        PermissionError: If the user does not have permission to write the file.
        OSError: If the file fails for any other reason.
    """
    try:
        file_path = ((TOOL_CWD.get() or WORKDIR) / path).resolve()
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"

def run_edit(path: str, old_text: str, new_text: str) -> str:
    """Replace a substring in a file.
    Args:
        path: The path to the file.
        old_text: The substring to replace.
        new_text: The substring to replace with.
    Returns:
        A message indicating the number of bytes written.
    Raises:
        FileNotFoundError: If the file does not exist.
        PermissionError: If the user does not have permission to write the file.
        OSError: If the file fails for any other reason.
    """
    try:
        file_path = ((TOOL_CWD.get() or WORKDIR) / path).resolve()
        text = file_path.read_text(encoding="utf-8")

        if old_text not in text:
            return f"Error: text not found in {path}"

        file_path.write_text(
            text.replace(old_text, new_text, 1),
            encoding="utf-8",
        )
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"

def run_glob(pattern: str) -> str:
    """Find files matching a glob pattern.
    Args:
        pattern: The glob pattern to match.
    Returns:
        A list of matching files.
    Raises:
        ValueError: If the pattern escapes the workspace directory.
    """
    import glob as g  

    try:
        cwd = (TOOL_CWD.get() or WORKDIR).resolve()
        matches = sorted({  # 去重
            match
            for match in g.glob(
                pattern,
                root_dir=cwd,
                recursive=True,
            )
            if (cwd / match).resolve().is_relative_to(cwd)
        })

        shown = matches[:200]  # 只显示前200个匹配


        if len(matches) > 200:
            shown.append(
                "... (more matches omitted; narrow the pattern)"
            )

        return "\n".join(shown) if shown else "(no matches)"
    except Exception as e:
        return f"Error: {e}"
