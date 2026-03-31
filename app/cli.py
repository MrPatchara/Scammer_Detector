"""CLI workflow orchestration."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .exceptions import (
    InvalidAudioFileError,
    ScamDetectorError,
    TranscriptionError,
    WhisperLoadError,
)
from .pipeline import analyze_audio
from .utils import color_text, default_result_filename, highlight_keywords, supports_ansi


def _print_summary(
    result: dict[str, Any],
    color: bool,
) -> None:
    transcribed_text = result["transcribed_text"]
    keywords = result["keywords_found"]
    risk_level = result["risk_level"]
    risk_score = result["risk_score"]
    ai_result = result["ai_analysis"]
    telegram_alert = result.get("telegram_alert", {})

    risk_color = "31" if risk_level == "high" else ("33" if risk_level == "medium" else "32")
    print(color_text("\n=== Thai Voice Scam Detection Result ===", "36", color))
    print(f"Transcribed text: {highlight_keywords(transcribed_text, keywords, use_color=color)}")
    print(f"Keywords found: {', '.join(keywords) if keywords else 'None'}")
    print(f"Risk score: {risk_score}")
    print(f"Risk level: {color_text(risk_level, risk_color, color)}")
    print(f"AI source: {ai_result.get('source', 'keyword_fallback')}")
    print(f"Telegram alert sent: {telegram_alert.get('sent')} ({telegram_alert.get('reason')})")
    print("AI analysis result (JSON):")
    print(json.dumps(ai_result, ensure_ascii=False, indent=2))


def _save_result_json(audio_path: str, payload: dict[str, Any]) -> Path:
    output_path = Path(default_result_filename(audio_path))
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python main.py <audio_file.wav|audio_file.mp3>")
        return 1

    audio_path = sys.argv[1]
    color = supports_ansi()

    try:
        final_output = analyze_audio(audio_path)

        _print_summary(final_output, color)
        saved = _save_result_json(audio_path, final_output)
        print(f"\nSaved full analysis to: {saved}")
        return 0

    except InvalidAudioFileError as exc:
        print(color_text(f"Input error: {exc}", "31", color))
    except WhisperLoadError as exc:
        print(color_text(f"Whisper load error: {exc}", "31", color))
    except TranscriptionError as exc:
        print(color_text(f"Transcription error: {exc}", "31", color))
    except ScamDetectorError as exc:
        print(color_text(f"Detector error: {exc}", "31", color))
    except Exception as exc:
        print(color_text(f"Unexpected error: {exc}", "31", color))

    return 1
