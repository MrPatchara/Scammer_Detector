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
2. Enable "Stereo Mix" on Windows to capture PC speaker audio:
   - Right-click speaker icon → "Open Sound settings"
   - Scroll down to "Advanced" → "App volume and device preferences"
   - Find "Stereo Mix (Realtek)" or similar and enable it
   - (Or use physical microphone if Stereo Mix not available)
3. Run: python realtime_pc_mic.py

The script auto-detects the best input device (Stereo Mix > Microphone > Default).
To manually select a device, list them first:

   python -c "import sounddevice as sd; print(sd.query_devices())"

Then set env var: REALTIME_MIC_DEVICE=<number>

Environment variables (recommended defaults):
    REALTIME_MIC_CHUNK_SECONDS=1.5          # Shorter = faster STT
    REALTIME_MIC_WINDOW_SECONDS=6
    REALTIME_MIC_OVERLAP_RATIO=0.5
    REALTIME_MIC_AI_GATE_SCORE=45
    REALTIME_MIC_AI_TRIGGERS=otp,โอนเงิน,เจ้าหน้าที่,บัญชี
    REALTIME_MIC_ALERT_COOLDOWN_SECONDS=15
    REALTIME_MIC_DEVICE=-1  (-1=auto-detect with priority: Stereo Mix > Physical Mic)
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


def _find_default_input_device() -> int:
    """Find the best input device for capturing audio on Windows.
    
    Priority:
    1. Device with 'Stereo Mix' (captures all PC audio)
    2. Physical microphone (Realtek, USB, etc.)
    3. Avoid Sound Mapper (device 0) - too low quality
    
    Returns device ID or raises error if none found.
    """
    devices = sd.query_devices()
    
    # Try to find Stereo Mix first
    for i, dev in enumerate(devices):
        if isinstance(dev, dict) and dev.get('max_input_channels', 0) > 0:
            name_lower = dev.get('name', '').lower()
            if 'stereo mix' in name_lower or 'what u hear' in name_lower:
                return i
    
    # Try physical microphones (Realtek, USB, etc.) - skip Sound Mapper (device 0)
    microphone_keywords = ['microphone', 'mic in', 'audio input', 'realtek', 'usb']
    for i, dev in enumerate(devices):
        if i == 0:  # Skip Sound Mapper - too low quality
            continue
        if isinstance(dev, dict) and dev.get('max_input_channels', 0) > 0:
            name_lower = dev.get('name', '').lower()
            if any(kw in name_lower for kw in microphone_keywords):
                return i
    
    # Last resort: any input device except 0
    for i, dev in enumerate(devices):
        if i == 0:  # Skip Sound Mapper
            continue
        if isinstance(dev, dict) and dev.get('max_input_channels', 0) > 0:
            return i
    
    raise RuntimeError(
        "No suitable input device found. "
        "Please enable 'Stereo Mix' in Windows Sound settings or connect a microphone."
    )


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

    def _denoise(self) -> np.ndarray:
        """Reduce noise by trimming low-energy silence and normalizing."""
        audio = self.audio_data.copy()
        
        # Normalize to [-1, 1]
        max_val = np.max(np.abs(audio))
        if max_val > 0:
            audio = audio / max_val
        
        # Trim silence from start/end (energy below threshold)
        threshold = 0.02
        energy = np.abs(audio)
        active = energy > threshold
        
        if np.any(active):
            active_indices = np.where(active)[0]
            start = max(0, active_indices[0] - int(0.1 * self.sample_rate))
            end = min(len(audio), active_indices[-1] + int(0.1 * self.sample_rate))
            audio = audio[start:end]
        
        # Normalize loudness
        if len(audio) > 0:
            rms = np.sqrt(np.mean(audio ** 2))
            if rms > 0:
                audio = audio / rms * 0.3  # Target RMS ~0.3
        
        return audio
    
    def to_wav_temp(self) -> Path:
        """Write cleaned chunk to temporary WAV file for processing."""
        tmp_dir = Path(tempfile.gettempdir())
        tmp_file = tmp_dir / f"mic_chunk_{int(time.time() * 1000)}_{self.chunk_id}.wav"
        
        # Clean audio
        audio_cleaned = self._denoise()
        
        # Convert float32 to int16
        audio_int16 = (audio_cleaned * 32767).astype(np.int16)
        
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
    chunk_seconds = max(1, _env_float("REALTIME_MIC_CHUNK_SECONDS", 1.5))
    window_seconds = max(chunk_seconds, _env_int("REALTIME_MIC_WINDOW_SECONDS", 6))
    overlap_ratio = _env_float("REALTIME_MIC_OVERLAP_RATIO", 0.5)
    overlap_ratio = max(0.0, min(overlap_ratio, 0.9))

    trigger_raw = os.getenv("REALTIME_MIC_AI_TRIGGERS", "otp,โอนเงิน,เจ้าหน้าที่,บัญชี")
    ai_triggers = {x.strip().lower() for x in trigger_raw.split(",") if x.strip()}

    device_id = _env_int("REALTIME_MIC_DEVICE", -1)
    if device_id < 0:
        device_id = _find_default_input_device()

    return RealtimeConfig(
        chunk_seconds=chunk_seconds,
        window_seconds=window_seconds,
        overlap_ratio=overlap_ratio,
        ai_gate_score=max(0, _env_int("REALTIME_MIC_AI_GATE_SCORE", 45)),
        ai_triggers=ai_triggers,
        alert_cooldown_seconds=max(1, _env_int("REALTIME_MIC_ALERT_COOLDOWN_SECONDS", 15)),
        device_id=device_id,
        sample_rate=max(8000, _env_int("REALTIME_MIC_SAMPLE_RATE", 16000)),
        keep_chunks=max(10, _env_int("REALTIME_MIC_KEEP_CHUNKS", 80)),
        output_dir=Path(os.getenv("REALTIME_MIC_OUTPUT_DIR", "mic_results")).resolve(),
    )


