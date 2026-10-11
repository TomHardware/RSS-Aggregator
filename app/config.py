"""配置加载：YAML → Flask 配置。

修复要点：
1. 原来 `_load_config` 把配置写在**实例属性**上，而 `get()` / `to_flask_config()`
   读的是**类属性** `cls._config`（永远是 None），于是：
     - `ConfigLoader.get()` 恒返回默认值；
     - `to_flask_config()` 在 `'server' in cls._config` 处抛
       `TypeError: argument of type 'NoneType' is not iterable`，应用直接起不来。
   现在统一用类属性承载配置，并支持按路径重新加载。
2. 相对 SQLite 路径锚定到项目根目录，避免 Flask-SQLAlchemy 3.x 把它塞进
   instance 目录（容器里会导致数据不在挂载卷上）。
"""
import logging
import os
import re
import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.yaml")

_SQLITE_PREFIX = "sqlite:///"
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_TRUE_WORDS = {"1", "true", "yes", "on", "enable", "enabled"}
_FALSE_WORDS = {"0", "false", "no", "off", "disable", "disabled"}
LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET")


class ConfigError(ValueError):
    """配置文件里的取值不合法。

    报错必须指明是哪个键、哪个文件——否则用户只会看到
    `ValueError: invalid literal for int() with base 10: 'not-a-number'`。
    """

    def __init__(self, section, key, value, expected, path=None):
        location = f"（配置文件: {path}）" if path else ""
        super().__init__(
            f"配置项 {section}.{key} 需要{expected}，当前值: {value!r}{location}"
        )
        self.section = section
        self.key = key
        self.value = value


def normalize_sqlite_uri(uri, base_dir=None):
    """把相对 SQLite 路径锚定到 base_dir（默认项目根目录），并创建所在目录。

    - `sqlite:///data/rss.db`   → `<base_dir>/data/rss.db`（相对路径被锚定）
    - `sqlite:////app/data/x.db` → 原样返回（已经是绝对路径）
    - `sqlite:///C:/x/rss.db`    → 原样返回（Windows 绝对路径）
    - `sqlite:///:memory:`       → 原样返回
    - 非 sqlite URL              → 原样返回
    """
    if not uri or not isinstance(uri, str):
        return uri
    if not uri.startswith(_SQLITE_PREFIX):
        return uri

    path = uri[len(_SQLITE_PREFIX):]
    if (not path) or path == ":memory:" or path.startswith("/") or _WINDOWS_DRIVE.match(path):
        return uri

    base = base_dir or PROJECT_ROOT
    full_path = os.path.normpath(os.path.join(base, path))
    directory = os.path.dirname(full_path)
    if directory:
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:  # 只读文件系统等：交给 SQLAlchemy 报更明确的错
            logger.warning("无法创建数据库目录 %s: %s", directory, exc)
    return _SQLITE_PREFIX + full_path.replace("\\", "/")


