"""Real-time scam monitoring service with SQLite persistence and enhanced Telegram alerts.

Run: python realtime_monitor_service.py

This is Phase 1 of the production monitoring system:
- Continuous mic/Stereo Mix capture
- Per-chunk STT processing
- SQLite event tracking
- Smart Telegram alerts (with full analysis results)
- Daily summary reports
- Cooldown management (no spam)

Environment variables:
    MONITOR_ENABLED=1                           # Enable/disable monitoring
    MONITOR_CHUNK_SECONDS=1.5
    MONITOR_WINDOW_SECONDS=6
    MONITOR_DEVICE=-1                           # Mic device (-1=auto)
    MONITOR_AI_GATE_SCORE=45
    MONITOR_AI_TRIGGERS=otp,โอนเงิน,เจ้าหน้าที่,บัญชี,ตำรวจ
    MONITOR_ALERT_COOLDOWN_SECONDS=30           # Min time between alerts
    MONITOR_OUTPUT_DIR=./monitor_results
    MONITOR_DB_PATH=./monitor_events.db
    MONITOR_DAILY_REPORT_HOUR=21                # 9 PM = hour 21
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sounddevice as sd
import tempfile
import time
import wave
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from app.ai_analysis import ai_analyze
from app.alerts import send_telegram_alert, should_alert
from app.risk import calculate_risk, keyword_detect
from app.stt import speech_to_text


@dataclass
class MonitorConfig:
    enabled: bool
    chunk_seconds: float
    window_seconds: int
    device_id: int
    sample_rate: int
    ai_gate_score: int
    ai_triggers: set[str]
    alert_cooldown_seconds: int
    output_dir: Path
    db_path: Path
    daily_report_hour: int


@dataclass
class AlertEvent:
    """Alert event record for SQLite."""
    timestamp: float
    datetime_str: str
    audio_file: str
    transcribed_text: str
    keywords_found: str  # comma-separated
    risk_level: str
    risk_score: int
    ai_source: str
    is_scam: bool
    telegram_sent: bool
    alert_message: str


class MonitorDatabase:
    """SQLite database for alert tracking."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS alert_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    datetime_str TEXT NOT NULL,
                    audio_file TEXT,
                    transcribed_text TEXT,
                    keywords_found TEXT,
                    risk_level TEXT,
                    risk_score INTEGER,
                    ai_source TEXT,
                    is_scam BOOLEAN,
                    telegram_sent BOOLEAN,
                    alert_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date TEXT PRIMARY KEY,
                    total_calls INTEGER DEFAULT 0,
                    high_risk_count INTEGER DEFAULT 0,
                    medium_risk_count INTEGER DEFAULT 0,
                    low_risk_count INTEGER DEFAULT 0,
                    alerts_sent INTEGER DEFAULT 0
                )
            """)
            conn.commit()

    def insert_alert(self, event: AlertEvent) -> int:
        """Insert alert event and return ID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO alert_events (
                    timestamp, datetime_str, audio_file, transcribed_text,
                    keywords_found, risk_level, risk_score, ai_source,
                    is_scam, telegram_sent, alert_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.timestamp,
                    event.datetime_str,
                    event.audio_file,
                    event.transcribed_text,
                    event.keywords_found,
                    event.risk_level,
                    event.risk_score,
                    event.ai_source,
                    event.is_scam,
                    event.telegram_sent,
                    event.alert_message,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_daily_stats(self, date: str) -> dict:
        """Get stats for a date (YYYY-MM-DD)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT * FROM daily_stats WHERE date = ?", (date,)
            )
            row = cursor.fetchone()
            if row:
                return {
                    "date": row[0],
                    "total_calls": row[1],
                    "high_risk_count": row[2],
                    "medium_risk_count": row[3],
                    "low_risk_count": row[4],
                    "alerts_sent": row[5],
                }
            return {
                "date": date,
                "total_calls": 0,
                "high_risk_count": 0,
                "medium_risk_count": 0,
                "low_risk_count": 0,
                "alerts_sent": 0,
            }

    def update_daily_stats(
        self, date: str, risk_level: str, alert_sent: bool
    ) -> None:
        """Update daily stats."""
        with sqlite3.connect(self.db_path) as conn:
            # Upsert
            conn.execute(
                """
                INSERT INTO daily_stats (date, total_calls, high_risk_count,
                    medium_risk_count, low_risk_count, alerts_sent)
                VALUES (?, 1, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    total_calls = total_calls + 1,
                    high_risk_count = high_risk_count + ?,
                    medium_risk_count = medium_risk_count + ?,
                    low_risk_count = low_risk_count + ?,
                    alerts_sent = alerts_sent + ?
                """,
                (
                    date,
                    1 if risk_level == "high" else 0,
                    1 if risk_level == "medium" else 0,
                    1 if risk_level == "low" else 0,
                    1 if alert_sent else 0,
                    1 if risk_level == "high" else 0,
                    1 if risk_level == "medium" else 0,
                    1 if risk_level == "low" else 0,
                    1 if alert_sent else 0,
                ),
            )
            conn.commit()

    def get_recent_alerts(self, hours: int = 24) -> list[dict]:
        """Get alerts from last N hours."""
        cutoff = time.time() - (hours * 3600)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT * FROM alert_events
                WHERE timestamp > ? AND is_scam = 1
                ORDER BY timestamp DESC
                LIMIT 10
                """,
                (cutoff,),
            )
            rows = cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "datetime": row[2],
                    "text": row[4][:100] if row[4] else "",
                    "risk_score": row[7],
                    "sent": row[10],
                }
                for row in rows
            ]


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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, str(default)).strip().lower()
    return raw in ("1", "true", "yes")


