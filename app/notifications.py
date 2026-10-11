"""邮件通知。

修复要点：
1. `to_addr` 支持多个收件人（英文逗号/分号分隔）。原来把整串当成一个地址塞进
   `[to_addr]`，配多个收件人时发信必然失败。
2. 只有配置完整才发信，并显式 ehlo；发送失败返回 False 而不是静默。
3. `MIMEMultipart()` 补上 subtype，避免生成不规范邮件。
"""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import current_app

logger = logging.getLogger(__name__)


def _recipients(raw):
    if not raw:
        return []
    items = raw if isinstance(raw, (list, tuple)) else str(raw).replace(";", ",").split(",")
    return [item.strip() for item in items if item and item.strip()]


def send_email(subject, body):
    """按配置发送通知邮件，返回是否发送成功（不抛异常）。"""
    if not current_app.config.get("EMAIL_ENABLED", False):
        logger.info("邮件通知未启用，跳过: %s", subject)
        return False

    recipients = _recipients(current_app.config.get("EMAIL_TO"))
    sender = (current_app.config.get("EMAIL_FROM") or "").strip() or (
        recipients[0] if recipients else ""
    )
    smtp_server = (current_app.config.get("EMAIL_SMTP_SERVER") or "").strip()
    if not recipients or not sender or not smtp_server:
        logger.error("邮件配置不完整（需要 smtp_server / from_addr / to_addr）")
        return False

    message = MIMEMultipart("mixed")
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.attach(MIMEText(body, "plain", "utf-8"))

    server = None
    try:
        server = smtplib.SMTP(
            smtp_server, int(current_app.config.get("EMAIL_SMTP_PORT", 587)), timeout=10
        )
        server.ehlo()
        if current_app.config.get("EMAIL_USE_TLS", True):
            server.starttls()
            server.ehlo()
        username = current_app.config.get("EMAIL_USERNAME")
        if username:
            server.login(username, current_app.config.get("EMAIL_PASSWORD", ""))
        server.sendmail(sender, recipients, message.as_string())
        logger.info("通知邮件已发送: %s", subject)
        return True
    except Exception as exc:  # noqa: BLE001 - 通知失败不能影响主流程
        logger.error("通知邮件发送失败: %s", exc)
        return False
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:  # noqa: BLE001
                pass
