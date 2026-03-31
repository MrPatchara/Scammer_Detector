"""Realtime scam monitoring using audio files instead of microphone.

Useful for testing the pipeline with pre-recorded audio files.

Two modes:
1. Process single file:
   python realtime_file.py path/to/audio.wav

2. Watch folder for files (auto-process as they appear):
   python realtime_file.py --watch ./audio_files/

Environment variables:
    REALTIME_FILE_AI_GATE_SCORE=45
    REALTIME_FILE_AI_TRIGGERS=otp,โอนเงิน,เจ้าหน้าที่,บัญชี
    REALTIME_FILE_ALERT_COOLDOWN_SECONDS=15
    REALTIME_FILE_OUTPUT_DIR=./file_results
    REALTIME_FILE_POLL_SECONDS=1.0          # For folder watch mode
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.ai_analysis import ai_analyze
from app.alerts import send_telegram_alert, should_alert
from app.risk import calculate_risk, keyword_detect
from app.stt import speech_to_text


SUPPORTED_AUDIO = {'.wav', '.mp3', '.m4a', '.ogg', '.webm', '.mp4', '.flac'}


@dataclass
class FileConfig:
    ai_gate_score: int
    ai_triggers: set[str]
    alert_cooldown_seconds: int
    output_dir: Path
    poll_seconds: float


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def load_config() -> FileConfig:
    trigger_raw = os.getenv("REALTIME_FILE_AI_TRIGGERS", "otp,โอนเงิน,เจ้าหน้าที่,บัญชี")
    ai_triggers = {x.strip().lower() for x in trigger_raw.split(",") if x.strip()}

    return FileConfig(
        ai_gate_score=max(0, _env_int("REALTIME_FILE_AI_GATE_SCORE", 45)),
        ai_triggers=ai_triggers,
        alert_cooldown_seconds=max(1, _env_int("REALTIME_FILE_ALERT_COOLDOWN_SECONDS", 15)),
        output_dir=Path(os.getenv("REALTIME_FILE_OUTPUT_DIR", "file_results")).resolve(),
        poll_seconds=max(0.1, _env_float("REALTIME_FILE_POLL_SECONDS", 1.0)),
    )


def _build_result(audio_path: Path, text: str, cooldown_ok: bool) -> dict[str, Any]:
    """Build analysis result from transcribed text."""
    if not text.strip():
        return {
            "audio_file": str(audio_path),
            "transcribed_text": "",
            "keywords_found": [],
            "risk_score": 0,
            "risk_level": "low",
            "ai_analysis": {
                "risk_level": "low",
                "score": 0,
                "is_scam": False,
                "reason": "No speech detected",
                "source": "empty_audio",
            },
            "telegram_alert": {"sent": False, "reason": "no_speech"},
        }
    
    keywords = keyword_detect(text)
    risk = calculate_risk(text, keywords)

    lowered_keywords = {k.lower() for k in keywords}
    trigger_hit = any(k in lowered_keywords for k in CONFIG.ai_triggers)
    use_ai = int(risk.get("score", 0)) >= CONFIG.ai_gate_score or trigger_hit

    if use_ai:
        ai_result = ai_analyze(text)
    else:
        ai_result = {
            "risk_level": risk.get("risk_level", "low"),
            "score": int(risk.get("score", 0)),
            "is_scam": risk.get("risk_level") == "high",
            "keywords_detected": keywords,
            "reason": "keyword gate: score below AI threshold and no trigger keyword",
            "source": "keyword_gate",
        }

    result: dict[str, Any] = {
        "audio_file": str(audio_path),
        "transcribed_text": text,
        "keywords_found": keywords,
        "risk_score": risk["score"],
        "risk_level": risk["risk_level"],
        "ai_analysis": ai_result,
    }

    alert_info: dict[str, Any] = {"sent": False, "reason": "not_triggered"}
    if should_alert(result["risk_level"], int(result["risk_score"])):
        if cooldown_ok:
            alert_info = send_telegram_alert(result)
        else:
            alert_info = {"sent": False, "reason": "cooldown"}

    result["telegram_alert"] = alert_info
    return result


def _print_result(result: dict[str, Any]) -> None:
    print("=" * 80)
    print(
        f"file={Path(result.get('audio_file', '')).name} "
        f"risk={result.get('risk_level')} score={result.get('risk_score')} "
        f"ai={result.get('ai_analysis', {}).get('source', 'unknown')} "
        f"alert={result.get('telegram_alert', {}).get('sent')}"
    )
    print(f"keywords={result.get('keywords_found', [])}")
    print(f"text={result.get('transcribed_text', '')[:200]}")


def process_single_file(audio_path: Path) -> None:
    """Process a single audio file."""
    if not audio_path.exists():
        print(f"ERROR: File not found: {audio_path}")
        return

    if audio_path.suffix.lower() not in SUPPORTED_AUDIO:
        print(f"ERROR: Unsupported audio format: {audio_path.suffix}")
        print(f"Supported: {SUPPORTED_AUDIO}")
        return

    CONFIG.output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Processing: {audio_path}")
    print(f"Output dir: {CONFIG.output_dir}")

    try:
        # Transcribe
        print("Running STT...")
        text = speech_to_text(str(audio_path))

        # Analyze
        now = time.time()
        result = _build_result(audio_path, text, cooldown_ok=True)

        # Output
        _print_result(result)

        out_path = CONFIG.output_dir / f"result_{int(now)}.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✓ Saved: {out_path}")

    except Exception as exc:
        print(f"ERROR: {exc}")


def process_folder(folder_path: Path) -> None:
    """Watch folder and process audio files as they appear."""
    if not folder_path.exists():
        folder_path.mkdir(parents=True, exist_ok=True)

    CONFIG.output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Watching folder: {folder_path}")
    print(f"Output dir: {CONFIG.output_dir}")
    print("Press Ctrl+C to stop\n")

    seen_files: set[str] = set()
    last_alert_at = 0.0

    while True:
        try:
            # List audio files in folder
            files = sorted(
                [f for f in folder_path.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED_AUDIO],
                key=lambda p: p.stat().st_mtime
            )

            for audio_file in files:
                key = str(audio_file.resolve())
                if key in seen_files:
                    continue

                seen_files.add(key)
                print(f"\n[{time.strftime('%H:%M:%S')}] Processing: {audio_file.name}")

                try:
                    # Transcribe
                    text = speech_to_text(str(audio_file))

                    # Analyze
                    now = time.time()
                    cooldown_ok = (now - last_alert_at) >= CONFIG.alert_cooldown_seconds
                    result = _build_result(audio_file, text, cooldown_ok=cooldown_ok)

                    if result.get("telegram_alert", {}).get("sent"):
                        last_alert_at = now

                    # Output
                    _print_result(result)

                    out_path = CONFIG.output_dir / f"result_{int(now)}.json"
                    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

                except Exception as exc:
                    print(f"ERROR processing {audio_file.name}: {exc}")

            time.sleep(CONFIG.poll_seconds)

        except KeyboardInterrupt:
            print("\n\nStopped")
            return
        except Exception as exc:
            print(f"ERROR: {exc}")
            time.sleep(max(1.0, CONFIG.poll_seconds))


CONFIG = load_config()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Process audio files for scam detection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python realtime_file.py test.wav              # Process single file
  python realtime_file.py --watch ./audio_files # Watch folder for files
        """
    )
    parser.add_argument("path", help="Audio file or folder path")
    parser.add_argument("--watch", action="store_true", help="Watch folder mode")

    args = parser.parse_args()
    path = Path(args.path).resolve()

    if args.watch or (path.is_dir() and args.watch):
        process_folder(path)
    elif path.is_file():
        process_single_file(path)
    elif path.is_dir():
        process_folder(path)
    else:
        print(f"ERROR: Path not found: {args.path}")
        sys.exit(1)
