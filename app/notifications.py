"""Notification adapters.  Each adapter returns JSON-safe result dictionaries."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Any


def _result(status: str, message: str, **details: Any) -> dict[str, Any]:
    return {"status": status, "message": message, **details}


class WindowsNotifier:
    def send(self, title: str, body: str, url: str | None = None) -> dict[str, Any]:
        if os.name != "nt":
            return _result("UNSUPPORTED", "Windows notifications are only available on Windows")
        try:
            from winotify import Notification

            notification = Notification(app_id="Order Reminder", title=title, msg=body)
            # winotify versions differ in URL action support. Display is still useful
            # even when the installed version cannot open the local dashboard.
            notification.show()
            return _result("SENT", "Windows notification displayed", url_supported=False if url else None)
        except ImportError:
            return _result("UNSUPPORTED", "winotify is not installed")
        except Exception as error:
            return _result("FAILED", f"Windows notification failed: {type(error).__name__}")


class SmtpMailer:
    def send_digest(self, event_type: str, orders: list[dict[str, Any]], settings: dict[str, Any], secret: str | None) -> dict[str, Any]:
        try:
            raw_recipients = settings.get("recipients", [])
            if not isinstance(raw_recipients, (list, tuple)):
                raise ValueError("recipients must be a list")
            recipients = [address.strip() for address in raw_recipients if isinstance(address, str) and address.strip()]
            host = str(settings.get("smtp_host") or "").strip()
            username = str(settings.get("smtp_username") or "").strip()
            port = int(settings.get("smtp_port") or 0)
        except (AttributeError, TypeError, ValueError):
            return _result("NOT_CONFIGURED", "SMTP settings contain invalid values")
        if not recipients or not host or not username or not secret or not 1 <= port <= 65535:
            return _result("NOT_CONFIGURED", "SMTP host, port, username, recipients, and authorization code are required")

        message = EmailMessage()
        message["Subject"] = f"Order reminder: {event_type} ({len(orders)})"
        message["From"] = username
        message["To"] = ", ".join(recipients)
        lines = [f"{item.get('order_no', '')} | {item.get('stage', '')}" for item in orders]
        message.set_content("\n".join([f"Reminder type: {event_type}", "", *lines]))
        try:
            if port == 465:
                with smtplib.SMTP_SSL(host, port, timeout=15) as client:
                    client.login(username, secret)
                    client.send_message(message)
            else:
                with smtplib.SMTP(host, port, timeout=15) as client:
                    client.ehlo()
                    client.starttls()
                    client.ehlo()
                    client.login(username, secret)
                    client.send_message(message)
            return _result("SENT", "Email digest sent", recipients=len(recipients))
        except Exception as error:
            # Never include username, authorization code, or SMTP exception details.
            return _result("FAILED", f"SMTP delivery failed: {type(error).__name__}")
