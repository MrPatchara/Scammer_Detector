"""Backward-compatible public API exports for detector functions."""

from app.config import THAI_KEYWORDS
from app.exceptions import (
    InvalidAudioFileError,
    ScamDetectorError,
    TranscriptionError,
    WhisperLoadError,
)
from app.risk import calculate_risk, keyword_detect
from app.stt import WhisperThaiTranscriber, speech_to_text
from app.utils import default_result_filename, highlight_keywords

__all__ = [
    "THAI_KEYWORDS",
    "ScamDetectorError",
    "InvalidAudioFileError",
    "WhisperLoadError",
    "TranscriptionError",
    "WhisperThaiTranscriber",
    "speech_to_text",
    "keyword_detect",
    "calculate_risk",
    "highlight_keywords",
    "default_result_filename",
]
