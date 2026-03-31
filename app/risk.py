"""Keyword detection and risk scoring logic."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from difflib import SequenceMatcher
from typing import Any

from .config import get_runtime_config


NOISY_ASR_PATTERNS: list[tuple[str, str]] = [
    ("โอนเงิน", r"(โอนเงิน|โอนเข้าบัญชี|โอนเข้า|โอนมา|โน่นมา|โน่นเข้า|โน่นเงิน)"),
    ("บัญชี", r"(บัญชี|บันชี|บันช[ีิ]|บญชี)"),
    ("ธนาคาร", r"(ธนาคาร|ธนคาร|แบงก์|แบงค์|bank)"),
    ("ยืนยันตัวตน", r"(ยืนยันตัวตน|ยืนยัน|verify|เวอริฟาย)"),
    ("เจ้าหน้าที่", r"(เจ้าหน้าที่|จนท\.?|คอลเซ็นเตอร์|call\s*center)"),
    ("รหัส", r"(รหัส|otp|โอทีพี|one\s*time\s*password)"),
    ("ทันที", r"(ทันที|เดี๋ยวนี้|ตอนนี้เลย|ด่วน)"),
    ("ลิงก์", r"(ลิงก์|ลิงค์|link|คลิก|กดลิงก์)"),
]


def _normalize_text(text: str) -> str:
    lowered = text.lower().strip()
    lowered = re.sub(r"\s+", " ", lowered)
    # Compress excessive repeated characters from noisy transcription.
    lowered = re.sub(r"(.)\1{2,}", r"\1\1", lowered)
    return lowered


def _fuzzy_match(s1: str, s2: str, threshold: float = 0.75) -> bool:
    """Check if two strings are similar enough (for Thai keyword matching)."""
    ratio = SequenceMatcher(None, s1, s2).ratio()
    return ratio >= threshold


@dataclass
class RiskResult:
    score: int
    risk_level: str


def keyword_detect(text: str) -> list[str]:
    config = get_runtime_config()
    thai_keywords = config["keywords"]
    lowered_text = _normalize_text(text)
    detected: list[str] = []

    # Exact match first
    for keyword in thai_keywords:
        if keyword in lowered_text:
            detected.append(keyword)

    # Fuzzy match for words not caught by exact match
    # Split text into words for better matching
    text_words = lowered_text.split()
    for keyword in thai_keywords:
        if keyword not in detected:
            keyword_words = keyword.split()
            # Try fuzzy matching on individual words and phrases
            for text_word in text_words:
                if _fuzzy_match(keyword, text_word, threshold=0.75) or \
                   any(_fuzzy_match(kw, text_word, threshold=0.75) for kw in keyword_words):
                    detected.append(keyword)
                    break

    for canonical, pattern in NOISY_ASR_PATTERNS:
        if canonical in detected:
            continue
        if re.search(pattern, lowered_text):
            detected.append(canonical)

    return detected


def _bounded_ratio(hits: int, normalizer: float) -> float:
    if normalizer <= 0:
        return 0.0
    return max(0.0, min(1.0, hits / normalizer))


def _scam_probability(
    keyword_set: set[str],
    category_hits: dict[str, int],
    text_lower: str,
) -> float:
    sensitive_ratio = _bounded_ratio(category_hits["sensitive"], 2.0)
    impersonation_ratio = _bounded_ratio(category_hits["impersonation"], 2.0)
    financial_ratio = _bounded_ratio(category_hits["financial"], 3.0)
    urgency_ratio = _bounded_ratio(category_hits["urgency"], 2.0)
    link_ratio = _bounded_ratio(category_hits["link_install"], 2.0)
    legal_ratio = _bounded_ratio(category_hits["legal_threat"], 2.0)
    fraud_context_ratio = _bounded_ratio(category_hits["fraud_context"], 2.0)

    intent_terms = [
        "ขอ",
        "ส่ง",
        "แจ้ง",
        "กรอก",
        "ยืนยัน",
        "กด",
        "คลิก",
        "โอน",
        "ต้อง",
        "ช่วย",
        "ตามที่บอก",
        "ตรวจสอบ",
        "ตรวสอบ",
    ]
    # Use fuzzy matching for intent detection
    text_words = text_lower.split()
    intent_hits = 0
    for term in intent_terms:
        if term in text_lower:
            intent_hits += 1
        else:
            # Fuzzy match individual words
            for word in text_words:
                if _fuzzy_match(term, word, threshold=0.7):
                    intent_hits += 1
                    break
    intent_ratio = _bounded_ratio(intent_hits, 4.0)

    combo_bonus = 0.0
    if category_hits["impersonation"] > 0 and category_hits["sensitive"] > 0:
        combo_bonus += 0.08
    if category_hits["financial"] > 0 and category_hits["urgency"] > 0:
        combo_bonus += 0.06
    if category_hits["link_install"] > 0 and category_hits["urgency"] > 0:
        combo_bonus += 0.05
    if category_hits["legal_threat"] > 0 and category_hits["financial"] > 0:
        combo_bonus += 0.05
    if category_hits["fraud_context"] > 0 and (
        category_hits["financial"] > 0 or category_hits["link_install"] > 0
    ):
        combo_bonus += 0.06

    linear = (
        -2.15
        + (1.85 * sensitive_ratio)
        + (1.45 * impersonation_ratio)
        + (1.1 * financial_ratio)
        + (0.95 * urgency_ratio)
        + (1.2 * link_ratio)
        + (0.9 * legal_ratio)
        + (1.25 * fraud_context_ratio)
        + (0.75 * intent_ratio)
        + (0.25 * _bounded_ratio(len(keyword_set), 8.0))
        + combo_bonus
    )
    probability = 1.0 / (1.0 + math.exp(-linear))
    return max(0.01, min(0.99, probability))


def calculate_risk(text: str, keywords: list[str]) -> dict[str, Any]:
    config = get_runtime_config()
    categories = config["categories"]
    weights = config["weights"]

    text_lower = _normalize_text(text)
    keyword_set = set(keywords)

    category_hits = {
        "urgency": len(keyword_set & categories["urgency"]),
        "financial": len(keyword_set & categories["financial"]),
        "sensitive": len(keyword_set & categories["sensitive"]),
        "impersonation": len(keyword_set & categories["impersonation"]),
        "link_install": len(keyword_set & categories["link_install"]),
        "legal_threat": len(keyword_set & categories["legal_threat"]),
        "fraud_context": len(keyword_set & categories["fraud_context"]),
    }

    weighted_score = min(
        len(keyword_set) * float(weights["base_keyword"]),
        float(weights["keyword_cap"]),
    )
    weighted_score += category_hits["urgency"] * float(weights["urgency"])
    weighted_score += category_hits["financial"] * float(weights["financial"])
    weighted_score += category_hits["sensitive"] * float(weights["sensitive"])
    weighted_score += category_hits["impersonation"] * float(weights["impersonation"])
    weighted_score += category_hits["link_install"] * float(weights["link_install"])
    weighted_score += category_hits["legal_threat"] * float(weights["legal_threat"])
    weighted_score += category_hits["fraud_context"] * float(weights["fraud_context"])

    if "ขอ" in text_lower and (
        "ข้อมูล" in text_lower
        or "ส่วนตัว" in text_lower
        or "เลขบัตร" in text_lower
        or "otp" in text_lower
        or "รหัส" in text_lower
    ):
        weighted_score += float(weights["request_sensitive_bonus"])
    transfer_intent = bool(re.search(r"(โอน|โน่น|ชำระ|จ่าย)", text_lower))
    if transfer_intent and (
        "ทันที" in text_lower or "เดี๋ยวนี้" in text_lower or "ด่วน" in text_lower
    ):
        weighted_score += float(weights["transfer_now_bonus"])
    if (
        category_hits["link_install"] > 0
        and ("ติดตั้ง" in text_lower or "ดาวน์โหลด" in text_lower or "apk" in text_lower)
    ):
        weighted_score += float(weights["remote_install_bonus"])
    if category_hits["impersonation"] > 0 and category_hits["sensitive"] > 0:
        weighted_score += float(weights["impersonation_sensitive_bonus"])
    if category_hits["legal_threat"] > 0 and category_hits["financial"] > 0:
        weighted_score += float(weights["legal_financial_bonus"])

    account_verification_pressure = (
        category_hits["financial"] > 0
        and ("บัญชี" in text_lower or "บันชี" in text_lower)
        and ("ตรวจสอบ" in text_lower or "ตรวสอบ" in text_lower)
        and (
            "ต้อง" in text_lower
            or "ช่วย" in text_lower
            or "ตามที่บอก" in text_lower
            or "ทันที" in text_lower
        )
    )
    if account_verification_pressure:
        weighted_score += 16.0

    probability = _scam_probability(keyword_set, category_hits, text_lower)
    probability_mix = max(0.0, min(1.0, float(weights["probability_mix"])))
    score = round((weighted_score * (1.0 - probability_mix)) + ((probability * 100.0) * probability_mix))

    score = max(0, min(100, score))

    high_threshold = int(weights["high_threshold"])
    medium_threshold = int(weights["medium_threshold"])

    if score >= high_threshold:
        level = "high"
    elif score >= medium_threshold:
        level = "medium"
    else:
        level = "low"

    result = RiskResult(score=score, risk_level=level)
    return {
        "score": result.score,
        "risk_level": result.risk_level,
        "probability": round(probability, 4),
    }
