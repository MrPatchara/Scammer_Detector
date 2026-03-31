"""Alert integrations (Telegram)."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import requests


def _level_to_rank(level: str) -> int:
    mapping = {"low": 1, "medium": 2, "high": 3}
    return mapping.get(level.lower(), 0)


def should_alert(risk_level: str, score: int) -> bool:
    min_level = os.getenv("ALERT_MIN_RISK_LEVEL", "high").strip().lower()
    min_score = int(os.getenv("ALERT_MIN_SCORE", "70").strip())
    return _level_to_rank(risk_level) >= _level_to_rank(min_level) and score >= min_score


def send_telegram_alert(result: dict[str, Any]) -> dict[str, Any]:
    """Send alert to Telegram if bot token + chat id are configured."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        return {
            "sent": False,
            "reason": "missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID",
        }

    ai = result.get("ai_analysis", {})
    text = result.get("transcribed_text", "")
    keywords = result.get("keywords_found", [])

    message = (
        "[Scam Alert]\n"
        f"Time: {datetime.utcnow().isoformat()}Z\n"
        f"Risk: {result.get('risk_level')} ({result.get('risk_score')})\n"
        f"AI Source: {ai.get('source', 'unknown')}\n"
        f"Is Scam: {ai.get('is_scam')}\n"
        f"Keywords: {', '.join(keywords) if keywords else '-'}\n"
        f"Reason: {ai.get('reason', '-') }\n"
        f"Transcript: {text[:900]}"
    )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(
        url,
        json={"chat_id": chat_id, "text": message},
        timeout=12,
    )

    if response.ok:
        return {"sent": True, "reason": "ok"}

    return {
        "sent": False,
        "reason": f"telegram api error: {response.status_code}",
        "body": response.text[:300],
    }