class ConfigLoader:
    """进程内单例；配置存放在类属性上，随配置路径变化而重新加载。"""

    _instance = None
    _config = None
    _config_path = None

    def __new__(cls, config_path=None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        cls._instance._load_config(config_path or DEFAULT_CONFIG_PATH)
        return cls._instance

    def _load_config(self, config_path):
        cls = type(self)
        abs_path = os.path.abspath(config_path)
        if cls._config is not None and cls._config_path == abs_path:
            return  # 同一份配置无需重复读盘
        with open(abs_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise ValueError(f"配置文件格式错误（顶层必须是映射）: {abs_path}")
        cls._config = data
        cls._config_path = abs_path

    # -- 读取 ------------------------------------------------------------- #
    @classmethod
    def _ensure_loaded(cls):
        if cls._config is None:
            cls(DEFAULT_CONFIG_PATH)
        return cls._config

    @classmethod
    def get(cls, key, default=None):
        """按 `a.b.c` 取值，缺失或类型不符时返回 default。"""
        value = cls._ensure_loaded()
        for part in str(key).split("."):
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return default
        return default if value is None else value

    @classmethod
    def to_flask_config(cls):
        """把 YAML 映射成 Flask 风格的大写配置键（取值不合法时抛 ConfigError）。"""
        raw = cls._ensure_loaded()
        path = cls._config_path
        flask_config = {}

        def section(name):
            value = raw.get(name)
            return value if isinstance(value, dict) else {}

        def as_int(section_name, key, value, default, minimum=None, maximum=None):
            if value is None:
                value = default
            if isinstance(value, bool):  # YAML 里的 yes/no 会解析成布尔
                raise ConfigError(section_name, key, value, "一个整数", path)
            try:
                number = int(value)
            except (TypeError, ValueError):
                raise ConfigError(section_name, key, value, "一个整数", path) from None
            if minimum is not None and number < minimum:
                raise ConfigError(section_name, key, value, f"不小于 {minimum} 的整数", path)
            if maximum is not None and number > maximum:
                raise ConfigError(section_name, key, value, f"不大于 {maximum} 的整数", path)
            return number

        def as_bool(section_name, key, value, default):
            if value is None:
                return default
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            if text in _TRUE_WORDS:
                return True
            if text in _FALSE_WORDS:
                return False
            raise ConfigError(section_name, key, value, "一个布尔值（true/false）", path)

        def as_str(section_name, key, value, default):
            if value is None:
                return default
            if not isinstance(value, str):
                raise ConfigError(section_name, key, value, "一个字符串", path)
            return value

        server = section("server")
        flask_config["SERVER_HOST"] = as_str("server", "host", server.get("host"), "0.0.0.0")
        flask_config["SERVER_PORT"] = as_int(
            "server", "port", server.get("port"), 5000, minimum=1, maximum=65535
        )
        flask_config["SERVER_DEBUG"] = as_bool(
            "server", "debug", server.get("debug"), False
        )

        database = section("database")
        flask_config["SQLALCHEMY_DATABASE_URI"] = as_str(
            "database", "url", database.get("url"), "sqlite:///data/rss.db"
        )

        sched = section("scheduler")
        flask_config["SCHEDULER_ENABLED"] = as_bool(
            "scheduler", "enabled", sched.get("enabled"), True
        )
        flask_config["SCHEDULER_DEFAULT_INTERVAL"] = as_int(
            "scheduler", "default_interval", sched.get("default_interval"), 30, minimum=1
        )

        app_section = section("app")
        flask_config["APP_NAME"] = as_str(
            "app", "name", app_section.get("name"), "RSS Aggregator"
        )
        flask_config["APP_LANGUAGE"] = as_str(
            "app", "language", app_section.get("language"), "en"
        )
        flask_config["APP_TIMEZONE"] = as_str(
            "app", "timezone", app_section.get("timezone"), "UTC"
        )
        flask_config["SECRET_KEY"] = app_section.get("secret_key")

        auth = section("auth")
        flask_config["AUTH_ENABLED"] = as_bool("auth", "enabled", auth.get("enabled"), True)
        flask_config["AUTH_USERNAME"] = as_str("auth", "username", auth.get("username"), "admin")
        flask_config["AUTH_PASSWORD_HASH"] = auth.get("password_hash")
        flask_config["AUTH_PASSWORD"] = auth.get("password")
        flask_config["AUTH_SESSION_DAYS"] = as_int(
            "auth", "session_days", auth.get("session_days"), 14, minimum=1
        )

        retention = section("retention")
        flask_config["RETENTION_MAX_ARTICLES_PER_SOURCE"] = as_int(
            "retention", "max_articles_per_source",
            retention.get("max_articles_per_source"), 0, minimum=0,
        )
        flask_config["RETENTION_MAX_AGE_DAYS"] = as_int(
            "retention", "max_age_days", retention.get("max_age_days"), 0, minimum=0
        )

        task_section = section("tasks")
        flask_config["TASKS_MAX_CONCURRENT"] = as_int(
            "tasks", "max_concurrent", task_section.get("max_concurrent"), 5, minimum=1
        )

        email = section("notifications").get("email")
        email = email if isinstance(email, dict) else {}
        flask_config["EMAIL_ENABLED"] = as_bool(
            "notifications.email", "enabled", email.get("enabled"), False
        )
        flask_config["EMAIL_SMTP_SERVER"] = as_str(
            "notifications.email", "smtp_server", email.get("smtp_server"), ""
        )
        flask_config["EMAIL_SMTP_PORT"] = as_int(
            "notifications.email", "smtp_port", email.get("smtp_port"), 587,
            minimum=1, maximum=65535,
        )
        flask_config["EMAIL_USERNAME"] = as_str(
            "notifications.email", "username", email.get("username"), ""
        )
        flask_config["EMAIL_PASSWORD"] = as_str(
            "notifications.email", "password", email.get("password"), ""
        )
        flask_config["EMAIL_FROM"] = as_str(
            "notifications.email", "from_addr", email.get("from_addr"), ""
        )
        flask_config["EMAIL_TO"] = as_str(
            "notifications.email", "to_addr", email.get("to_addr"), ""
        )
        flask_config["EMAIL_USE_TLS"] = as_bool(
            "notifications.email", "use_tls", email.get("use_tls"), True
        )

        security = section("security")
        flask_config["SECURITY_ALLOW_PRIVATE_NETWORKS"] = as_bool(
            "security", "allow_private_networks", security.get("allow_private_networks"), False
        )
        flask_config["SECURITY_CSRF_ORIGIN_CHECK"] = as_bool(
            "security", "csrf_origin_check", security.get("csrf_origin_check"), True
        )
        flask_config["SECURITY_SESSION_COOKIE_SECURE"] = as_bool(
            "security", "session_cookie_secure", security.get("session_cookie_secure"), False
        )
        flask_config["SECURITY_MAX_CONTENT_BYTES"] = as_int(
            "security", "max_content_bytes", security.get("max_content_bytes"),
            8 * 1024 * 1024, minimum=1024,
        )

        fulltext = section("fulltext")
        flask_config["FULLTEXT_ENABLED"] = as_bool(
            "fulltext", "enabled", fulltext.get("enabled"), True
        )
        flask_config["FULLTEXT_MAX_PER_FETCH"] = as_int(
            "fulltext", "max_per_fetch", fulltext.get("max_per_fetch"), 5, minimum=0
        )
        flask_config["FULLTEXT_TIMEOUT"] = as_int(
            "fulltext", "timeout", fulltext.get("timeout"), 10, minimum=1
        )
        flask_config["FULLTEXT_MAX_BYTES"] = as_int(
            "fulltext", "max_bytes", fulltext.get("max_bytes"), 2 * 1024 * 1024, minimum=1
        )

        fetch = section("fetch")
        flask_config["FETCH_RETRIES"] = as_int(
            "fetch", "retries", fetch.get("retries"), 3, minimum=1
        )
        flask_config["FETCH_TIMEOUT"] = as_int(
            "fetch", "timeout", fetch.get("timeout"), 15, minimum=1
        )
        flask_config["FETCH_MAX_ENTRIES"] = as_int(
            "fetch", "max_entries", fetch.get("max_entries"), 50, minimum=0
        )
        flask_config["FETCH_MAX_BYTES"] = as_int(
            "fetch", "max_bytes", fetch.get("max_bytes"), 8 * 1024 * 1024, minimum=1
        )
        flask_config["FETCH_INITIAL_ASYNC"] = as_bool(
            "fetch", "initial_async", fetch.get("initial_async"), True
        )
        flask_config["FETCH_USER_AGENT"] = as_str(
            "fetch", "user_agent", fetch.get("user_agent"),
            "RSSAggregator/1.0 (+feed reader)",
        )

        log_section = section("logging")
        level = str(log_section.get("level", "INFO")).upper()
        if level not in LOG_LEVELS:
            raise ConfigError(
                "logging", "level", log_section.get("level"), f"以下之一 {list(LOG_LEVELS)}", path
            )
        flask_config["LOG_LEVEL"] = level
        flask_config["LOG_FILE"] = as_str(
            "logging", "file", log_section.get("file"), "rss_aggregator.log"
        )

        return flask_config
