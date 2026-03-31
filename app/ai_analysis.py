"""Prompt-based AI analysis with simulation fallback."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import requests

from .exceptions import AIAnalysisError
from .risk import calculate_risk, keyword_detect

SCAM_ANALYSIS_PROMPT_TEMPLATE = """You are a scam detection AI.

Analyze this Thai text:
"{text}"

Return JSON:
{{
"risk_level": "low | medium | high",
"score": 0-100,
"is_scam": true/false,
"keywords_detected": [],
"reason": "short explanation"
}}

Rules:

* Financial requests increase risk
* Urgency increases risk
* Asking for personal data = high risk
* Impersonation (เจ้าหน้าที่) = high risk
"""


def build_prompt(text: str) -> str:
    return SCAM_ANALYSIS_PROMPT_TEMPLATE.format(text=text)


_GEMINI_BACKOFF_UNTIL = 0.0


def _extract_json_object(raw_text: str) -> dict[str, Any]:
    # Handle pure JSON and markdown fenced JSON output.
    match = re.search(r"\{[\s\S]*\}", raw_text)
    if not match:
        raise ValueError("No JSON object found in model response")
    return json.loads(match.group(0))


def _normalize_ai_output(text: str, payload: dict[str, Any], source: str) -> dict[str, Any]:
    keywords = keyword_detect(text)
    risk = calculate_risk(text, keywords)

    risk_level = str(payload.get("risk_level", risk["risk_level"])).lower()
    if risk_level not in {"low", "medium", "high"}:
        risk_level = risk["risk_level"]

    score_raw = payload.get("score", risk["score"])
    try:
        score = int(score_raw)
    except (TypeError, ValueError):
        score = int(risk["score"])
    score = max(0, min(100, score))

    is_scam = payload.get("is_scam")
    if not isinstance(is_scam, bool):
        is_scam = score >= 40

    kws = payload.get("keywords_detected", keywords)
    if not isinstance(kws, list):
        kws = keywords

    reason = str(payload.get("reason", "")) or "No reason provided"

    return {
        "risk_level": risk_level,
        "score": score,
        "is_scam": is_scam,
        "keywords_detected": kws,
        "reason": reason,
        "source": source,
    }


def _gemini_can_call() -> bool:
    return time.time() >= _GEMINI_BACKOFF_UNTIL


def _set_gemini_backoff(seconds: int) -> None:
    global _GEMINI_BACKOFF_UNTIL
    _GEMINI_BACKOFF_UNTIL = time.time() + max(1, seconds)


def _gemini_analyze(text: str) -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise AIAnalysisError("GEMINI_API_KEY is not configured")

    if not _gemini_can_call():
        raise AIAnalysisError("Gemini backoff active")

    model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
    timeout_sec = float(os.getenv("GEMINI_TIMEOUT_SEC", "7"))
    cooldown_sec = int(os.getenv("GEMINI_COOLDOWN_SEC", "600"))

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )

    prompt = build_prompt(text) + "\nRespond with JSON only."
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }

    try:
        response = requests.post(url, json=payload, timeout=timeout_sec)
    except requests.RequestException as exc:
        raise AIAnalysisError(f"Gemini request failed: {exc}") from exc

    if response.status_code == 429:
        _set_gemini_backoff(cooldown_sec)
        raise AIAnalysisError("Gemini quota/rate limit reached")

    if not response.ok:
        text_body = response.text[:300]
        if "RESOURCE_EXHAUSTED" in text_body or "quota" in text_body.lower():
            _set_gemini_backoff(cooldown_sec)
        raise AIAnalysisError(f"Gemini API error: {response.status_code} {text_body}")

    body = response.json()
    try:
        model_text = body["candidates"][0]["content"]["parts"][0]["text"]
        parsed = _extract_json_object(model_text)
    except Exception as exc:
        raise AIAnalysisError("Failed to parse Gemini response") from exc

    return _normalize_ai_output(text, parsed, source="gemini")


def _simulate_response(text: str) -> dict[str, Any]:
    keywords = keyword_detect(text)
    risk = calculate_risk(text, keywords)
    score = int(risk["score"])

    reasons: list[str] = []
    if any(k in keywords for k in ["โอนเงิน", "บัญชี", "ธนาคาร"]):
        reasons.append("พบเจตนาทางการเงินหรือคำที่เกี่ยวกับบัญชี")
    if any(k in keywords for k in ["ด่วน", "รีบ", "ทันที"]):
        reasons.append("พบแรงกดดันด้านเวลา")
    if any(k in keywords for k in ["OTP", "รหัส", "ยืนยันตัวตน"]):
        reasons.append("พบการร้องขอข้อมูลอ่อนไหว")
    if "เจ้าหน้าที่" in keywords:
        reasons.append("มีการอ้างตัวเป็นเจ้าหน้าที่")

    if not reasons:
        reasons.append("ไม่พบสัญญาณหลอกลวงที่ชัดเจนจากคีย์เวิร์ด")

    return {
        "risk_level": risk["risk_level"],
        "score": score,
        "is_scam": score >= 40,
        "keywords_detected": keywords,
        "reason": " | ".join(reasons),
        "source": "keyword_fallback",
    }


def _api_ready_response(text: str, api_key: str) -> dict[str, Any]:
    _ = api_key
    prompt = build_prompt(text)
    response = _simulate_response(text)
    response["reason"] = (
        "API key detected; currently running simulation mode. Prompt prepared for LLM call. "
        + response["reason"]
    )
    response["prompt_preview"] = prompt[:350]
    return response


def ai_analyze(text: str) -> dict[str, Any]:
    """Required public interface: AI analysis with optional API-key mode."""
    try:
        mode = os.getenv("AI_MODE", "auto").strip().lower()
        if mode not in {"auto", "gemini", "keyword"}:
            mode = "auto"

        if mode == "keyword":
            return _simulate_response(text)

        if mode in {"auto", "gemini"}:
            try:
                return _gemini_analyze(text)
            except Exception as exc:
                fallback = _simulate_response(text)
                fallback["fallback_reason"] = str(exc)
                if mode == "gemini":
                    fallback["forced_gemini"] = True
                return fallback

        return _simulate_response(text)
    except Exception as exc:
        raise AIAnalysisError("AI analysis failed.") from exc


def ai_analysis_json(text: str) -> str:
    return json.dumps(ai_analyze(text), ensure_ascii=False, indent=2)
