import json
import logging
import os
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List

import requests
from bs4 import BeautifulSoup

BOLETIN_BASE_URL = "https://www.boletinoficial.gob.ar"
BOLETIN_DAILY_URL = "https://www.boletinoficial.gob.ar/seccion/primera"

TARGET_MINISTRY_KEYWORDS = [
    "MINISTERIO DE ECONOMÍA",
    "MINISTERIO DE ECONOMIA",
]

TARGET_TECHNICAL_REG_KEYWORDS = [
    "REGLAMENTO TÉCNICO",
    "REGLAMENTOS TÉCNICOS",
    "REGLAMENTO TECNICO",
]

RECIPIENT_EMAIL = os.getenv("AGENT_RECIPIENT_EMAIL", "recipient@example.com")
SENDER_EMAIL = os.getenv("AGENT_SENDER_EMAIL", "your_sender_email@example.com")
SMTP_SERVER = os.getenv("AGENT_SMTP_SERVER", "smtp.example.com")
SMTP_PORT = int(os.getenv("AGENT_SMTP_PORT", "587"))
SMTP_USER = os.getenv("AGENT_SMTP_USER", "smtp_user")
SMTP_PASSWORD = os.getenv("AGENT_SMTP_PASSWORD", "smtp_password")

AUDIT_LOG_FILE = Path(__file__).with_name("reg_monitor_audit_log.jsonl")

LLM_API_KEY = os.getenv("LLM_API_KEY", "YOUR_API_KEY_HERE")
LLM_MODEL_NAME = "gpt-4.1-mini"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def fetch_daily_publications() -> List[Dict[str, Any]]:
    logging.info("Fetching daily publications from Boletín Oficial...")
    resp = requests.get(BOLETIN_DAILY_URL, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    publications: List[Dict[str, Any]] = []
    for item in soup.select("article"):
        title_el = item.select_one("h2, h3")
        link_el = item.select_one("a")
        if not title_el or not link_el:
            continue

        title = title_el.get_text(strip=True)
        relative_url = link_el.get("href")
        if not relative_url:
            continue

        url = (
            relative_url
            if not relative_url.startswith("/")
            else f"{BOLETIN_BASE_URL}{relative_url}"
        )
        publications.append({"title": title, "url": url})

    logging.info("Found %d publications.", len(publications))
    return publications


def fetch_publication_text(url: str) -> str:
    logging.info("Fetching publication content: %s", url)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    content_el = soup.select_one("article")
    if not content_el:
        return ""

    return content_el.get_text(separator="\n", strip=True)


def is_relevant_ministry(text: str) -> bool:
    normalized = text.upper()
    return any(keyword in normalized for keyword in TARGET_MINISTRY_KEYWORDS)


def is_technical_regulation(text: str) -> bool:
    normalized = text.upper()
    return any(keyword in normalized for keyword in TARGET_TECHNICAL_REG_KEYWORDS)


def classify_publication(pub: Dict[str, Any]) -> Dict[str, Any]:
    combined = f"{pub.get('title', '')}\n{pub.get('text', '')}"
    pub["is_ministry_economia"] = is_relevant_ministry(combined)
    pub["is_reglamento_tecnico"] = is_technical_regulation(combined)
    pub["is_relevant"] = pub["is_ministry_economia"] or pub["is_reglamento_tecnico"]
    return pub


def summarize_with_llm(text: str, max_chars: int = 6000) -> str:
    _ = (LLM_API_KEY, LLM_MODEL_NAME)
    trimmed_text = text[:max_chars].strip()
    first_line = trimmed_text.splitlines()[0] if trimmed_text else "Sin contenido disponible."
    return (
        "RESUMEN EJECUTIVO (EJEMPLO):\n"
        "- Resumen generado de forma simulada.\n"
        f"- Extracto analizado: {first_line}\n"
    )


def send_email_notification(subject: str, body: str, recipient: str) -> None:
    logging.info("Sending email notification to %s", recipient)

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = recipient

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)

    logging.info("Email sent")


def append_audit_log(entry: Dict[str, Any]) -> None:
    log_entry = dict(entry)
    log_entry["logged_at"] = datetime.now(timezone.utc).isoformat()
    with AUDIT_LOG_FILE.open("a", encoding="utf-8") as file_obj:
        file_obj.write(json.dumps(log_entry, ensure_ascii=False) + "\n")


def process_new_publications() -> None:
    logging.info("Starting agent run...")

    publications = fetch_daily_publications()
    if not publications:
        logging.info("No publications found.")
        return

    for pub in publications:
        try:
            pub["text"] = fetch_publication_text(pub["url"])
            classify_publication(pub)
            append_audit_log(pub)

            if not pub["is_relevant"]:
                continue

            summary = summarize_with_llm(pub["text"])
            send_email_notification(f"[Regulación AR] {pub['title']}", summary, RECIPIENT_EMAIL)
        except Exception as exc:
            logging.exception(
                "Error processing publication: title=%s url=%s",
                pub.get("title"),
                pub.get("url"),
            )
            append_audit_log({"error": str(exc), "url": pub.get("url"), "title": pub.get("title")})

    logging.info("Run completed successfully")


if __name__ == "__main__":
    process_new_publications()
