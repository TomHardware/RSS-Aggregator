"""本地桌面 GUI（tkinter，Python 自带，无需安装任何额外依赖）。

启动：
    python desktop_gui.py                  # 窗口 + 内置 Web 服务（默认 127.0.0.1:5000）
    python desktop_gui.py --port 8000      # 换端口
    python desktop_gui.py --no-browser     # 不自动打开浏览器

窗口内会启动同一套 Flask 应用（跑在后台线程里），所以：
* 只开这一个窗口就能用，不用另外开终端跑服务；
* 同时也能用浏览器访问同一地址，两边看到的是同一份数据。

界面上的操作直接调用 app/services.py（与网页共用同一套业务逻辑），
不经过 HTTP，因此不存在「GUI 与网页行为不一致」的问题。
"""
import argparse
import logging
import sys
import threading
import webbrowser

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from app import create_app, db
from app.i18n import translate
from app.platform_utils import (
    configure_console_encoding,
    console_hint,
    default_font_family,
    enable_high_dpi,
    platform_name,
    tk_scaling,
)
from app.schema import ensure_schema
from app.scheduler import init_scheduler
from app.services import (
    ServiceError,
    add_feed,
    delete_feed,
    edit_feed,
    fetch_all,
    list_articles,
    list_feeds,
    mark_read,
    refresh_feed,
    start_background_fetch,
    status_snapshot,
    toggle_feed,
    unread_counts,
)
from app.version import __version__

logger = logging.getLogger(__name__)

POLL_INTERVAL_MS = 2000


def start_server(app, host, port):
    """在后台线程里跑 Flask 应用，返回 (server, thread)。"""
    from werkzeug.serving import make_server

    server = make_server(host, port, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, name="rss-web", daemon=True)
    thread.start()
    logger.info("内置 Web 服务已启动: http://%s:%s", host, port)
    return server, thread


