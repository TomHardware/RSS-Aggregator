"""OPML 导入 / 导出。

修复要点：
1. `outline.getparent()` 是 lxml 的 API，stdlib `xml.etree.ElementTree` 没有，
   原实现每次导入都抛 AttributeError，被宽泛的 except 吞掉后返回 (0, 1)——
   也就是「一键迁移订阅」从来没能用。这里自己建父节点映射。
2. 导入的订阅地址同样要过 SSRF 校验。
3. 单条记录失败不再中断整批导入（用 savepoint 隔离），并且不再吞掉异常细节。
4. 导出改为 utf-8 字节流 + XML 声明，避免声明与实际编码不一致。
"""
import logging
from xml.etree import ElementTree as ET

from flask import current_app, has_app_context
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Source
from app.urlsafety import is_safe_url

logger = logging.getLogger(__name__)

DEFAULT_CATEGORY = "Imported"


def export_opml():
    """导出全部订阅，返回 utf-8 编码的 OPML 字节串。"""
    opml = ET.Element("opml", version="2.0")
    head = ET.SubElement(opml, "head")
    ET.SubElement(head, "title").text = "RSS Aggregator Feeds"
    body = ET.SubElement(opml, "body")

    categories = {}
    for source in Source.query.order_by(Source.id).all():
        categories.setdefault(source.category or "General", []).append(source)

    for category, sources in categories.items():
        outline = ET.SubElement(body, "outline", text=category, title=category)
        for source in sources:
            label = source.name or source.url
            ET.SubElement(
                outline,
                "outline",
                type="rss",
                text=label,
                title=label,
                xmlUrl=source.url,
                htmlUrl=source.url,
            )
    return ET.tostring(opml, encoding="utf-8", xml_declaration=True)


def _build_parent_map(root):
    """stdlib ElementTree 没有 getparent()，这里自己建一张父节点映射表。"""
    return {child: parent for parent in root.iter() for child in parent}


def _category_of(outline, parents):
    parent = parents.get(outline)
    if parent is not None and parent.tag == "outline":
        name = parent.get("text") or parent.get("title")
        if name:
            return name
    return DEFAULT_CATEGORY


def import_opml(opml_content, allow_private=None):
    """导入 OPML，返回 (成功数, 失败数)。可重复导入，不会产生重复源。"""
    if isinstance(opml_content, bytes):
        opml_content = opml_content.decode("utf-8", errors="replace")
    if not opml_content or not opml_content.strip():
        logger.error("OPML 内容为空")
        return 0, 1

    if allow_private is None:
        allow_private = bool(
            current_app.config.get("SECURITY_ALLOW_PRIVATE_NETWORKS", False)
            if has_app_context()
            else False
        )

    try:
        root = ET.fromstring(opml_content)
    except ET.ParseError as exc:
        logger.error("OPML 解析失败: %s", exc)
        return 0, 1

    parents = _build_parent_map(root)
    success = 0
    failed = 0

    for outline in root.iter("outline"):
        xml_url = (outline.get("xmlUrl") or "").strip()
        if not xml_url:
            continue  # 分类节点，没有订阅地址

        if not is_safe_url(xml_url, allow_private=allow_private):
            logger.warning("跳过被安全策略拒绝的订阅地址: %s", xml_url)
            failed += 1
            continue

        if Source.query.filter_by(url=xml_url).first():
            continue  # 已经订阅过，静默跳过

        label = (outline.get("text") or outline.get("title") or xml_url).strip()
        source = Source(
            name=label[:200],
            url=xml_url[:500],
            category=(_category_of(outline, parents) or DEFAULT_CATEGORY)[:100],
        )
        try:
            with db.session.begin_nested():  # 单条失败不影响其它条目
                db.session.add(source)
        except IntegrityError:
            logger.warning("跳过重复订阅: %s", xml_url)
            continue
        success += 1

    try:
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        logger.error("OPML 导入提交失败: %s", exc)
        return 0, failed + success

    return success, failed
