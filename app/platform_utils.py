"""跨平台适配的小工具。

Windows / macOS / Linux 的差异集中在这几处，统一收在这里，避免散落各文件：
* 数据目录位置（Windows 用 %APPDATA%，macOS 用 ~/Library/Application Support，Linux 用 XDG）
* 控制台编码（Windows 默认 GBK，日志里的中文会乱码）
* 平台名称与可直接复制的启动命令
"""
import logging
import os
import platform
import sys

logger = logging.getLogger(__name__)

WINDOWS = sys.platform.startswith("win")
MACOS = sys.platform == "darwin"
LINUX = not WINDOWS and not MACOS


def platform_name():
    """人类可读的平台名，用于日志与诊断信息。"""
    if WINDOWS:
        return f"Windows {platform.release()}"
    if MACOS:
        return f"macOS {platform.mac_ver()[0]}"
    return f"Linux {platform.release()}"


def python_command():
    """各平台可直接复制的 Python 命令名（Windows 是 python，其余多为 python3）。"""
    return "python" if WINDOWS else "python3"


def user_data_dir(app_name="RSS-Aggregator"):
    """按各平台惯例返回用户级数据目录（不创建）。"""
    if WINDOWS:
        base = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
        return os.path.join(base, app_name)
    if MACOS:
        return os.path.join(os.path.expanduser("~/Library/Application Support"), app_name)
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, app_name)


def configure_console_encoding():
    """Windows 控制台默认 GBK，中文日志会变成乱码或直接抛异常。"""
    if not WINDOWS:
        return
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - 老终端不支持就算了
            logger.debug("重设 %s 编码失败: %s", stream_name, exc)


def filesystem_hint(path):
    """规范化路径，便于在各平台的文件管理器里直接打开。"""
    return os.path.normpath(path) if path else ""
