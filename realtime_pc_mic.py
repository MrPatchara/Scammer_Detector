"""Realtime scam monitoring on PC using microphone input (phone speaker → PC mic).

This version captures audio directly from the PC's microphone, eliminating the need
for file transfer. Simply place the phone on speaker during a call and let the PC
microphone pick up the conversation.

How it works:
1. Records audio from PC microphone in chunks (2-3 seconds).
2. Builds a sliding window from buffered chunks.
3. Runs STT + keyword/rule (+ optional AI gate) and sends Telegram alerts.

Setup:
1. Install sounddevice: pip install sounddevice
2. In Windows: Set your default recording device to the mic picking up the speaker.
3. Run: python realtime_pc_mic.py

Environment variables (recommended defaults):
    REALTIME_MIC_CHUNK_SECONDS=2
    REALTIME_MIC_WINDOW_SECONDS=8
    REALTIME_MIC_OVERLAP_RATIO=0.5
    REALTIME_MIC_AI_GATE_SCORE=45
    REALTIME_MIC_AI_TRIGGERS=otp,โอนเงิน,เจ้าหน้าที่,บัญชี
    REALTIME_MIC_ALERT_COOLDOWN_SECONDS=15
    REALTIME_MIC_DEVICE=-1  (-1=default, or use: python -m sounddevice to list)
    REALTIME_MIC_SAMPLE_RATE=16000
    REALTIME_MIC_KEEP_CHUNKS=80
    REALTIME_MIC_OUTPUT_DIR=./mic_results
"""

from __future__ import annotations

import hashlib
import json
import os
import sounddevice as sd
import tempfile
import time
import wave
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.ai_analysis import ai_analyze
from app.alerts import send_telegram_alert, should_alert
from app.risk import calculate_risk, keyword_detect
from app.stt import speech_to_text


@dataclass
class RealtimeConfig:
    chunk_seconds: int
    window_seconds: int
    overlap_ratio: float
    ai_gate_score: int
    ai_triggers: set[str]
    alert_cooldown_seconds: int
    device_id: int
    sample_rate: int
    keep_chunks: int
    output_dir: Path


@dataclass
class AudioChunk:
    """Memory-resident audio chunk with metadata."""
    audio_data: np.ndarray  # PCM audio as numpy array
    sample_rate: int
    created_at: float
    chunk_id: int  # Sequential ID for ordering

    def to_wav_temp(self) -> Path:
        """Write chunk to temporary WAV file for processing."""
        tmp_dir = Path(tempfile.gettempdir())
        tmp_file = tmp_dir / f"mic_chunk_{int(time.time() * 1000)}_{self.chunk_id}.wav"
        
        # Convert float32 to int16
        audio_int16 = (self.audio_data * 32767).astype(np.int16)
        
        # Write WAV file using wave module
        with wave.open(str(tmp_file), 'wb') as wav_file:
            wav_file.setnchannels(1)  # mono
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(audio_int16.tobytes())
        
        return tmp_file


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
    chunk_seconds = max(1, _env_int("REALTIME_MIC_CHUNK_SECONDS", 2))
    window_seconds = max(chunk_seconds, _env_int("REALTIME_MIC_WINDOW_SECONDS", 8))
    overlap_ratio = _env_float("REALTIME_MIC_OVERLAP_RATIO", 0.5)
    overlap_ratio = max(0.0, min(overlap_ratio, 0.9))

    trigger_raw = os.getenv("REALTIME_MIC_AI_TRIGGERS", "otp,โอนเงิน,เจ้าหน้าที่,บัญชี")
    ai_triggers = {x.strip().lower() for x in trigger_raw.split(",") if x.strip()}

    return RealtimeConfig(
        chunk_seconds=chunk_seconds,
        window_seconds=window_seconds,
        overlap_ratio=overlap_ratio,
        ai_gate_score=max(0, _env_int("REALTIME_MIC_AI_GATE_SCORE", 45)),
        ai_triggers=ai_triggers,
        alert_cooldown_seconds=max(1, _env_int("REALTIME_MIC_ALERT_COOLDOWN_SECONDS", 15)),
        device_id=_env_int("REALTIME_MIC_DEVICE", -1),
        sample_rate=max(8000, _env_int("REALTIME_MIC_SAMPLE_RATE", 16000)),
        keep_chunks=max(10, _env_int("REALTIME_MIC_KEEP_CHUNKS", 80)),
        output_dir=Path(os.getenv("REALTIME_MIC_OUTPUT_DIR", "mic_results")).resolve(),
    )


