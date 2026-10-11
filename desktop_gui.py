"""本地桌面 GUI 启动器。

用法：
    python desktop_gui.py
    python desktop_gui.py --port 8000 --no-browser

等价于 `python -m app.gui`。窗口内会同时启动同一套 Web 服务，
因此也可以用浏览器访问同一个地址。
"""
from app.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