def load_config() -> MonitorConfig:
    trigger_raw = os.getenv(
        "MONITOR_AI_TRIGGERS", "otp,โอนเงิน,เจ้าหน้าที่,บัญชี,ตำรวจ"
    )
    ai_triggers = {x.strip().lower() for x in trigger_raw.split(",") if x.strip()}

    return MonitorConfig(
        enabled=_env_bool("MONITOR_ENABLED", True),
        chunk_seconds=max(1, _env_float("MONITOR_CHUNK_SECONDS", 1.5)),
        window_seconds=max(3, _env_int("MONITOR_WINDOW_SECONDS", 6)),
        device_id=_env_int("MONITOR_DEVICE", -1),
        sample_rate=_env_int("MONITOR_SAMPLE_RATE", 16000),
        ai_gate_score=max(0, _env_int("MONITOR_AI_GATE_SCORE", 45)),
        ai_triggers=ai_triggers,
        alert_cooldown_seconds=max(1, _env_int("MONITOR_ALERT_COOLDOWN_SECONDS", 30)),
        output_dir=Path(os.getenv("MONITOR_OUTPUT_DIR", "monitor_results")).resolve(),
        db_path=Path(os.getenv("MONITOR_DB_PATH", "monitor_events.db")).resolve(),
        daily_report_hour=_env_int("MONITOR_DAILY_REPORT_HOUR", 21),
    )


def _find_default_input_device() -> int:
    """Find best input device (Stereo Mix > Physical Mic)."""
    devices = sd.query_devices()

    # Try Stereo Mix first
    for i, dev in enumerate(devices):
        if isinstance(dev, dict) and dev.get("max_input_channels", 0) > 0:
            name_lower = dev.get("name", "").lower()
            if "stereo mix" in name_lower or "what u hear" in name_lower:
                return i

    # Try physical mics (skip device 0)
    microphone_keywords = ["microphone", "mic in", "audio input", "realtek", "usb"]
    for i, dev in enumerate(devices):
        if i == 0:
            continue
        if isinstance(dev, dict) and dev.get("max_input_channels", 0) > 0:
            name_lower = dev.get("name", "").lower()
            if any(kw in name_lower for kw in microphone_keywords):
                return i

    # Last resort
    for i, dev in enumerate(devices):
        if i == 0:
            continue
        if isinstance(dev, dict) and dev.get("max_input_channels", 0) > 0:
            return i

    raise RuntimeError("No input device found")


