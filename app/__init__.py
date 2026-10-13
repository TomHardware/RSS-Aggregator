
import logging
import os
import secrets
from datetime import timedelta

from flask import Flask
from flask_sqlalchemy import SQLAlchemy

from app.config import ConfigLoader, DEFAULT_CONFIG_PATH, normalize_sqlite_uri
from app.version import __version__

db = SQLAlchemy()

_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def _configure_logging(app):
    """配置根日志；重复调用不会重复添加 handler。"""
    level = getattr(logging, str(app.config.get("LOG_LEVEL", "INFO")).upper(), logging.INFO)
    log_file = os.path.abspath(app.config.get("LOG_FILE") or "rss_aggregator.log")

    root = logging.getLogger()
    root.setLevel(level)

    has_file = any(
        isinstance(handler, logging.FileHandler)
        and os.path.abspath(getattr(handler, "baseFilename", "")) == log_file
        for handler in root.handlers
    )
    if not has_file:
        try:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
            root.addHandler(file_handler)
        except OSError as exc:
            logging.getLogger(__name__).warning("无法创建日志文件 %s: %s", log_file, exc)

    has_stream = any(
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
        for handler in root.handlers
    )
    if not has_stream:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        root.addHandler(stream_handler)

    # Flask 首次访问 app.logger 时会挂一个默认 handler；而 "app.xxx" 的记录会先经
    # "app" 再经 root，同一行日志会被两个 handler 各输出一次。这里让 app logger 只透传。
    app.logger.handlers.clear()
    app.logger.propagate = True


def _harden_session(app):
    """会话 Cookie 与请求体大小的安全默认值。

    注意 Flask 的默认配置里已经**存在** `SESSION_COOKIE_SAMESITE = None`
    与 `MAX_CONTENT_LENGTH = None`，用 `setdefault` 是改不动的，必须显式赋值。
    """
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = (
        app.config.get("SECURITY_SESSION_COOKIE_SAMESITE") or "Lax"
    )
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
        days=int(app.config.get("AUTH_SESSION_DAYS", 14))
    )
    # 走 HTTPS 的部署应把 security.session_cookie_secure 设为 true
    app.config["SESSION_COOKIE_SECURE"] = bool(
        app.config.get("SECURITY_SESSION_COOKIE_SECURE", False)
    )
    app.config["MAX_CONTENT_LENGTH"] = int(
        app.config.get("SECURITY_MAX_CONTENT_BYTES", 8 * 1024 * 1024)
    )


def create_app(config_path=None):
    app = Flask(__name__)

    config_path = config_path or os.environ.get("RSS_AGGREGATOR_CONFIG") or DEFAULT_CONFIG_PATH
    ConfigLoader(config_path)
    app.config.update(ConfigLoader.to_flask_config())

    database_uri = normalize_sqlite_uri(
        app.config.get("SQLALCHEMY_DATABASE_URI"),
        base_dir=os.path.dirname(DEFAULT_CONFIG_PATH),
    )
    app.config["SQLALCHEMY_DATABASE_URI"] = database_uri
    app.config.setdefault("SQLALCHEMY_TRACK_MODIFICATIONS", False)

    engine_options = {"pool_pre_ping": True}
    if database_uri.startswith("sqlite"):
        # SQLite 并发写会等锁 30 秒（默认 5 秒太短，调度线程与请求线程会互相撞）
        engine_options["connect_args"] = {"timeout": 30}
    app.config.setdefault("SQLALCHEMY_ENGINE_OPTIONS", engine_options)

    # 容器 / CI 里用环境变量提供口令，避免把明文写进配置文件
    env_password = os.environ.get("RSS_AGGREGATOR_PASSWORD")
    if env_password:
        app.config["AUTH_PASSWORD"] = env_password
        app.config["AUTH_PASSWORD_FROM_ENV"] = True

    secret_key = os.environ.get("SECRET_KEY") or app.config.get("SECRET_KEY")
    _configure_logging(app)
    if not secret_key:
        secret_key = secrets.token_hex(32)
        app.logger.warning(
            "未配置 app.secret_key / SECRET_KEY，已生成临时密钥；"
            "重启后登录状态与 flash 消息会失效，生产环境请在 config.yaml 中显式配置"
        )
    app.config["SECRET_KEY"] = secret_key
    app.config["APP_VERSION"] = __version__

    _harden_session(app)
    db.init_app(app)

    from app.auth import auth_enabled, require_login
    from app.web.routes import web_bp

    app.register_blueprint(web_bp)
    app.before_request(require_login)

    if not auth_enabled(app.config):
        app.logger.warning(
            "登录鉴权未启用：请在 config.yaml 里设置 auth.password_hash"
            "（生成方式：python -m app.auth <password>），或设置环境变量 "
            "RSS_AGGREGATOR_PASSWORD。当前任何人都能访问本服务。"
        )

    # 只在这里绑定 app；启动与任务注册由 init_scheduler 负责
    from app.scheduler import scheduler

    scheduler.app = app

    return app
