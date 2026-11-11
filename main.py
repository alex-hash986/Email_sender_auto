#!/usr/bin/env python3
"""CLI для генерации и отправки персонализированных писем."""

import argparse
import csv
import logging
import re
import time
from collections import Counter
from datetime import datetime, timezone
from html import escape
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_CSV = PROJECT_DIR / "data" / "contacts_example.csv"
SENT_LOG = PROJECT_DIR / "data" / "sent_log.csv"
MAILER_LOG = PROJECT_DIR / "mailer.log"
LOG_FIELDS = ("timestamp", "email", "name", "company", "subject", "status", "error_details")
EMAIL_PATTERN = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
logger = logging.getLogger(__name__)


def body_to_html(body: str) -> str:
    paragraphs = re.split(r"\r?\n(?:[^\S\r\n]*\r?\n)+", body.strip())
    return "\n".join(
        f'<p style="margin:0 0 16px;">{escape(paragraph.strip())}</p>'
        for paragraph in paragraphs
        if paragraph.strip()
    )


def sent_addresses(path: Path) -> set[str]:
    if not path.exists() or path.stat().st_size == 0:
        return set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != list(LOG_FIELDS):
            raise ValueError(f"Неожиданный заголовок журнала {path}")
        return {
            row["email"].strip().casefold()
            for row in reader
            if row["status"] == "SENT" and row["email"]
        }


def append_result(contact: dict, subject: str, status: str, error: str) -> None:
    SENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SENT_LOG.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=LOG_FIELDS)
        if stream.tell() == 0:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "email": contact["email"],
            "name": contact.get("name", ""),
            "company": contact.get("company", ""),
            "subject": subject,
            "status": status,
            "error_details": error,
        })


def run(args: argparse.Namespace) -> int:
    import pandas as pd
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from tqdm import tqdm
    from tqdm.contrib.logging import logging_redirect_tqdm

    from ai_generator import EmailPersonalizer
    from config import Settings
    from mailer import SMTPMailer, random_delay

    frame = pd.read_csv(args.csv, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    frame.columns = frame.columns.str.strip()
    if "email" not in frame.columns:
        raise ValueError(f"В файле {args.csv} отсутствует колонка email")
    if not frame.columns.is_unique:
        raise ValueError(f"В файле {args.csv} есть повторяющиеся имена колонок")

    sent = sent_addresses(SENT_LOG)
    eligible = []
    already_sent = invalid = duplicates = 0
    seen = set()
    for contact in frame.to_dict("records"):
        contact = {key: value.strip() for key, value in contact.items()}
        address = contact["email"].casefold()
        if not EMAIL_PATTERN.fullmatch(contact["email"]):
            invalid += 1
        elif address in sent:
            already_sent += 1
        elif address in seen:
            duplicates += 1
        else:
            seen.add(address)
            eligible.append(contact)

    contacts = eligible[:args.limit]
    logger.info(
        "Пропущено до обработки: уже отправлено — %d, неверный email — %d, "
        "дубликаты — %d, сверх лимита — %d",
        already_sent, invalid, duplicates, len(eligible) - len(contacts),
    )

    counts = Counter()
    if contacts:
        settings = Settings()
        personalizer = EmailPersonalizer(settings)
        mailer = None if args.dry_run else SMTPMailer(settings)
        template = Environment(
            loader=FileSystemLoader(PROJECT_DIR / "templates"),
            autoescape=select_autoescape(["html"]),
        ).get_template("email_template.html")
        attempted_smtp = False

        with logging_redirect_tqdm(), tqdm(contacts, desc="Обработка", unit="конт.") as progress:
            for contact in progress:
                subject = ""
                error = ""
                try:
                    generated = personalizer.generate_email(contact)
                except Exception as exc:
                    status = "AI_ERROR"
                    error = f"{type(exc).__name__}: {exc}"
                else:
                    subject = generated["subject"]
                    body = generated["body"]
                    try:
                        rendered = template.render(
                            subject=subject,
                            body_html=body_to_html(body),
                            recipient_name=contact.get("name", ""),
                            sender_name=settings.sender_name,
                            company_name="",
                        )
                    except Exception as exc:
                        status = "TEMPLATE_ERROR"
                        error = f"{type(exc).__name__}: {exc}"
                    else:
                        if mailer is None:
                            status = "DRY_RUN"
                        else:
                            if attempted_smtp:
                                random_delay(settings)
                            attempted_smtp = True
                            if mailer.send_email(contact["email"], subject, rendered):
                                status = "SENT"
                            else:
                                status = "SMTP_ERROR"
                                error = "SMTP-отправка не удалась. Подробности в mailer.log"

                append_result(contact, subject, status, error)
                counts[status] += 1
                if error:
                    logger.error("%s %s — %s", status, contact["email"], error)
                else:
                    logger.info("%s %s", status, contact["email"])
                if args.dry_run and sum(counts.values()) < len(contacts):
                    time.sleep(0.5)

    processed = sum(counts.values())
    errors = counts["AI_ERROR"] + counts["TEMPLATE_ERROR"] + counts["SMTP_ERROR"]
    print(
        f"Всего контактов: {len(frame)}\n"
        f"Обработано: {processed}\n"
        f"Успешно: {counts['SENT']}\n"
        f"С ошибками: {errors}\n"
        f"Пропущено: {len(frame) - processed}"
    )
    if args.dry_run:
        print(f"Тестовых писем подготовлено: {counts['DRY_RUN']}")
    return int(errors > 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="CSV-файл с контактами")
    parser.add_argument("--dry-run", action="store_true", help="Подготовить письма без SMTP")
    parser.add_argument("--limit", type=int, help="Максимальное число обрабатываемых контактов")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 0:
        parser.error("--limit должен быть неотрицательным")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(MAILER_LOG, encoding="utf-8")],
    )
    try:
        return run(args)
    except (ImportError, OSError, ValueError) as exc:
        logger.error("Рассылка остановлена: %s", exc)
        return 1
    except KeyboardInterrupt:
        logger.warning("Рассылка прервана")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
