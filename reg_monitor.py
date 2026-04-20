# -*- coding: utf-8 -*-

import json
import logging
import os
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin
from urllib.request import Request, urlopen

# =========================
# CONFIGURATION
# =========================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOLETIN_BASE_URL = os.getenv("BOLETIN_BASE_URL", "https://www.boletinoficial.gob.ar")
BOLETIN_DAILY_URL = os.getenv(
    "BOLETIN_DAILY_URL",
    "https://www.boletinoficial.gob.ar/seccion/primera",
)
HTTP_USER_AGENT = os.getenv(
    "AGENT_HTTP_USER_AGENT",
    "Mozilla/5.0 (compatible; RegMonitor/1.0; +https://www.boletinoficial.gob.ar)",
)

TARGET_MINISTRY_KEYWORDS = [
    "MINISTERIO DE ECONOMÍA",
    "MINISTERIO DE ECONOMIA",
]

TARGET_TECHNICAL_REG_KEYWORDS = [
    "REGLAMENTO TÉCNICO",
    "REGLAMENTOS TÉCNICOS",
    "REGLAMENTO TECNICO",
]

RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", "paula.calviello@dell.com")
SENDER_EMAIL = os.getenv("AGENT_SENDER_EMAIL", "your_sender_email@example.com")
SMTP_SERVER = os.getenv("AGENT_SMTP_SERVER", "smtp.example.com")
SMTP_PORT = int(os.getenv("AGENT_SMTP_PORT", "587"))
SMTP_USER = os.getenv("AGENT_SMTP_USER", "smtp_user")
SMTP_PASSWORD = os.getenv("AGENT_SMTP_PASSWORD", "smtp_password")

AUDIT_LOG_FILE = os.path.join(BASE_DIR, "reg_monitor_audit_log.jsonl")

LLM_API_KEY = os.getenv("LLM_API_KEY", "YOUR_API_KEY_HERE")
LLM_MODEL_NAME = "gpt-4.1-mini"

# =========================
# LOGGING SETUP
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


class DailyPublicationsParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.in_article = False
        self.in_heading = False
        self.current_title_parts: List[str] = []
        self.current_url: Optional[str] = None
        self.publications: List[Dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        attrs_dict = dict(attrs)

        if tag == "article":
            self.in_article = True
            self.in_heading = False
            self.current_title_parts = []
            self.current_url = None
            return

        if not self.in_article:
            return

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.in_heading = True

        if tag == "a" and not self.current_url:
            href = attrs_dict.get("href")
            if href:
                self.current_url = urljoin(self.base_url, href)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.in_heading = False

        if tag == "article" and self.in_article:
            title = " ".join(part.strip() for part in self.current_title_parts if part.strip()).strip()
            if title and self.current_url:
                self.publications.append(
                    {
                        "title": title,
                        "url": self.current_url,
                    }
                )

            self.in_article = False
            self.in_heading = False
            self.current_title_parts = []
            self.current_url = None

    def handle_data(self, data: str) -> None:
        if self.in_article and self.in_heading:
            self.current_title_parts.append(data)


class ArticleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.article_depth = 0
        self.parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        if tag == "article":
            self.article_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "article" and self.article_depth > 0:
            self.article_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.article_depth > 0:
            value = data.strip()
            if value:
                self.parts.append(value)

    def text(self) -> str:
        return "\n".join(self.parts)


# =========================
# HTTP / PARSING
# =========================


def fetch_url_text(url: str, timeout: int = 30) -> str:
    request = Request(url, headers={"User-Agent": HTTP_USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        encoding = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(encoding, errors="replace")


# =========================
# SCRAPER AGENT
# =========================


def fetch_daily_publications() -> List[Dict[str, Any]]:
    logging.info("Fetching daily publications from Boletín Oficial...")
    html = fetch_url_text(BOLETIN_DAILY_URL)

    parser = DailyPublicationsParser(BOLETIN_BASE_URL)
    parser.feed(html)

    logging.info("Found %d publications.", len(parser.publications))
    return parser.publications


def fetch_publication_text(url: str) -> str:
    logging.info("Fetching publication content: %s", url)
    html = fetch_url_text(url)

    parser = ArticleTextParser()
    parser.feed(html)
    return parser.text()


# =========================
# CLASSIFICATION AGENT
# =========================


def is_relevant_ministry(text: str) -> bool:
    return any(keyword in text.upper() for keyword in TARGET_MINISTRY_KEYWORDS)


def is_technical_regulation(text: str) -> bool:
    return any(keyword in text.upper() for keyword in TARGET_TECHNICAL_REG_KEYWORDS)


def classify_publication(pub: Dict[str, Any]) -> Dict[str, Any]:
    combined = f"{pub.get('title', '')}\n{pub.get('text', '')}"

    pub["is_ministry_economia"] = is_relevant_ministry(combined)
    pub["is_reglamento_tecnico"] = is_technical_regulation(combined)
    pub["is_relevant"] = pub["is_ministry_economia"] or pub["is_reglamento_tecnico"]

    return pub


# =========================
# SUMMARIZATION AGENT
# =========================


def summarize_with_llm(text: str, max_chars: int = 6000) -> str:
    text = text[:max_chars]
    return (
        "RESUMEN EJECUTIVO (EJEMPLO):\n"
        "- Resumen generado de forma simulada.\n"
        "- Norma con posibles impactos regulatorios.\n"
        f"- Longitud del texto analizado: {len(text)} caracteres.\n"
    )


# =========================
# EMAIL
# =========================


def smtp_is_configured() -> bool:
    placeholder_values = {
        "your_sender_email@example.com",
        "smtp.example.com",
        "smtp_user",
        "smtp_password",
    }
    values = {SENDER_EMAIL, SMTP_SERVER, SMTP_USER, SMTP_PASSWORD}
    return not values.intersection(placeholder_values)


def send_email_notification(subject: str, body: str, recipient: str) -> None:
    if not smtp_is_configured():
        logging.warning(
            "SMTP is not configured. Skipping email send. Set AGENT_SENDER_EMAIL, "
            "AGENT_SMTP_SERVER, AGENT_SMTP_USER and AGENT_SMTP_PASSWORD."
        )
        return

    logging.info("Sending email notification to %s", recipient)

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = recipient

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)

    logging.info("Email sent")


# =========================
# AUDIT
# =========================


def append_audit_log(entry: Dict[str, Any]) -> None:
    entry = dict(entry)
    entry["logged_at"] = datetime.now(timezone.utc).isoformat()
    with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as file_handle:
        file_handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


# =========================
# ORCHESTRATION
# =========================


def process_new_publications() -> None:
    logging.info("Starting agent run...")

    publications = fetch_daily_publications()
    if not publications:
        logging.info("No publications found.")
        print("RUN OK - sin publicaciones encontradas")
        return

    processed_count = 0
    relevant_count = 0

    for pub in publications:
        try:
            pub["text"] = fetch_publication_text(pub["url"])
            classify_publication(pub)
            append_audit_log(pub)
            processed_count += 1

            if not pub["is_relevant"]:
                continue

            relevant_count += 1
            summary = summarize_with_llm(pub["text"])

            send_email_notification(
                f"[Regulación AR] {pub['title']}",
                summary,
                RECIPIENT_EMAIL,
            )

        except Exception as error:
            logging.exception("Error processing publication")
            append_audit_log(
                {
                    "title": pub.get("title"),
                    "url": pub.get("url"),
                    "error": str(error),
                }
            )

    print(f"RUN OK - procesadas: {processed_count}, relevantes: {relevant_count}")


# =========================
# ENTRY POINT
# =========================


if __name__ == "__main__":
    process_new_publications()
