"""Realtime scam monitoring on PC using chunk files from phone/audio source.

How it works:
1. A phone (or any source) drops audio chunks into a folder.
2. This script builds a sliding window from the latest chunks.
3. It runs STT + keyword/rule (+ optional AI gate) and sends Telegram alerts with cooldown.

Run:
    python realtime_pc.py

Environment variables (recommended defaults):
    REALTIME_CHUNK_SECONDS=2
    REALTIME_WINDOW_SECONDS=8
    REALTIME_OVERLAP_RATIO=0.5
    REALTIME_AI_GATE_SCORE=45
    REALTIME_AI_TRIGGERS=otp,โอนเงิน,เจ้าหน้าที่,บัญชี
    REALTIME_ALERT_COOLDOWN_SECONDS=15
    REALTIME_INPUT_DIR=./incoming_chunks
    REALTIME_POLL_SECONDS=0.5
    REALTIME_KEEP_CHUNKS=80
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.ai_analysis import ai_analyze
from app.alerts import send_telegram_alert, should_alert
from app.risk import calculate_risk, keyword_detect
from app.stt import speech_to_text


SUPPORTED_SUFFIXES = {".wav", ".mp3", ".m4a", ".ogg", ".webm", ".mp4"}


@dataclass
class RealtimeConfig:
    chunk_seconds: int
    window_seconds: int
    overlap_ratio: float
    ai_gate_score: int
    ai_triggers: set[str]
    alert_cooldown_seconds: int
    input_dir: Path
    poll_seconds: float
    keep_chunks: int


@dataclass
class ChunkMeta:
    path: Path
    created_at: float


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


def load_config() -> RealtimeConfig:
    chunk_seconds = max(1, _env_int("REALTIME_CHUNK_SECONDS", 2))
    window_seconds = max(chunk_seconds, _env_int("REALTIME_WINDOW_SECONDS", 8))
    overlap_ratio = _env_float("REALTIME_OVERLAP_RATIO", 0.5)
    overlap_ratio = max(0.0, min(overlap_ratio, 0.9))

    trigger_raw = os.getenv("REALTIME_AI_TRIGGERS", "otp,โอนเงิน,เจ้าหน้าที่,บัญชี")
    ai_triggers = {x.strip().lower() for x in trigger_raw.split(",") if x.strip()}

    return RealtimeConfig(
        chunk_seconds=chunk_seconds,
        window_seconds=window_seconds,
        overlap_ratio=overlap_ratio,
        ai_gate_score=max(0, _env_int("REALTIME_AI_GATE_SCORE", 45)),
        ai_triggers=ai_triggers,
        alert_cooldown_seconds=max(1, _env_int("REALTIME_ALERT_COOLDOWN_SECONDS", 15)),
        input_dir=Path(os.getenv("REALTIME_INPUT_DIR", "incoming_chunks")).resolve(),
        poll_seconds=max(0.1, _env_float("REALTIME_POLL_SECONDS", 0.5)),
        keep_chunks=max(10, _env_int("REALTIME_KEEP_CHUNKS", 80)),
    )


def _list_audio_files(input_dir: Path) -> list[Path]:
    files: list[Path] = []
    if not input_dir.exists():
        return files

    for item in input_dir.iterdir():
        if not item.is_file():
            continue
        if item.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        files.append(item)

    files.sort(key=lambda p: p.stat().st_mtime)
    return files


def _merge_window_to_wav(window_files: list[Path]) -> Path:
    tmp_dir = Path(tempfile.gettempdir())
    list_file = tmp_dir / f"realtime_concat_{int(time.time() * 1000)}.txt"
    out_file = tmp_dir / f"realtime_window_{int(time.time() * 1000)}.wav"

    with open(list_file, "w", encoding="utf-8") as f:
        for audio in window_files:
            escaped = str(audio).replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-ac",
        "1",
        "-ar",
        "16000",
        str(out_file),
    ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        if list_file.exists():
            list_file.unlink(missing_ok=True)

    return out_file


def _build_result(audio_file: Path, cooldown_ok: bool) -> dict[str, Any]:
    text = speech_to_text(str(audio_file))
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
        "audio_file": str(audio_file),
        "transcribed_text": text,
        "keywords_found": keywords,
        "risk_score": risk["score"],
        "risk_level": risk["risk_level"],
        "ai_analysis": ai_result,
        "realtime": {
            "used_ai": use_ai,
            "cooldown_ok": cooldown_ok,
        },
    }

    alert_info: dict[str, Any] = {"sent": False, "reason": "not_triggered"}
    if should_alert(result["risk_level"], int(result["risk_score"])):
        if cooldown_ok:
            alert_info = send_telegram_alert(result)
        else:
            alert_info = {"sent": False, "reason": "cooldown"}

    result["telegram_alert"] = alert_info
    return result


def _window_hash(files: list[Path]) -> str:
    key = "|".join(str(p) for p in files)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def _print_result(result: dict[str, Any]) -> None:
    print("=" * 80)
    print(
        f"risk={result.get('risk_level')} score={result.get('risk_score')} "
        f"ai={result.get('ai_analysis', {}).get('source', 'unknown')} "
        f"alert={result.get('telegram_alert', {}).get('sent')}"
    )
    print(f"keywords={result.get('keywords_found', [])}")
    print(f"text={result.get('transcribed_text', '')[:240]}")


def run_realtime_monitor() -> None:
    CONFIG.input_dir.mkdir(parents=True, exist_ok=True)

    window_chunks = deque(maxlen=CONFIG.keep_chunks)
    seen_files: set[str] = set()
    last_window_digest = ""
    last_alert_at = 0.0

    required_chunks = max(1, int(round(CONFIG.window_seconds / CONFIG.chunk_seconds)))
    step_chunks = max(1, int(round(required_chunks * (1.0 - CONFIG.overlap_ratio))))

    print("Realtime monitor started")
    print(f"input_dir={CONFIG.input_dir}")
    print(
        f"chunk={CONFIG.chunk_seconds}s window={CONFIG.window_seconds}s "
        f"overlap={CONFIG.overlap_ratio:.2f} required_chunks={required_chunks} step={step_chunks}"
    )

    while True:
        try:
            audio_files = _list_audio_files(CONFIG.input_dir)
            for f in audio_files:
                key = str(f.resolve())
                if key in seen_files:
                    continue
                seen_files.add(key)
                window_chunks.append(ChunkMeta(path=f, created_at=f.stat().st_mtime))

            if len(window_chunks) < required_chunks:
                time.sleep(CONFIG.poll_seconds)
                continue

            latest_window = [c.path for c in list(window_chunks)[-required_chunks:]]
            digest = _window_hash(latest_window)
            if digest == last_window_digest:
                time.sleep(CONFIG.poll_seconds)
                continue
            last_window_digest = digest

            merged = _merge_window_to_wav(latest_window)
            now = time.time()
            cooldown_ok = (now - last_alert_at) >= CONFIG.alert_cooldown_seconds

            try:
                result = _build_result(merged, cooldown_ok=cooldown_ok)
            finally:
                merged.unlink(missing_ok=True)

            if result.get("telegram_alert", {}).get("sent"):
                last_alert_at = now

            _print_result(result)

            out_path = CONFIG.input_dir / f"result_{int(now)}.json"
            out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

            # Move window forward based on step size
            for _ in range(step_chunks):
                if window_chunks:
                    window_chunks.popleft()

        except KeyboardInterrupt:
            print("Stopped")
            return
        except Exception as exc:
            print(f"realtime loop error: {exc}")
            time.sleep(max(1.0, CONFIG.poll_seconds))


CONFIG = load_config()


if __name__ == "__main__":
    run_realtime_monitor()
