"""Keyword detection and risk scoring logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import (
    FINANCIAL_WORDS,
    IMPERSONATION_WORDS,
    SENSITIVE_WORDS,
    THAI_KEYWORDS,
    URGENCY_WORDS,
)


@dataclass
class RiskResult:
    score: int
    risk_level: str


def keyword_detect(text: str) -> list[str]:
    lowered_text = text.lower()
    detected: list[str] = []

    for keyword in THAI_KEYWORDS:
        if keyword.lower() in lowered_text:
            detected.append(keyword)

    return detected


def calculate_risk(text: str, keywords: list[str]) -> dict[str, Any]:
    score = min(len(set(keywords)) * 8, 40)
    keyword_set = set(keywords)

    score += len(keyword_set & URGENCY_WORDS) * 10
    score += len(keyword_set & FINANCIAL_WORDS) * 12
    score += len(keyword_set & SENSITIVE_WORDS) * 18
    score += len(keyword_set & IMPERSONATION_WORDS) * 15

    if "ขอ" in text and ("ข้อมูล" in text or "ส่วนตัว" in text):
        score += 10
    if "โอน" in text and "ทันที" in text:
        score += 12

    score = max(0, min(100, score))

    if score >= 70:
        level = "high"
    elif score >= 40:
        level = "medium"
    else:
        level = "low"

    result = RiskResult(score=score, risk_level=level)
    return {"score": result.score, "risk_level": result.risk_level}
