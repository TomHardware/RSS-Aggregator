# -*- coding: utf-8 -*-
import logging

from app import create_app
from app.scheduler import init_scheduler
from app.schema import ensure_schema
from app.version import GITHUB_URL, __version__

logger = logging.getLogger(__name__)

app = create_app()


def bootstrap():
    """幂等引导：建表 + 补列/索引 + 启动调度器。"""
    with app.app_context():
        ensure_schema(app)
    init_scheduler(app)
    logger.info("%s %s 已启动（%s）", app.config.get("APP_NAME", "RSS Aggregator"), __version__, GITHUB_URL)
    return app


bootstrap()

if __name__ == "__main__":
    app.run(
        host=app.config.get("SERVER_HOST", "0.0.0.0"),
        port=int(app.config.get("SERVER_PORT", 5000)),
        debug=bool(app.config.get("SERVER_DEBUG", False)),
    )