class DesktopApp:
    """主窗口。所有数据库操作都在 Tk 主线程内、应用上下文中执行。"""

    def __init__(self, root, app, base_url, lang="zh"):
        self.root = root
        self.app = app
        self.base_url = base_url
        self.lang = lang
        self.current_source_id = None
        self.article_filter = "all"  # all | unread | bookmarked

        root.title(f"RSS Aggregator {__version__}")
        self._apply_theme()
        # 高分屏：Windows 需要显式 DPI 感知（在 main() 里设置），
        # 这里再按真实 DPI 调整 Tk 的缩放系数，避免字体过小。
        scaling = tk_scaling(root)
        if scaling:
            try:
                root.tk.call("tk", "scaling", scaling)
            except Exception:  # noqa: BLE001 - 个别 Tk 版本不支持
                logger.debug("设置 tk scaling 失败", exc_info=True)
        root.geometry("1120x700")
        root.minsize(880, 520)

        self._build_menu()
        self._build_toolbar()
        self._build_body()
        self._build_statusbar()

        self.refresh_feeds()
        self.poll_status()

    # -- 界面 ------------------------------------------------------------- #
    def _t(self, key, **kwargs):
        text = translate(key, self.lang)
        return text.format(**kwargs) if kwargs else text

    def _apply_theme(self):
        """跨平台外观：优先用各平台的现代主题，再按系统深浅色微调。"""
        style = ttk.Style(self.root)
        for candidate in ("vista", "aqua", "clam"):
            if candidate in style.theme_names():
                try:
                    style.theme_use(candidate)
                    break
                except Exception:  # noqa: BLE001
                    continue
        # 显式指定字体家族：空字体名在 Windows 上会退化成宋体
        base = default_font_family()
        try:
            from tkinter import font as tkfont

            for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
                try:
                    tkfont.nametofont(name).configure(family=base)
                except Exception:  # noqa: BLE001
                    continue
            self.heading_font = tkfont.Font(family=base, size=10, weight="bold")
        except Exception:  # noqa: BLE001
            self.heading_font = ("", 10, "bold")

    def _build_menu(self):
        """菜单栏：把不常用操作收进菜单，工具栏只留高频动作。"""
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label=self._t("export_opml"), command=self.on_export_opml)
        file_menu.add_command(label=self._t("gui_export_json"), command=lambda: self.on_export("json"))
        file_menu.add_command(label=self._t("gui_export_csv"), command=lambda: self.on_export("csv"))
        file_menu.add_separator()
        file_menu.add_command(label=self._t("gui_quit"), command=self.root.destroy)
        menubar.add_cascade(label=self._t("gui_menu_file"), menu=file_menu)

        view_menu = tk.Menu(menubar, tearoff=0)
        self.filter_var = tk.StringVar(value="all")
        for label_key, value in (
            ("gui_filter_all", "all"), ("unread_only", "unread"), ("gui_bookmarked_only", "bookmarked"),
        ):
            view_menu.add_radiobutton(
                label=self._t(label_key), value=value, variable=self.filter_var,
                command=self.on_filter_changed,
            )
        view_menu.add_separator()
        view_menu.add_command(label=self._t("gui_open_browser"), command=self.on_open_browser)
        menubar.add_cascade(label=self._t("gui_menu_view"), menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label=self._t("gui_about"), command=self.on_about)
        menubar.add_cascade(label=self._t("gui_menu_help"), menu=help_menu)

        self.root.config(menu=menubar)

    def _build_toolbar(self):
        bar = ttk.Frame(self.root, padding=(8, 6))
        bar.pack(fill="x")

        ttk.Button(bar, text=self._t("gui_add_feed"), command=self.on_add_feed).pack(side="left")
        ttk.Button(bar, text=self._t("gui_refresh_selected"), command=self.on_refresh_selected).pack(
            side="left", padx=4
        )
        ttk.Button(bar, text=self._t("gui_refresh_all"), command=self.on_refresh_all).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text=self._t("gui_toggle_feed"), command=self.on_toggle_feed).pack(side="left")
        ttk.Button(bar, text=self._t("gui_edit_feed"), command=self.on_edit_feed).pack(side="left", padx=4)
        ttk.Button(bar, text=self._t("delete_source"), command=self.on_delete_feed).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text=self._t("gui_open_browser"), command=self.on_open_browser).pack(side="left")
        ttk.Button(bar, text=self._t("gui_open_article"), command=self.on_open_article).pack(
            side="left", padx=4
        )

        # 搜索框：边输边过滤（300ms 防抖，避免每敲一个键就查库）
        search_bar = ttk.Frame(self.root, padding=(8, 0))
        search_bar.pack(fill="x")
        ttk.Label(search_bar, text=self._t("search")).pack(side="left", padx=(0, 6))
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(search_bar, textvariable=self.search_var)
        self.search_entry.pack(side="left", fill="x", expand=True)
        self.search_entry.bind("<KeyRelease>", self.on_search_key)
        self._search_job = None
        ttk.Button(search_bar, text=self._t("gui_clear"), command=self.on_clear_search).pack(side="left", padx=6)

    def _build_body(self):
        body = ttk.Panedwindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=4)

        # 左：订阅源
        left = ttk.Frame(body)
        ttk.Label(left, text=self._t("sources"), font=self.heading_font).pack(anchor="w")
        self.feed_tree = ttk.Treeview(
            left, columns=("name", "category", "interval", "state", "unread", "fetched"),
            show="headings", selectmode="browse", height=18,
        )
        for column, key, width in (
            ("name", "name", 150), ("category", "category", 80), ("interval", "interval", 60),
            ("state", "gui_state", 60), ("unread", "unread", 50), ("fetched", "last_fetched", 130),
        ):
            self.feed_tree.heading(column, text=self._t(key))
            self.feed_tree.column(column, width=width, anchor="w")
        self.feed_tree.pack(fill="both", expand=True)
        self.feed_tree.bind("<<TreeviewSelect>>", self.on_feed_selected)

        # 右：文章
        right = ttk.Frame(body)
        self.article_label = ttk.Label(right, text=self._t("articles"), font=self.heading_font)
        self.article_label.pack(anchor="w")
        self.article_tree = ttk.Treeview(
            right, columns=("title", "source", "published", "read", "mark"),
            show="headings", selectmode="browse",
        )
        for column, key, width in (
            ("title", "gui_article_title", 380), ("source", "sources", 120),
            ("published", "gui_published", 130), ("read", "gui_read_state", 60),
            ("mark", "collections", 40),
        ):
            self.article_tree.heading(column, text=self._t(key))
            self.article_tree.column(column, width=width, anchor="w")
        self.article_tree.pack(fill="both", expand=True)
        self.article_tree.bind("<Double-1>", lambda event: self.on_open_article())
        # 右键菜单：收藏 / 标记已读未读
        self.article_menu = tk.Menu(self.root, tearoff=0)
        self.article_menu.add_command(label=self._t("gui_toggle_bookmark"), command=self.on_toggle_bookmark)
        self.article_menu.add_command(label=self._t("mark_read"), command=lambda: self.on_mark(True))
        self.article_menu.add_command(label=self._t("mark_unread"), command=lambda: self.on_mark(False))
        self.article_tree.bind("<Button-3>", self.on_article_right_click)   # Windows / Linux
        self.article_tree.bind("<Button-2>", self.on_article_right_click)   # macOS
        self.article_tree.bind("<Control-Button-1>", self.on_article_right_click)

        body.add(left, weight=1)
        body.add(right, weight=2)

    def _build_statusbar(self):
        bar = ttk.Frame(self.root, padding=(8, 4))
        bar.pack(fill="x")
        self.status_var = tk.StringVar(value=self._t("gui_ready"))
        ttk.Label(bar, textvariable=self.status_var).pack(side="left")
        ttk.Label(bar, text=f"v{__version__} · {platform_name()}").pack(side="right")

    # -- 数据加载 --------------------------------------------------------- #
    def refresh_feeds(self):
        with self.app.app_context():
            feeds = list_feeds()
            counts, total = unread_counts()
            rows = [
                (
                    str(feed.id), feed.name or feed.url, feed.category or "",
                    f"{feed.interval} min",
                    self._t("gui_paused") if not feed.enabled else "",
                    str(counts.get(feed.id, 0)),
                    str(feed.last_fetched or self._t("never_fetched"))[:19],
                )
                for feed in feeds
            ]
        selected = self.current_source_id
        self.feed_tree.delete(*self.feed_tree.get_children())
        for row in rows:
            self.feed_tree.insert("", "end", iid=row[0], values=row[1:])
        if selected and self.feed_tree.exists(str(selected)):
            self.feed_tree.selection_set(str(selected))
        self.status_var.set(self._t("gui_summary", feeds=len(rows), unread=total))

    def load_articles(self, source_id=None):
        """按当前过滤条件（全部/未读/收藏）与搜索词加载文章。

        搜索走 app.search.search_articles，与网页版共用同一套匹配与转义逻辑。
        """
        keyword = self.search_var.get().strip() if hasattr(self, "search_var") else ""
        mode = self.article_filter
        with self.app.app_context():
            if keyword:
                from app.search import search_articles

                pagination = search_articles(
                    keyword, source_id=source_id, unread_only=(mode == "unread"),
                    bookmarked_only=(mode == "bookmarked"), page=1, per_page=300,
                )
                articles = list(pagination.items)
            else:
                articles = list_articles(
                    source_id=source_id, unread_only=(mode == "unread"),
                    bookmarked_only=(mode == "bookmarked"), limit=300,
                )
            sources = {feed.id: (feed.name or feed.url) for feed in list_feeds()}
            rows = [
                (
                    str(article.id),
                    article.title or "",
                    sources.get(article.source_id, ""),
                    str(article.published or "")[:19],
                    self._t("gui_read") if article.read else self._t("unread"),
                    "★" if article.bookmarked else "",
                )
                for article in articles
            ]
        self.article_tree.delete(*self.article_tree.get_children())
        for row in rows:
            self.article_tree.insert("", "end", iid=row[0], values=row[1:])
        self.article_label.config(text=f"{self._t('articles')} ({len(rows)})")

    def on_search_key(self, event=None):
        """防抖：停止输入 300ms 后再查，避免每敲一个键都查库。"""
        if getattr(self, "_search_job", None):
            self.root.after_cancel(self._search_job)
        self._search_job = self.root.after(300, lambda: self.load_articles(self.current_source_id))

    def on_clear_search(self):
        if hasattr(self, "search_var"):
            self.search_var.set("")
        self.load_articles(self.current_source_id)

    def on_filter_changed(self):
        self.article_filter = self.filter_var.get()
        self.load_articles(self.current_source_id)

    def on_article_right_click(self, event):
        row = self.article_tree.identify_row(event.y)
        if row:
            self.article_tree.selection_set(row)
        try:
            self.article_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.article_menu.grab_release()

    def on_toggle_bookmark(self):
        from app.services import toggle_bookmark

        article = self.selected_article()
        if article is None:
            return
        updated = self._guard(lambda: toggle_bookmark(article.id))
        if updated is not None:
            self.status_var.set(
                self._t("msg_bookmark_on") if updated.bookmarked else self._t("msg_bookmark_off")
            )
            self.load_articles(self.current_source_id)

    def on_mark(self, read):
        from app.services import mark_read

        article = self.selected_article()
        if article is None:
            return
        self._guard(lambda: mark_read(article.id, read))
        self.load_articles(self.current_source_id)

    def on_export(self, fmt):
        """导出到用户选择的文件；格式由后缀决定。"""
        from tkinter import filedialog

        from app.exporter import (
            export_articles_csv,
            export_articles_json,
            export_articles_markdown,
        )

        exporters = {"json": export_articles_json, "csv": export_articles_csv, "md": export_articles_markdown}
        suffix = {"json": ".json", "csv": ".csv", "md": ".md"}[fmt]
        path = filedialog.asksaveasfilename(
            title=self._t("gui_export"), defaultextension=suffix,
            initialfile=f"rss-export{suffix}",
            filetypes=[(fmt.upper(), f"*{suffix}"), (self._t("gui_all_files"), "*.*")],
        )
        if not path:
            return
        try:
            with self.app.app_context():
                payload = exporters[fmt](unread_only=(self.article_filter == "unread"))
            # CSV 写 BOM，Excel 打开中文才不乱码
            encoding = "utf-8-sig" if fmt == "csv" else "utf-8"
            with open(path, "w", encoding=encoding, newline="") as handle:
                handle.write(payload)
        except Exception as exc:  # noqa: BLE001
            logger.exception("导出失败")
            messagebox.showerror(self._t("app_name"), f"{type(exc).__name__}: {exc}")
            return
        self.status_var.set(self._t("gui_export_done", path=path))

    def on_export_opml(self):
        from tkinter import filedialog

        from app.opml import export_opml

        path = filedialog.asksaveasfilename(
            title=self._t("export_opml"), defaultextension=".opml",
            initialfile="feeds.opml", filetypes=[("OPML", "*.opml"), (self._t("gui_all_files"), "*.*")],
        )
        if not path:
            return
        try:
            with self.app.app_context():
                payload = export_opml()
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(payload)
        except Exception as exc:  # noqa: BLE001
            logger.exception("导出 OPML 失败")
            messagebox.showerror(self._t("app_name"), f"{type(exc).__name__}: {exc}")
            return
        self.status_var.set(self._t("gui_export_done", path=path))

    def on_about(self):
        from app.platform_utils import console_hint, user_data_dir

        messagebox.showinfo(
            self._t("gui_about"),
            f"RSS Aggregator {__version__}\n"
            f"{platform_name()}\n"
            f"Python {sys.version.split()[0]}\n\n"
            f"{self._t('gui_data_dir')}: {user_data_dir()}\n"
            f"{self._t('gui_start_hint')}: {console_hint()}",
        )

    def selected_feed_id(self, warn=True):
        selection = self.feed_tree.selection()
        if not selection:
            if warn:
                messagebox.showinfo(self._t("app_name"), self._t("gui_select_feed_first"))
            return None
        return int(selection[0])

    def selected_article(self, warn=True):
        selection = self.article_tree.selection()
        if not selection:
            if warn:
                messagebox.showinfo(self._t("app_name"), self._t("gui_select_article_first"))
            return None
        with self.app.app_context():
            from app.models import Article

            return db.session.get(Article, int(selection[0]))

    # -- 动作 ------------------------------------------------------------- #
    def _guard(self, func, success_key=None):
        """统一处理失败提示，避免未捕获异常把窗口弄崩。"""
        try:
            result = func()
        except ServiceError as exc:
            messagebox.showerror(self._t("app_name"), self._t(exc.message_key, **exc.params))
            return None
        except Exception as exc:  # noqa: BLE001
            logger.exception("GUI 操作失败")
            messagebox.showerror(self._t("app_name"), f"{type(exc).__name__}: {exc}")
            return None
        if success_key:
            self.status_var.set(self._t(success_key))
        self.refresh_feeds()
        return result

    def on_add_feed(self):
        dialog = AddFeedDialog(self.root, self)
        self.root.wait_window(dialog.window)

    def on_feed_selected(self, event=None):
        selection = self.feed_tree.selection()
        if not selection:
            return
        self.current_source_id = int(selection[0])
        self.load_articles(self.current_source_id)

    def on_refresh_selected(self):
        source_id = self.selected_feed_id()
        if source_id is None:
            return
        self.status_var.set(self._t("gui_fetching"))
        started = self._guard(
            lambda: start_background_fetch(source_id, kind="manual_fetch"), None
        )
        if started is None:  # 并发已满 → 同步抓取
            self._guard(lambda: refresh_feed(source_id), "msg_refresh_ok")
        if self.current_source_id == source_id:
            self.load_articles(source_id)

    def on_refresh_all(self):
        self._guard(lambda: fetch_all(), "gui_fetch_all_started")

    def on_toggle_feed(self):
        source_id = self.selected_feed_id()
        if source_id is None:
            return
        source = self._guard(lambda: toggle_feed(source_id))
        if source is not None:
            key = "msg_source_resumed" if source.enabled else "msg_source_paused"
            self.status_var.set(self._t(key, name=source.name or source.url))

    def on_edit_feed(self):
        source_id = self.selected_feed_id()
        if source_id is None:
            return
        with self.app.app_context():
            from app.models import Source

            source = db.session.get(Source, source_id)
            current = (source.name or "", source.category or "", source.interval)
        name = simpledialog.askstring(self._t("gui_edit_feed"), self._t("name"), initialvalue=current[0])
        if name is None:
            return
        category = simpledialog.askstring(
            self._t("gui_edit_feed"), self._t("category"), initialvalue=current[1]
        )
        if category is None:
            return
        interval = simpledialog.askstring(
            self._t("interval"), self._t("interval"), initialvalue=str(current[2])
        )
        if interval is None:
            return
        source = self._guard(lambda: edit_feed(source_id, name, category, interval))
        if source is not None:
            self.status_var.set(self._t("msg_source_updated", name=source.name))

    def on_delete_feed(self):
        source_id = self.selected_feed_id()
        if source_id is None:
            return
        if not messagebox.askyesno(self._t("app_name"), self._t("delete_confirm")):
            return
        self._guard(lambda: delete_feed(source_id), "msg_source_deleted")
        self.current_source_id = None
        self.article_tree.delete(*self.article_tree.get_children())

    def on_open_article(self):
        article = self.selected_article()
        if article is None:
            return
        if article.link and article.link.startswith("http"):
            webbrowser.open(article.link)
            self._guard(lambda: mark_read(article.id, True))
            self.load_articles(self.current_source_id)

    def on_open_browser(self):
        webbrowser.open(self.base_url)

    # -- 轮询 ------------------------------------------------------------- #
    def poll_status(self):
        try:
            with self.app.app_context():
                snapshot = status_snapshot()
            running = snapshot["running"]
            recent = snapshot["recent"]
            if running:
                names = "、".join(task.get("source_name") or f"#{task['source_id']}" for task in running)
                self.status_var.set(self._t("gui_fetching_named", names=names))
            elif recent:
                latest = recent[0]
                if latest["state"] == "success":
                    self.status_var.set(
                        self._t("gui_last_result", count=latest["new_articles"],
                                name=latest.get("source_name") or "")
                    )
                else:
                    self.status_var.set(self._t("gui_last_failed", name=latest.get("source_name") or ""))
            # 有任务刚结束时刷新列表，等于网页版的「自动刷新」
            if not running and recent and getattr(self, "_last_running", 0) > 0:
                self.refresh_feeds()
                self.load_articles(self.current_source_id)
            self._last_running = len(running)
        except Exception:  # noqa: BLE001 - 轮询失败不能中断界面
            logger.debug("状态轮询失败", exc_info=True)
        finally:
            self.root.after(POLL_INTERVAL_MS, self.poll_status)


