"""Отправка HTML-писем через SMTP и пауза между отправками."""

import logging
import random
import re
import smtplib
import ssl
import time
from contextlib import suppress
from email.errors import HeaderParseError
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import SMTP
from html.parser import HTMLParser

from config import Settings

logger = logging.getLogger(__name__)


class _PlainTextParser(HTMLParser):
    _hidden_tags = {"head", "script", "style", "title"}
    _block_tags = {"p", "div", "tr", "li", "blockquote", "h1", "h2", "h3"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden: str | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if self.hidden:
            return
        if tag in self._hidden_tags:
            self.hidden = tag
        elif tag == "br" or tag in self._block_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.hidden == tag:
            self.hidden = None
        elif not self.hidden and tag in self._block_tags:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(re.sub(r"\s+", " ", data))


def _html_to_text(body_html: str) -> str:
    parser = _PlainTextParser()
    parser.feed(body_html)
    parser.close()
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line)


class SMTPMailer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._tls_context = ssl.create_default_context()

    def send_email(
        self,
        to_email: str,
        subject: str,
        body_html: str,
        body_text: str | None = None,
    ) -> bool:
        settings = self._settings
        connection = None
        try:
            message = EmailMessage(policy=SMTP)
            message["Subject"] = subject
            message["From"] = Address(settings.sender_name, addr_spec=settings.sender_email)
            message["To"] = Address(addr_spec=to_email)
            message.set_content(
                _html_to_text(body_html) if body_text is None else body_text,
                charset="utf-8",
            )
            message.add_alternative(body_html, subtype="html", charset="utf-8")

            logger.info("Отправка письма на %s", to_email)
            if settings.smtp_port == 465:
                connection = smtplib.SMTP_SSL(
                    settings.smtp_server, settings.smtp_port, timeout=30,
                    context=self._tls_context,
                )
            else:
                connection = smtplib.SMTP(settings.smtp_server, settings.smtp_port, timeout=30)
                connection.starttls(context=self._tls_context)

            connection.login(settings.smtp_username, settings.smtp_password.get_secret_value())
            connection.send_message(message)
        except (smtplib.SMTPException, OSError, ValueError, HeaderParseError) as error:
            logger.error("Ошибка отправки письма на %r: %s", to_email, error)
            return False
        finally:
            if connection is not None:
                with suppress(OSError):
                    connection.close()

        logger.info("SMTP-сервер принял письмо на %s", to_email)
        return True


def random_delay(settings: Settings) -> None:
    seconds = random.uniform(settings.delay_min_seconds, settings.delay_max_seconds)
    print(f"Пауза перед следующей отправкой: {seconds:.1f} с", flush=True)
    time.sleep(seconds)
