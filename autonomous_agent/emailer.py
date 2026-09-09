from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage


def send_report(subject: str, body: str) -> bool:
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "465"))
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_APP_PASSWORD", "").strip()
    recipient = os.getenv("REPORT_EMAIL", "").strip()
    if not all((username, password, recipient)):
        return False
    msg = EmailMessage()
    msg["From"] = username
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL(host, port, timeout=20) as server:
            server.login(username, password)
            server.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException):
        return False
