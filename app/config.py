"""Configuration constants and runtime keyword loading for scam detector."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SUPPORTED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".webm", ".mp4"}
DEFAULT_WHISPER_MODEL = "large"

# Common Whisper Thai transcription errors - partial word corrections
THAI_TEXT_CORRECTIONS = {
    # Common initial mistakes
    "ส่วดดี": "สวัสดี",
    "สวัสดี": "สวัสดี",
    "สวา": "สวัสดี",
    
    # Authority/Police variations
    "ตำรถ": "ตำรวจ",
    "ตำรต": "ตำรวจ",
    "ตำรวจ": "ตำรวจ",
    "หน่าที่": "หน้าที่",
    "หน้าที่": "หน้าที่",
    
    # Inspection/Check variations
    "ตรวด": "ตรวจ",
    "ตรด": "ตรวจ",
    "ตรวจ": "ตรวจ",
    "โตสอบ": "ตรวจสอบ",
    "ทรสออก": "ตรวจสอบ",
    "ทรดสอบ": "ตรวจสอบ",
    "เพื่อโตสอบ": "เพื่อตรวจสอบ",
    "เพื่อตรวจ": "เพื่อตรวจสอบ",
    
    # Crime/wrongdoing variations
    "พิด": "ผิด",
    "พิก": "ผิด",
    "ผิด": "ผิด",
    "กระทา": "กระทำ",
    "กระทำ": "กระทำ",
    "กระทามความผิด": "กระทำความผิด",
    "กระทามความพิก": "กระทำความผิด",
    "กระทามความพิด": "กระทำความผิด",
    
    # Account/Bank variations
    "บันชี": "บัญชี",
    "ปันชี": "บัญชี",
    "ปสชี": "บัญชี",
    "บัญชี": "บัญชี",
    "บัญชั": "บัญชี",
    "เลขบัญชี": "เลขบัญชี",
    "บัญชีเลข": "เลขบัญชี",
    
    # Money transfer variations
    "อนื่น": "โอนเงิน",
    "อ้อนนึน": "โอนเงิน",
    "เงิน": "เงิน",
    "โอนน": "โอนเงิน",
    "โอนนเงิน": "โอนเงิน",
    "โอนเงิน": "โอนเงิน",
    
    # Please/Request variations
    "กระนา": "กรุณา",
    "กรูนา": "กรุณา",
    "กรุณา": "กรุณา",
    "ช่วย": "ช่วย",
    
    # Password/verification variations
    "พือทรัส": "เพื่อตรวจสอบ",
    "พืเทรส": "เพื่อตรวจสอบ",
    
    # Police/authority
    "ปปง": "ปปง",
    "dsi": "dsi",
}

DEFAULT_THAI_KEYWORDS = [
    "โอนเงิน",
    "โอนเข้าบัญชี",
    "โอนด่วน",
    "พร้อมเพย์",
    "promptpay",
    "otp",
    "one time password",
    "รหัส",
    "รหัสผ่าน",
    "pin",
    "cvv",
    "cvc",
    "รหัส sms",
    "รหัสยืนยัน",
    "บัญชี",
    "เลขบัญชี",
    "ธนาคาร",
    "ตัดบัญชี",
    "หักบัญชี",
    "เงินประกัน",
    "มัดจำ",
    "ค่าธรรมเนียม",
    "ค่าปรับ",
    "ค่าปลดล็อก",
    "เติมเงิน",
    "ทรูวอลเล็ต",
    "wallet",
    "e-wallet",
    "ด่วน",
    "รีบ",
    "ทันที",
    "เดี๋ยวนี้",
    "ภายในวันนี้",
    "ก่อนหมดเวลา",
    "มิฉะนั้น",
    "หากไม่ดำเนินการ",
    "ระงับบัญชี",
    "อายัดบัญชี",
    "ล็อกบัญชี",
    "ดำเนินคดี",
    "ฟ้องร้อง",
    "เจ้าหน้าที่",
    "call center",
    "คอลเซ็นเตอร์",
    "ตำรวจ",
    "ปปง",
    "dsi",
    "กรมสอบสวนคดีพิเศษ",
    "กสทช",
    "สรรพากร",
    "การไฟฟ้า",
    "การประปา",
    "ไปรษณีย์ไทย",
    "ยืนยันตัวตน",
    "สแกนหน้า",
    "สแกนใบหน้า",
    "บัตรประชาชน",
    "เลขบัตรประชาชน",
    "เลขหลังบัตร",
    "ข้อมูลส่วนตัว",
    "หน้าบัตร",
    "หลังบัตร",
    "ลิงก์",
    "กดลิงก์",
    "คลิกลิงก์",
    "โหลดแอป",
    "ดาวน์โหลดแอป",
    "แอปดูดเงิน",
    "ติดตั้งแอป",
    "รีโมตหน้าจอ",
    "แชร์หน้าจอ",
    "anydesk",
    "teamviewer",
    "quicksupport",
    "apk",
    "พัสดุตกค้าง",
    "พัสดุผิดกฎหมาย",
    "กู้เงินด่วน",
    "สินเชื่อด่วน",
    "งานออนไลน์ได้เงินจริง",
    "ภารกิจกดรับออเดอร์",
    "รับงานรีวิว",
    "ลงทุนผลตอบแทนสูง",
    "ปันผลรายวัน",
    "แชร์ลูกโซ่",
]

DEFAULT_CATEGORIES = {
    "urgency": {
        "ด่วน",
        "รีบ",
        "ทันที",
        "เดี๋ยวนี้",
        "ภายในวันนี้",
        "ก่อนหมดเวลา",
        "มิฉะนั้น",
        "หากไม่ดำเนินการ",
    },
    "financial": {
        "โอนเงิน",
        "โอนเข้าบัญชี",
        "โอนด่วน",
        "พร้อมเพย์",
        "promptpay",
        "บัญชี",
        "เลขบัญชี",
        "ธนาคาร",
        "ตัดบัญชี",
        "หักบัญชี",
        "เงินประกัน",
        "มัดจำ",
        "ค่าธรรมเนียม",
        "ค่าปรับ",
        "ค่าปลดล็อก",
        "เติมเงิน",
        "ทรูวอลเล็ต",
        "wallet",
        "e-wallet",
    },
    "sensitive": {
        "otp",
        "one time password",
        "รหัส",
        "รหัสผ่าน",
        "pin",
        "cvv",
        "cvc",
        "รหัส sms",
        "รหัสยืนยัน",
        "ยืนยันตัวตน",
        "สแกนหน้า",
        "สแกนใบหน้า",
        "บัตรประชาชน",
        "เลขบัตรประชาชน",
        "เลขหลังบัตร",
        "ข้อมูลส่วนตัว",
        "หน้าบัตร",
        "หลังบัตร",
    },
    "impersonation": {
        "เจ้าหน้าที่",
        "call center",
        "คอลเซ็นเตอร์",
        "ตำรวจ",
        "ปปง",
        "dsi",
        "กรมสอบสวนคดีพิเศษ",
        "กสทช",
        "สรรพากร",
        "การไฟฟ้า",
        "การประปา",
        "ไปรษณีย์ไทย",
    },
    "link_install": {
        "ลิงก์",
        "กดลิงก์",
        "คลิกลิงก์",
        "โหลดแอป",
        "ดาวน์โหลดแอป",
        "แอปดูดเงิน",
        "ติดตั้งแอป",
        "รีโมตหน้าจอ",
        "แชร์หน้าจอ",
        "anydesk",
        "teamviewer",
        "quicksupport",
        "apk",
    },
    "legal_threat": {
        "ระงับบัญชี",
        "อายัดบัญชี",
        "ล็อกบัญชี",
        "ดำเนินคดี",
        "ฟ้องร้อง",
        "พัสดุตกค้าง",
        "พัสดุผิดกฎหมาย",
    },
    "fraud_context": {
        "งานออนไลน์ได้เงินจริง",
        "ภารกิจกดรับออเดอร์",
        "รับงานรีวิว",
        "ลงทุนผลตอบแทนสูง",
        "ปันผลรายวัน",
        "แชร์ลูกโซ่",
        "พัสดุติดศุลกากร",
        "ค่าปลดปล่อยพัสดุ",
    },
}

DEFAULT_RISK_WEIGHTS = {
    "base_keyword": 7.0,
    "keyword_cap": 50.0,
    "urgency": 9.0,
    "financial": 11.0,
    "sensitive": 17.0,
    "impersonation": 15.0,
    "link_install": 13.0,
    "legal_threat": 14.0,
    "fraud_context": 16.0,
    "request_sensitive_bonus": 10.0,
    "transfer_now_bonus": 12.0,
    "remote_install_bonus": 16.0,
    "impersonation_sensitive_bonus": 10.0,
    "legal_financial_bonus": 8.0,
    "probability_mix": 0.35,
    "high_threshold": 68.0,
    "medium_threshold": 38.0,
}

_RUNTIME_CACHE: dict[str, Any] | None = None
_RUNTIME_FILE_MTIME: float | None = None


def _normalize_keyword(term: str) -> str:
    return term.strip().lower()


def _normalize_keyword_list(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        term = _normalize_keyword(str(value))
        if not term or term in seen:
            continue
        normalized.append(term)
        seen.add(term)
    return normalized


def _normalize_keyword_set(values: set[str] | list[str]) -> set[str]:
    return set(_normalize_keyword_list(list(values)))


def _resolve_external_config_path() -> Path:
    configured = os.getenv("SCAM_KEYWORDS_FILE", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent.parent / "keywords.json"


def _load_external_config() -> dict[str, Any]:
    path = _resolve_external_config_path()
    if not path.exists():
        return {}

    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return {}
        return payload
    except Exception:
        # Fail-safe: invalid JSON should not crash detection.
        return {}


def _merge_runtime_config(payload: dict[str, Any]) -> dict[str, Any]:
    replace = bool(payload.get("replace", False))

    if replace:
        keywords: list[str] = []
        categories = {
            "urgency": set(),
            "financial": set(),
            "sensitive": set(),
            "impersonation": set(),
            "link_install": set(),
            "legal_threat": set(),
            "fraud_context": set(),
        }
    else:
        keywords = _normalize_keyword_list(DEFAULT_THAI_KEYWORDS)
        categories = {name: set(values) for name, values in DEFAULT_CATEGORIES.items()}

    ext_keywords = payload.get("keywords", [])
    if isinstance(ext_keywords, list):
        keywords = _normalize_keyword_list(keywords + [str(item) for item in ext_keywords])

    ext_categories = payload.get("categories", {})
    if isinstance(ext_categories, dict):
        for name in categories:
            ext_values = ext_categories.get(name)
            if isinstance(ext_values, list):
                categories[name] |= _normalize_keyword_set([str(item) for item in ext_values])

    weights = dict(DEFAULT_RISK_WEIGHTS)
    ext_weights = payload.get("weights", {})
    if isinstance(ext_weights, dict):
        for key, value in ext_weights.items():
            if key not in weights:
                continue
            try:
                weights[key] = float(value)
            except (TypeError, ValueError):
                continue

    for name, values in categories.items():
        keywords = _normalize_keyword_list(keywords + list(values))
        categories[name] = _normalize_keyword_set(list(values))

    return {
        "keywords": keywords,
        "categories": categories,
        "weights": weights,
    }


def _sync_exported_constants(config: dict[str, Any]) -> None:
    global THAI_KEYWORDS
    global URGENCY_WORDS
    global FINANCIAL_WORDS
    global SENSITIVE_WORDS
    global IMPERSONATION_WORDS
    global LINK_INSTALL_WORDS
    global LEGAL_THREAT_WORDS
    global FRAUD_CONTEXT_WORDS
    global RISK_WEIGHTS

    categories = config["categories"]
    THAI_KEYWORDS = list(config["keywords"])
    URGENCY_WORDS = set(categories["urgency"])
    FINANCIAL_WORDS = set(categories["financial"])
    SENSITIVE_WORDS = set(categories["sensitive"])
    IMPERSONATION_WORDS = set(categories["impersonation"])
    LINK_INSTALL_WORDS = set(categories["link_install"])
    LEGAL_THREAT_WORDS = set(categories["legal_threat"])
    FRAUD_CONTEXT_WORDS = set(categories["fraud_context"])
    RISK_WEIGHTS = dict(config["weights"])


def get_runtime_config() -> dict[str, Any]:
    global _RUNTIME_CACHE
    global _RUNTIME_FILE_MTIME

    path = _resolve_external_config_path()
    mtime = path.stat().st_mtime if path.exists() else None

    if _RUNTIME_CACHE is None or mtime != _RUNTIME_FILE_MTIME:
        payload = _load_external_config()
        _RUNTIME_CACHE = _merge_runtime_config(payload)
        _RUNTIME_FILE_MTIME = mtime
        _sync_exported_constants(_RUNTIME_CACHE)

    return _RUNTIME_CACHE


def reload_runtime_config() -> dict[str, Any]:
    global _RUNTIME_CACHE
    global _RUNTIME_FILE_MTIME
    _RUNTIME_CACHE = None
    _RUNTIME_FILE_MTIME = None
    return get_runtime_config()


# Initialize exported constants at import time.
get_runtime_config()


def load_thai_stt_corrections() -> dict[str, str]:
    """Load Thai STT corrections from JSON file or fallback to hardcoded dict.
    
    This allows easy updates to the correction mappings without code changes.
    Returns a flat dict of {wrong_text: correct_text} mappings.
    """
    # Try to load from JSON file
    json_path = Path(__file__).resolve().parent.parent / "thai_stt_corrections.json"
    
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Flatten nested structure: common_errors -> category -> {wrong: correct}
            corrections = {}
            common_errors = data.get("common_errors", {})
            if isinstance(common_errors, dict):
                for category_name, category_dict in common_errors.items():
                    if isinstance(category_dict, dict):
                        corrections.update(category_dict)
            return corrections
        except Exception:
            # If JSON loading fails, fall back to hardcoded
            pass
    
    # Fallback to hardcoded dictionary
    return THAI_TEXT_CORRECTIONS
