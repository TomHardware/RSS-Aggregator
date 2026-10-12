"""跨平台适配的小工具。

Windows / macOS / Linux 的差异集中在几处，统一收在这里，避免散落各文件：
* 数据目录位置（Windows 用 %APPDATA%，macOS 用 ~/Library/Application Support，Linux 用 XDG）
* DPI 缩放（Windows 需要显式声明感知，否则高分屏上界面模糊）
* 控制台编码（Windows 默认 GBK，日志里的中文会乱码）
* 用户默认字体（tkinter 的空字体名在各平台后果不同）
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
    """人类可读的平台名，用于日志与「关于」信息。"""
    if WINDOWS:
        return f"Windows {platform.release()}"
    if MACOS:
        return f"macOS {platform.mac_ver()[0]}"
    return f"Linux {platform.release()}"


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


def enable_high_dpi():
    """Windows 高分屏：不声明 DPI 感知的话，tkinter 窗口会被系统拉伸而模糊。"""
    if not WINDOWS:
        return False
    try:
        import ctypes

        # 2 = PROCESS_PER_MONITOR_DPI_AWARE, 1 = PROCESS_SYSTEM_DPI_AWARE
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001 - Win8 以下没有 shcore
            ctypes.windll.user32.SetProcessDPIAware()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("设置 DPI 感知失败: %s", exc)
        return False


def tk_scaling(root):
    """计算 tkinter 的缩放系数。

    Windows 用 winfo_fpixels 得到真实 DPI；macOS 的 Tk 已经处理好了；
    Linux 一般也是 96。返回值直接给 tk.call('tk', 'scaling', ...)。
    """
    try:
        dpi = root.winfo_fpixels("1i")
    except Exception:  # noqa: BLE001
        return None
    if dpi and abs(dpi - 96) > 1:
        return dpi / 72.0  # tk 的 scaling 单位是「点」
    return None


def default_font_family():
    """选一个各平台都存在的界面字体。

    tkinter 里 `font=("", size)` 的空字体名**不会**继承主题字体，
    而是退回一个可能很丑的默认字体（Windows 上实测退化成宋体），
    所以这里显式按平台给出候选，并交给 Tk 自己回退。
    """
    if WINDOWS:
        return "Microsoft YaHei UI"
    if MACOS:
        return "PingFang SC"
    return "Noto Sans CJK SC"


def console_hint():
    """按平台给出可直接复制的启动命令提示。"""
    if WINDOWS:
        return "python desktop_gui.py"
    return "python3 desktop_gui.py"