def _record_chunk(config: RealtimeConfig, duration: float, chunk_id: int) -> AudioChunk:
    """Record audio from microphone for specified duration."""
    print(f"[chunk {chunk_id}] recording {duration:.1f}s from device {config.device_id}...")
    try:
        audio = sd.rec(
            int(config.sample_rate * duration),
            samplerate=config.sample_rate,
            channels=1,
            device=config.device_id,
            dtype=np.float32,
            blocking=True,
        )
    except Exception as exc:
        print(f"ERROR recording from device {config.device_id}: {exc}")
        print("Trying fallback device...")
        # Try first available input device (skip device 0)
        devices = sd.query_devices()
        found = False
        for i, dev in enumerate(devices):
            if i == 0:  # Skip Sound Mapper
                continue
            if isinstance(dev, dict) and dev.get('max_input_channels', 0) > 0:
                try:
                    audio = sd.rec(
                        int(config.sample_rate * duration),
                        samplerate=config.sample_rate,
                        channels=1,
                        device=i,
                        dtype=np.float32,
                        blocking=True,
                    )
                    config.device_id = i
                    print(f"✓ Switched to device {i} ({dev['name']})")
                    found = True
                    break
                except Exception:
                    continue
        if not found:
            raise RuntimeError("No working input device found. Enable 'Stereo Mix' in Windows Sound settings.")
    
    return AudioChunk(
        audio_data=audio.flatten(),
        sample_rate=config.sample_rate,
        created_at=time.time(),
        chunk_id=chunk_id,
    )


def _merge_window_chunks(chunks: list[AudioChunk]) -> tuple[Path, list[str]]:
    """Merge multiple audio chunks into a single WAV file AND do per-chunk STT.
    
    Returns: (merged_wav_path, list_of_transcribed_texts)
    This allows us to do STT in parallel with recording (faster real-time response).
    """
    if not chunks:
        raise ValueError("No chunks to merge")

    # Transcribe each chunk immediately (non-blocking)
    texts: list[str] = []
    for chunk in chunks:
        tmp_file = chunk.to_wav_temp()
        try:
            text = speech_to_text(str(tmp_file))
            if text.strip():
                texts.append(text.strip())
        finally:
            tmp_file.unlink(missing_ok=True)

    # Also concatenate all audio data for potential re-analysis
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

    return out_file, texts


def _build_result(audio_file: Path, transcribed_texts: list[str], cooldown_ok: bool) -> dict[str, Any]:
    """Build analysis result from pre-transcribed text chunks."""
    # Combine all chunk texts
    text = " ".join(transcribed_texts)
    
    if not text.strip():
        return {
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
            "realtime": {
                "used_ai": False,
                "cooldown_ok": cooldown_ok,
                "source": "microphone",
                "chunks_merged": len(transcribed_texts),
            },
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
            "chunks_merged": len(transcribed_texts),
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
    print(f"device={CONFIG.device_id} ({sd.query_devices()[CONFIG.device_id]['name']})")
    print(f"sample_rate={CONFIG.sample_rate}Hz chunk={CONFIG.chunk_seconds}s")
    print(
        f"window={CONFIG.window_seconds}s overlap={CONFIG.overlap_ratio:.2f} "
        f"required_chunks={required_chunks} step={step_chunks}"
    )
    print(f"output_dir={CONFIG.output_dir}")

    try:
        # Verify microphone is accessible
        print("Testing microphone access...")
        test_audio = sd.rec(
            CONFIG.sample_rate,
            samplerate=CONFIG.sample_rate,
            channels=1,
            device=CONFIG.device_id,
            blocking=True,
        )
        print("✓ Microphone OK. Place phone on speaker and press Enter to start recording...")
        input()
    except Exception as exc:
        dev_name = sd.query_devices()[CONFIG.device_id]['name'] if CONFIG.device_id < len(sd.query_devices()) else "Unknown"
        print(f"Device {CONFIG.device_id} ({dev_name}) failed: {exc}")
        print("\n⚠️  PLEASE ENABLE 'STEREO MIX' IN WINDOWS:")
        print("  1. Right-click speaker icon (bottom-right)")
        print("  2. Click 'Open Sound settings'")
        print("  3. Scroll to 'Advanced' section")
        print("  4. Look for 'Stereo Mix' or 'What U Hear'")
        print("  5. Right-click → Enable (if disabled)")
        print("\n(Or connect a quality microphone, then restart script)")
        print("\nSearching for alternative devices...")
        
        found = False
        devices = sd.query_devices()
        for i, dev in enumerate(devices):
            if i == 0:  # Skip Sound Mapper
                continue
            if isinstance(dev, dict) and dev.get('max_input_channels', 0) > 0:
                try:
                    test_audio = sd.rec(
                        CONFIG.sample_rate,
                        samplerate=CONFIG.sample_rate,
                        channels=1,
                        device=i,
                        blocking=True,
                    )
                    CONFIG.device_id = i
                    print(f"✓ Found & using device {i} ({dev['name']})")
                    print("✓ Microphone OK. Place phone on speaker and press Enter to start recording...")
                    input()
                    found = True
                    break
                except Exception:
                    continue
        if not found:
            print("\n❌ ERROR: No working input device found.")
            print("You MUST enable 'Stereo Mix' in Windows Sound settings.")
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
            merged, texts = _merge_window_chunks(latest_window)
            now = time.time()
            cooldown_ok = (now - last_alert_at) >= CONFIG.alert_cooldown_seconds

            try:
                result = _build_result(merged, texts, cooldown_ok=cooldown_ok)
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
