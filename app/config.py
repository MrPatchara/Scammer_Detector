"""Configuration constants for scam detector."""

from __future__ import annotations

THAI_KEYWORDS = [
    "โอนเงิน",
    "OTP",
    "รหัส",
    "บัญชี",
    "ธนาคาร",
    "ด่วน",
    "รีบ",
    "ทันที",
    "เจ้าหน้าที่",
    "ยืนยันตัวตน",
    "ลิงก์",
]

SUPPORTED_AUDIO_EXTENSIONS = {".wav", ".mp3"}
DEFAULT_WHISPER_MODEL = "small"

URGENCY_WORDS = {"ด่วน", "รีบ", "ทันที"}
FINANCIAL_WORDS = {"โอนเงิน", "บัญชี", "ธนาคาร"}
SENSITIVE_WORDS = {"OTP", "รหัส", "ยืนยันตัวตน"}
IMPERSONATION_WORDS = {"เจ้าหน้าที่"}
