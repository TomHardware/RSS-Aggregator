"""数据库结构引导（轻量迁移）。

`db.create_all()` 只会新建缺失的表，**不会**给已有表补列或补索引。
所以升级时需要在这里幂等地补齐，否则老库升级后会因为少一列而直接报错：

* 补列：`source.enabled`（v2.0.1 新增，用于暂停订阅源）；
* 补索引：`article.link` 唯一索引等（v1.3 起）；
* 清理：历史重复文章（同一 link 只保留 id 最小的一条）。

请在应用上下文内调用：`with app.app_context(): ensure_schema(app)`。
"""
import logging

from sqlalchemy import inspect, text

from app import db

logger = logging.getLogger(__name__)

INDEX_DDL = (
    ("uq_article_link", "CREATE UNIQUE INDEX IF NOT EXISTS uq_article_link ON article (link)"),
    ("ix_article_published", "CREATE INDEX IF NOT EXISTS ix_article_published ON article (published)"),
    ("ix_article_source_id", "CREATE INDEX IF NOT EXISTS ix_article_source_id ON article (source_id)"),
    (
        "ix_article_read_published",
        "CREATE INDEX IF NOT EXISTS ix_article_read_published ON article (read, published)",
    ),
)

# (表, 列, SQLite 默认值, 其他数据库默认值)
COLUMN_MIGRATIONS = (
    ("source", "enabled", "1", "TRUE"),
)

_FIND_DUPLICATES = text(
    "SELECT link FROM article "
    "WHERE link IS NOT NULL AND link <> '' "
    "GROUP BY link HAVING COUNT(*) > 1"
)

_DELETE_DUPLICATES = text(
    "DELETE FROM article WHERE link = :link AND id NOT IN "
    "(SELECT MIN(id) FROM article WHERE link = :link)"
)


def _cleanup_articles(conn):
    """删除重复文章与空链接记录，返回删除条数。"""
    removed = conn.execute(
        text("DELETE FROM article WHERE link IS NULL OR link = ''")
    ).rowcount or 0

    for (link,) in conn.execute(_FIND_DUPLICATES).fetchall():
        removed += conn.execute(_DELETE_DUPLICATES, {"link": link}).rowcount or 0

    if removed:
        logger.warning("已清理 %d 条重复/无效文章记录（升级唯一索引所需）", removed)
    return removed


def _add_missing_columns(conn, dialect):
    """为已存在的表补上模型里新增的列。返回被补充的列名列表。"""
    added = []
    for table, column, sqlite_default, other_default in COLUMN_MIGRATIONS:
        existing_tables = set(inspect(conn).get_table_names())
        if table not in existing_tables:
            continue
        columns = {col["name"] for col in inspect(conn).get_columns(table)}
        if column in columns:
            continue
        default = sqlite_default if dialect == "sqlite" else other_default
        ddl = f"ALTER TABLE {table} ADD COLUMN {column} BOOLEAN DEFAULT {default}"
        try:
            conn.execute(text(ddl))
        except Exception as exc:  # noqa: BLE001 - 补列失败不应阻断启动
            logger.warning("补充列 %s.%s 失败：%s", table, column, exc)
        else:
            added.append(f"{table}.{column}")
    if added:
        logger.info("已补充缺失的数据库列: %s", ", ".join(added))
    return added


def ensure_schema(app=None):
    """建表、补列、补索引；可重复调用。"""
    db.create_all()

    dialect = db.engine.dialect.name
    with db.engine.begin() as conn:
        tables = set(inspect(conn).get_table_names())
        if "article" not in tables:
            return []

        _add_missing_columns(conn, dialect)
        _cleanup_articles(conn)

        for name, ddl in INDEX_DDL:
            try:
                conn.execute(text(ddl))
            except Exception as exc:  # noqa: BLE001 - 索引建不上不应阻断启动
                logger.warning("创建索引 %s 失败：%s", name, exc)

    return [column for _, column, _, _ in COLUMN_MIGRATIONS]
