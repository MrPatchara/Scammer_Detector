"""Shared analysis pipeline for CLI and UI."""

from __future__ import annotations

from typing import Any

from .ai_analysis import ai_analyze
from .alerts import send_telegram_alert, should_alert
from .risk import calculate_risk, keyword_detect
from .stt import speech_to_text


def analyze_audio(audio_path: str) -> dict[str, Any]:
    text = speech_to_text(audio_path)
    keywords = keyword_detect(text)
    risk = calculate_risk(text, keywords)
    ai_result = ai_analyze(text)

    result: dict[str, Any] = {
        "audio_file": audio_path,
        "transcribed_text": text,
        "keywords_found": keywords,
        "risk_score": risk["score"],
        "risk_level": risk["risk_level"],
        "ai_analysis": ai_result,
    }

    alert_info: dict[str, Any] = {"sent": False, "reason": "not_triggered"}
    if should_alert(result["risk_level"], int(result["risk_score"])):
        alert_info = send_telegram_alert(result)

    result["telegram_alert"] = alert_info
    return result