class AddFeedDialog:
    """新增订阅源的小对话框。"""

    def __init__(self, parent, app_window):
        self.app_window = app_window
        self.window = tk.Toplevel(parent)
        self.window.title(app_window._t("gui_add_feed"))
        self.window.transient(parent)
        self.window.grab_set()
        self.window.resizable(False, False)

        frame = ttk.Frame(self.window, padding=12)
        frame.pack(fill="both", expand=True)
        self.url_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.category_var = tk.StringVar(value="General")
        self.interval_var = tk.StringVar(value="30")

        for row, (label_key, variable, width) in enumerate(
            (
                ("gui_feed_url", self.url_var, 46),
                ("name", self.name_var, 46),
                ("category", self.category_var, 46),
                ("interval", self.interval_var, 12),
            )
        ):
            ttk.Label(frame, text=app_window._t(label_key)).grid(row=row, column=0, sticky="w", pady=4)
            entry = ttk.Entry(frame, textvariable=variable, width=width)
            entry.grid(row=row, column=1, sticky="we", pady=4)
            if row == 0:
                entry.focus_set()

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=2, pady=(10, 0), sticky="e")
        ttk.Button(buttons, text=app_window._t("save"), command=self.on_save).pack(side="left")
        ttk.Button(buttons, text=app_window._t("gui_cancel"), command=self.window.destroy).pack(
            side="left", padx=6
        )
        self.window.bind("<Return>", lambda event: self.on_save())

    def on_save(self):
        source = self.app_window._guard(
            lambda: add_feed(
                self.url_var.get(), self.name_var.get(), self.category_var.get(),
                self.interval_var.get(),
            ),
            "msg_source_added_fetching",
        )
        if source is None:
            return
        self.window.destroy()
        self.app_window.current_source_id = source.id
        self.app_window._guard(
            lambda: start_background_fetch(source.id, source.name), None
        )
        if self.app_window.feed_tree.exists(str(source.id)):
            self.app_window.feed_tree.selection_set(str(source.id))


