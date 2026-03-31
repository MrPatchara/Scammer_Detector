"""Backward-compatible public API exports for AI analysis functions."""

from app.ai_analysis import (
    SCAM_ANALYSIS_PROMPT_TEMPLATE,
    ai_analysis_json,
    ai_analyze,
    build_prompt,
)
from app.exceptions import AIAnalysisError

__all__ = [
    "SCAM_ANALYSIS_PROMPT_TEMPLATE",
    "AIAnalysisError",
    "build_prompt",
    "ai_analyze",
    "ai_analysis_json",
]