def _record_chunk(config: RealtimeConfig, duration: float, chunk_id: int) -> AudioChunk:
    """Record audio from microphone for specified duration."""
    print(f"[chunk {chunk_id}] recording {duration:.1f}s from device {config.device_id}...")
    audio = sd.rec(
        int(config.sample_rate * duration),
        samplerate=config.sample_rate,
        channels=1,
        device=config.device_id,
        dtype=np.float32,
        blocking=True,
    )
    return AudioChunk(
        audio_data=audio.flatten(),
        sample_rate=config.sample_rate,
        created_at=time.time(),
        chunk_id=chunk_id,
    )


def _merge_window_chunks(chunks: list[AudioChunk]) -> Path:
    """Merge multiple audio chunks into a single WAV file."""
    if not chunks:
        raise ValueError("No chunks to merge")

    # Concatenate all audio data
    all_audio = np.concatenate([c.audio_data for c in chunks])
    sample_rate = chunks[0].sample_rate

    # Normalize to prevent clipping
    max_val = np.max(np.abs(all_audio))
    if max_val > 0:
        all_audio = all_audio / max_val * 0.95

    # Convert to int16 for WAV file
    audio_int16 = (all_audio * 32767).astype(np.int16)

    # Save to temporary WAV file using wave module
    tmp_dir = Path(tempfile.gettempdir())
    out_file = tmp_dir / f"mic_window_{int(time.time() * 1000)}.wav"
    
    with wave.open(str(out_file), 'wb') as wav_file:
        wav_file.setnchannels(1)  # mono
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(audio_int16.tobytes())

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
            "source": "microphone",
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


def _window_hash(chunks: list[AudioChunk]) -> str:
    """Hash window identity to detect duplicates."""
    ids = [str(c.chunk_id) for c in chunks]
    key = "|".join(ids)
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
    CONFIG.output_dir.mkdir(parents=True, exist_ok=True)

    window_chunks = deque(maxlen=CONFIG.keep_chunks)
    last_window_digest = ""
    last_alert_at = 0.0
    next_chunk_id = 0

    required_chunks = max(1, int(round(CONFIG.window_seconds / CONFIG.chunk_seconds)))
    step_chunks = max(1, int(round(required_chunks * (1.0 - CONFIG.overlap_ratio))))

    print("Realtime microphone monitor started")
    print(f"sample_rate={CONFIG.sample_rate}Hz chunk={CONFIG.chunk_seconds}s")
    print(
        f"window={CONFIG.window_seconds}s overlap={CONFIG.overlap_ratio:.2f} "
        f"required_chunks={required_chunks} step={step_chunks}"
    )
    print(f"output_dir={CONFIG.output_dir}")

    try:
        # Verify microphone is accessible
        print("Testing microphone access...")
        sd.rec(
            CONFIG.sample_rate,
            samplerate=CONFIG.sample_rate,
            channels=1,
            device=CONFIG.device_id,
            blocking=True,
        )
        print("✓ Microphone OK. Place phone on speaker and press Enter to start recording...")
        input()
    except Exception as exc:
        print(f"ERROR: Microphone access failed: {exc}")
        return

    while True:
        try:
            # Record a chunk
            chunk = _record_chunk(CONFIG, CONFIG.chunk_seconds, next_chunk_id)
            next_chunk_id += 1
            window_chunks.append(chunk)

            # Check if we have enough chunks to analyze
            if len(window_chunks) < required_chunks:
                continue

            # Build current window
            latest_window = list(window_chunks)[-required_chunks:]
            digest = _window_hash(latest_window)

            # Skip if this is the same window as before (no new audio)
            if digest == last_window_digest:
                continue

            last_window_digest = digest

            # Merge chunks and analyze
            merged = _merge_window_chunks(latest_window)
            now = time.time()
            cooldown_ok = (now - last_alert_at) >= CONFIG.alert_cooldown_seconds

            try:
                result = _build_result(merged, cooldown_ok=cooldown_ok)
            finally:
                merged.unlink(missing_ok=True)

            if result.get("telegram_alert", {}).get("sent"):
                last_alert_at = now

            _print_result(result)

            out_path = CONFIG.output_dir / f"result_{int(now)}.json"
            out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

            # Move window forward by step size
            for _ in range(step_chunks):
                if window_chunks:
                    window_chunks.popleft()

        except KeyboardInterrupt:
            print("\nStopped")
            return
        except Exception as exc:
            print(f"realtime loop error: {exc}")
            time.sleep(1.0)


CONFIG = load_config()


if __name__ == "__main__":
    run_realtime_monitor()