def main(argv=None):
    parser = argparse.ArgumentParser(description="RSS Aggregator 本地桌面 GUI")
    parser.add_argument("--host", default=None, help="内置 Web 服务监听地址")
    parser.add_argument("--port", type=int, default=None, help="内置 Web 服务端口")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args(argv)

    app = create_app()
    host = args.host or app.config.get("SERVER_HOST", "127.0.0.1")
    port = int(args.port or app.config.get("SERVER_PORT", 5000))
    if host == "0.0.0.0":  # 桌面场景默认只听本机
        host = "127.0.0.1"

    with app.app_context():
        ensure_schema(app)
    init_scheduler(app)

    # 必须在创建窗口之前：Windows 上先声明 DPI 感知，窗口才不会被系统拉伸糊掉
    enable_high_dpi()
    server, _thread = start_server(app, host, port)
    base_url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}"

    try:
        root = tk.Tk()
    except tk.TclError as exc:  # 无图形环境（例如服务器上 ssh）
        logger.error("无法创建窗口（当前环境没有图形界面）: %s", exc)
        print(
            "当前环境无法显示图形界面，请改用网页版 / No display available, use the web UI:\n"
            f"  {'python' if sys.platform.startswith('win') else 'python3'} main.py\n"
            f"  {base_url}\n"
            f"(启动命令 / start command: {console_hint()})"
        )
        server.shutdown()
        return 2

    logger.info("桌面 GUI 启动于 %s（%s）", platform_name(), base_url)
    DesktopApp(root, app, base_url, lang=app.config.get("APP_LANGUAGE", "zh"))
    if not args.no_browser:
        webbrowser.open(base_url)
    try:
        root.mainloop()
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