@dataclass
class AudioChunk:
    """Audio chunk with processing."""

    audio_data: np.ndarray
    sample_rate: int
    created_at: float
    chunk_id: int

    def _denoise(self) -> np.ndarray:
        """Clean audio: trim silence + normalize."""
        audio = self.audio_data.copy()

        max_val = np.max(np.abs(audio))
        if max_val > 0:
            audio = audio / max_val

        threshold = 0.02
        energy = np.abs(audio)
        active = energy > threshold

        if np.any(active):
            active_indices = np.where(active)[0]
            start = max(0, active_indices[0] - int(0.1 * self.sample_rate))
            end = min(len(audio), active_indices[-1] + int(0.1 * self.sample_rate))
            audio = audio[start:end]

        if len(audio) > 0:
            rms = np.sqrt(np.mean(audio**2))
            if rms > 0:
                audio = audio / rms * 0.3

        return audio

    def to_wav_temp(self) -> Path:
        """Write cleaned chunk to temp WAV file."""
        tmp_dir = Path(tempfile.gettempdir())
        tmp_file = tmp_dir / f"monitor_chunk_{int(time.time() * 1000)}_{self.chunk_id}.wav"

        audio_cleaned = self._denoise()
        audio_int16 = (audio_cleaned * 32767).astype(np.int16)

        with wave.open(str(tmp_file), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(audio_int16.tobytes())

        return tmp_file


def _record_chunk(config: MonitorConfig, duration: float, chunk_id: int) -> AudioChunk:
    """Record audio chunk from mic."""
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
        # Try fallback
        devices = sd.query_devices()
        for i, dev in enumerate(devices):
            if i == 0:
                continue
            if isinstance(dev, dict) and dev.get("max_input_channels", 0) > 0:
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
                    print(f"✓ Switched to device {i}")
                    break
                except Exception:
                    continue
        else:
            raise RuntimeError("No working device found")

    return AudioChunk(
        audio_data=audio.flatten(),
        sample_rate=config.sample_rate,
        created_at=time.time(),
        chunk_id=chunk_id,
    )


def _merge_and_transcribe(chunks: list[AudioChunk]) -> tuple[Path, list[str]]:
    """Merge chunks and do per-chunk STT."""
    if not chunks:
        raise ValueError("No chunks")

    texts: list[str] = []
    for chunk in chunks:
        tmp_file = chunk.to_wav_temp()
        try:
            text = speech_to_text(str(tmp_file))
            if text.strip():
                texts.append(text.strip())
        finally:
            tmp_file.unlink(missing_ok=True)

    # Also concatenate all audio
    all_audio = np.concatenate([c.audio_data for c in chunks])
    sample_rate = chunks[0].sample_rate

    max_val = np.max(np.abs(all_audio))
    if max_val > 0:
        all_audio = all_audio / max_val * 0.95

    audio_int16 = (all_audio * 32767).astype(np.int16)

    tmp_dir = Path(tempfile.gettempdir())
    out_file = tmp_dir / f"monitor_window_{int(time.time() * 1000)}.wav"

    with wave.open(str(out_file), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(audio_int16.tobytes())

    return out_file, texts


def _build_result(texts: list[str]) -> dict[str, Any]:
    """Analyze combined text."""
    text = " ".join(texts)

    if not text.strip():
        return {
            "transcribed_text": "",
            "keywords_found": [],
            "risk_score": 0,
            "risk_level": "low",
            "ai_analysis": {"source": "empty", "is_scam": False, "reason": "No speech"},
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
            "reason": "keyword gate",
            "source": "keyword_gate",
        }

    return {
        "transcribed_text": text,
        "keywords_found": keywords,
        "risk_score": risk["score"],
        "risk_level": risk["risk_level"],
        "ai_analysis": ai_result,
    }


def _format_telegram_message(result: dict[str, Any], audio_file: str) -> str:
    """Format result as readable Telegram message."""
    risk_level = result.get("risk_level", "low")
    risk_score = result.get("risk_score", 0)
    keywords = result.get("keywords_found", [])
    text = result.get("transcribed_text", "")[:150]
    ai_analysis = result.get("ai_analysis", {})
    is_scam = ai_analysis.get("is_scam", False)

    icon = "🔴" if risk_level == "high" else "🟡" if risk_level == "medium" else "🟢"

    msg = f"""
{icon} *SCAM ALERT DETECTED*

📊 ANALYSIS RESULTS
{'='*60}

📁 File: {Path(audio_file).name}

🎙️ Transcribed Text:
```
{text}...
```

🔍 Keywords Found:
{", ".join(keywords) if keywords else "None"}

⚠️  Risk Level: *{risk_level.upper()}*
📈 Risk Score: {risk_score}/100

🤖 AI Analysis:
  • Source: {ai_analysis.get('source', 'unknown')}
  • Is Scam: {'YES ⚠️' if is_scam else 'No ✓'}
  • Reason: {ai_analysis.get('reason', 'N/A')}

🕐 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

    return msg


def run_monitoring() -> None:
    """Main monitoring loop."""
    CONFIG.output_dir.mkdir(parents=True, exist_ok=True)
    db = MonitorDatabase(CONFIG.db_path)

    if CONFIG.device_id < 0:
        CONFIG.device_id = _find_default_input_device()

    print("\n" + "=" * 70)
    print("🚀 Real-time Scam Monitoring Service Started")
    print("=" * 70)
    print(f"📱 Device: {sd.query_devices()[CONFIG.device_id]['name']}")
    print(f"💾 Database: {CONFIG.db_path}")
    print(f"📁 Output: {CONFIG.output_dir}")
    print("Press Ctrl+C to stop\n")

    window_chunks = deque(maxlen=int(CONFIG.window_seconds / CONFIG.chunk_seconds))
    last_window_digest = ""
    last_alert_time = 0.0
    next_chunk_id = 0
    last_daily_report_date = datetime.now().strftime("%Y-%m-%d")

    required_chunks = max(1, int(CONFIG.window_seconds / CONFIG.chunk_seconds))
    step_chunks = max(1, int(required_chunks * 0.5))  # 50% overlap

    while True:
        try:
            # Check if should send daily report
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            if (
                today_str != last_daily_report_date
                and now.hour == CONFIG.daily_report_hour
            ):
                stats = db.get_daily_stats(last_daily_report_date)
                report = f"""
📊 *DAILY SCAM REPORT*
Date: {last_daily_report_date}

Total Calls: {stats['total_calls']}
🔴 High Risk: {stats['high_risk_count']}
🟡 Medium Risk: {stats['medium_risk_count']}
🟢 Low Risk: {stats['low_risk_count']}
📱 Alerts Sent: {stats['alerts_sent']}
"""
                try:
                    send_telegram_alert({"telegram_result": report})
                except:
                    pass
                last_daily_report_date = today_str

            # Record chunk
            chunk = _record_chunk(CONFIG, CONFIG.chunk_seconds, next_chunk_id)
            next_chunk_id += 1
            window_chunks.append(chunk)

            if len(window_chunks) < required_chunks:
                continue

            # Build window and check for new data
            latest_window = list(window_chunks)[-required_chunks:]
            digest = hashlib.sha1(
                "|".join(str(c.chunk_id) for c in latest_window).encode()
            ).hexdigest()

            if digest == last_window_digest:
                continue

            last_window_digest = digest

            # Process window
            merged, texts = _merge_and_transcribe(latest_window)
            now_time = time.time()

            try:
                result = _build_result(texts)
            finally:
                merged.unlink(missing_ok=True)

            # Check if should alert
            risk_level = result.get("risk_level", "low")
            risk_score = result.get("risk_score", 0)
            is_scam = result.get("ai_analysis", {}).get("is_scam", False)

            should_send_alert = is_scam and (
                now_time - last_alert_time
            ) >= CONFIG.alert_cooldown_seconds

            if should_send_alert:
                # Send Telegram alert
                msg = _format_telegram_message(result, str(merged))
                try:
                    send_telegram_alert({"telegram_result": msg})
                    telegram_sent = True
                except Exception as exc:
                    print(f"⚠️  Telegram error: {exc}")
                    telegram_sent = False

                last_alert_time = now_time

                # Log to database
                now_dt = datetime.now()
                event = AlertEvent(
                    timestamp=now_time,
                    datetime_str=now_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    audio_file=str(merged),
                    transcribed_text=result.get("transcribed_text", "")[:200],
                    keywords_found=", ".join(result.get("keywords_found", [])),
                    risk_level=risk_level,
                    risk_score=risk_score,
                    ai_source=result.get("ai_analysis", {}).get("source", "unknown"),
                    is_scam=is_scam,
                    telegram_sent=telegram_sent,
                    alert_message=msg[:300],
                )
                alert_id = db.insert_alert(event)
                db.update_daily_stats(now_dt.strftime("%Y-%m-%d"), risk_level, True)
                print(
                    f"[{now_dt.strftime('%H:%M:%S')}] 🚨 ALERT #{alert_id}: {risk_level.upper()} ({risk_score})"
                )
            else:
                # Log stats even if no alert
                date_str = datetime.now().strftime("%Y-%m-%d")
                db.update_daily_stats(date_str, risk_level, False)

            # Move window forward
            for _ in range(step_chunks):
                if window_chunks:
                    window_chunks.popleft()

        except KeyboardInterrupt:
            print("\n\n✋ Monitoring stopped")
            return
        except Exception as exc:
            print(f"⚠️  Error: {exc}")
            time.sleep(1.0)


CONFIG = load_config()

if __name__ == "__main__":
    try:
        if not CONFIG.enabled:
            print("Monitoring is disabled (MONITOR_ENABLED=0)")
            exit(1)

        run_monitoring()
    except Exception as exc:
        print(f"❌ Fatal error: {exc}")
        exit(1)
